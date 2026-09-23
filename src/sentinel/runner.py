"""Conservative flight sequencing and safety orchestration."""

from __future__ import annotations

import time
from dataclasses import replace
from threading import Event, RLock
from typing import Any, Callable, TypeVar

from .client import DroneClient, EmergencyAborted
from .envelope import EnvelopeViolation, FlightEnvelope
from .models import CommandRecord, Position, RunReport, VideoFrame


Result = TypeVar("Result")


class FlightTestRunner:
    """Run SENTINEL's single approved sequence through a :class:`DroneClient`.

    The runner is deliberately the only layer that decides when a movement is
    safe to transmit.  The client remains responsible for transport and
    protocol details; the runner owns ordering, preflight, and abort policy.
    """

    def __init__(
        self,
        client: DroneClient,
        mode: str,
        minimum_battery: int = 40,
        *,
        adapter: Any | None = None,
        route_before: Any | None = None,
        route_after: Any | None = None,
        route_snapshot_provider: Callable[[], Any] | None = None,
        progress: Callable[[str, str], None] | None = None,
        retain_video_stream: bool = False,
        video_only: bool = False,
        video_only_duration_s: float = 30.0,
        envelope: FlightEnvelope | None = None,
    ) -> None:
        self.client = client
        self.mode = mode
        self.minimum_battery = minimum_battery
        self.adapter = adapter
        self.route_before = route_before
        self.route_after = route_after
        self.route_snapshot_provider = route_snapshot_provider
        self.progress = progress
        self.retain_video_stream = retain_video_stream
        if video_only_duration_s < 0:
            raise ValueError("video_only_duration_s must not be negative")
        self.video_only = video_only
        self.video_only_duration_s = video_only_duration_s
        self.envelope = envelope if envelope is not None else FlightEnvelope()
        self.video_frame: VideoFrame | None = None
        self.history: list[CommandRecord] = []
        self.emergency_event = Event()
        self._emergency_error: Exception | None = None
        self._emergency_lock = RLock()
        self._step_executor = None

    @property
    def step_executor(self):
        """One lifecycle shared with a configured connected-session executor."""
        with self._emergency_lock:
            if self._step_executor is None:
                from .cube.step_executor import StepExecutor

                StepExecutor(self)
            return self._step_executor

    def _emit(self, event: str, message: str) -> None:
        if self.progress is not None:
            self.progress(event, message)

    @property
    def emergency_triggered(self) -> bool:
        return self.emergency_event.is_set()

    def _raise_if_emergency(self) -> None:
        if self.emergency_event.is_set():
            raise EmergencyAborted("operator emergency stop was triggered")

    def _capture_records(
        self,
        record_count: int,
        position: Position | None,
    ) -> None:
        records = self._client_records()
        for record in records[record_count:]:
            try:
                self.history.append(replace(record, position=position))
            except TypeError:
                # The documented client uses dataclass records.  Keeping this
                # fallback makes the boundary tolerant of simple test doubles.
                self.history.append(record)

    def _client_records(self) -> tuple[Any, ...]:
        """Read the protocol audit stream with compatibility for older fakes."""

        history = getattr(self.client, "command_history", None)
        if history is not None:
            return tuple(history)
        return tuple(getattr(self.client, "records", ()))

    def _call(
        self,
        action: Callable[[], Result],
        position: Position | None = None,
    ) -> Result:
        with self.step_executor.client_call():
            return self._call_locked(action, position)

    def _call_locked(
        self, action: Callable[[], Result], position: Position | None = None,
    ) -> Result:
        self._raise_if_emergency()
        if self.route_snapshot_provider is not None:
            self._assert_route_unchanged()
        record_count = len(self._client_records())
        try:
            result = action()
        except BaseException:
            self._capture_records(record_count, position)
            raise
        self._capture_records(record_count, position)
        # The operator hotkey can fire from a callback while the backend call
        # is in progress. Re-check after recording the action so the caller
        # cannot advance the flight sequence after the emergency latch wins.
        self._raise_if_emergency()
        return result

    @staticmethod
    def _is_ok(response: object) -> bool:
        return str(response).strip().lower() == "ok"

    @staticmethod
    def _require_height(height: object) -> int:
        if isinstance(height, bool) or not isinstance(height, int) or height < 0:
            raise RuntimeError(f"invalid height telemetry: {height!r}")
        return height

    def trigger_emergency(self) -> None:
        """Latch emergency state before sending the direct motor-stop command.

        Setting the event first closes the race where a normal command could
        start after the operator presses the emergency hotkey.  The emergency
        command is attempted once, and its failure is retained for reporting;
        no later flight command is permitted either way.
        """

        with self._emergency_lock:
            if self.emergency_event.is_set():
                return
            self.emergency_event.set()
            self.step_executor.note_emergency()
            record_count = len(self._client_records())
            try:
                self.client.emergency()
            except Exception as exc:  # pragma: no cover - hardware boundary
                self._emergency_error = exc
            self._capture_records(record_count, self.envelope.position)

    def move_checked(self, direction: str, distance_cm: int, *,
                     validate_vertical_telemetry: bool = True,
                     commit_position: bool = True) -> Position:
        """Validate a movement endpoint before invoking the client."""

        self._raise_if_emergency()
        candidate = self.envelope.validate_move(
            self.envelope.position, direction, distance_cm
        )
        self._emit("flight", f"sending {direction} {distance_cm} cm")
        response = self._call(
            lambda: self.client.move(direction, distance_cm), candidate
        )
        if not self._is_ok(response):
            raise RuntimeError(
                f"movement failed: {direction} {distance_cm}: {response}"
            )
        if commit_position:
            self.envelope.position = candidate
        if validate_vertical_telemetry and direction in {"up", "down"}:
            self._emit("flight", "validating altitude telemetry")
            measured_height = self._require_height(
                self._call(self.client.height_cm)
            )
            if not self.envelope.min_z_cm <= measured_height <= self.envelope.max_z_cm:
                raise EnvelopeViolation(
                    f"measured vertical position is outside the cube: "
                    f"{measured_height} cm"
                )
            candidate = replace(candidate, z_cm=measured_height)
            if commit_position:
                self.envelope.position = candidate
        return candidate

    def _assert_route_unchanged(self) -> None:
        if self.route_snapshot_provider is not None:
            current = self.route_snapshot_provider()
            if self.route_before is None:
                self.route_before = current
            self.route_after = current
        if (
            self.route_before is not None
            and self.route_after is not None
            and self.route_before != self.route_after
        ):
            raise RuntimeError(
                "internet default route changed during adapter selection"
            )

    def _enter_altitude_band(self) -> None:
        """Take 20 cm steps until measured height reaches the cube floor."""

        self._emit("flight", "validating initial altitude")
        height = self._require_height(self._call(self.client.height_cm))
        if height > self.envelope.max_z_cm:
            raise EnvelopeViolation(
                f"takeoff height exceeds upper face: {height} cm"
            )

        while height < self.envelope.min_z_cm:
            if height + 20 > self.envelope.max_z_cm:
                raise EnvelopeViolation("20 cm altitude step would cross upper face")

            self._emit("flight", f"sending altitude move up 20 cm from {height} cm")
            response = self._call(
                lambda: self.client.move("up", 20),
                Position(0, 0, height + 20),
            )
            if not self._is_ok(response):
                raise RuntimeError(f"altitude-entry move failed: {response}")

            self._emit("flight", "validating altitude telemetry")
            next_height = self._require_height(self._call(self.client.height_cm))
            if next_height > self.envelope.max_z_cm:
                raise EnvelopeViolation(
                    f"measured height exceeds upper face: {next_height} cm"
                )
            if next_height <= height:
                raise RuntimeError("altitude telemetry did not increase after up move")
            height = next_height

        self.envelope.position = Position(0, 0, height)

    def _preflight(self) -> None:
        self._emit("preflight", "connecting to Tello")
        self._call(self.client.connect)

        self._emit("preflight", "validating battery telemetry")
        battery = self._call(self.client.battery)
        if (
            isinstance(battery, bool)
            or not isinstance(battery, int)
            or not 0 <= battery <= 100
        ):
            raise RuntimeError(f"invalid battery telemetry: {battery!r}")
        if battery < self.minimum_battery:
            if self.video_only:
                self._emit(
                    "preflight",
                    f"battery is {battery}%: below flight minimum, "
                    "continuing in no-flight video-only mode",
                )
            else:
                raise RuntimeError(
                    f"battery is below the {self.minimum_battery}% minimum"
                )

        self._emit("preflight", "validating current altitude telemetry")
        height = self._require_height(self._call(self.client.height_cm))
        if height > self.envelope.max_z_cm:
            raise EnvelopeViolation(
                f"preflight height exceeds upper face: {height} cm"
            )

        # SDK 1.3 does not define an sdk? read command.  Command-mode
        # acknowledgement plus the documented battery?/height?/speed? and
        # stream commands below are the compatibility checks.
        self._emit("preflight", "validating SDK 1.3 command compatibility")

        self._emit("preflight", "setting command speed to 20 cm/s")
        response = self._call(lambda: self.client.set_speed(20))
        if not self._is_ok(response):
            raise RuntimeError(f"speed command failed: {response}")
        self._emit("preflight", "validating speed readback")
        if self._call(self.client.speed_cm_per_second) != 20:
            raise RuntimeError("speed readback did not match 20 cm/s")

        self._assert_route_unchanged()

        self._emit("preflight", "starting video stream")
        self._call(self.client.stream_on)
        try:
            self._emit("preflight", "validating decoded video frame")
            frame = self._call(lambda: self.client.read_frame(timeout=10))
            if frame is None or frame.width <= 0 or frame.height <= 0:
                raise RuntimeError("video frame dimensions are invalid")
            self.video_frame = frame
        finally:
            if not self.emergency_event.is_set() and not self.retain_video_stream:
                self._emit("preflight", "stopping video stream")
                self._call(self.client.stream_off)

    def _make_report(self, status: str, error: str | None = None) -> RunReport:
        return RunReport(
            status=status,
            mode=self.mode,
            backend=getattr(self.client, "backend_name", self.mode),
            commands=tuple(self.history),
            video_frame=self.video_frame,
            adapter=self.adapter,
            error=error,
        )

    def _record_video_only(self) -> None:
        self._emit(
            "video_only",
            f"recording video for {self.video_only_duration_s:.1f} seconds",
        )
        deadline = time.monotonic() + self.video_only_duration_s
        while True:
            if self.emergency_event.is_set():
                raise EmergencyAborted("emergency stop triggered during video capture")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.25, remaining))

    def _emergency_report_error(self, error: Exception) -> str:
        if self._emergency_error is None:
            return str(error)
        return f"{error}; emergency send failed: {self._emergency_error}"

    def _attempt_normal_landing(self) -> None:
        if self.emergency_event.is_set():
            return
        try:
            self.step_executor.land()
        except EmergencyAborted:
            # The emergency latch won the race; importantly, no land command
            # was transmitted after the latch was set.
            pass
        except Exception:
            pass

    def _fly_sequence(self) -> None:
        """The approved airborne sequence, between takeoff and hover/stop."""

        self._enter_altitude_band()
        for direction in ("forward", "right", "back", "left", "up", "down"):
            self.move_checked(direction, 50)

        self._emit("flight", "sending clockwise rotation 90 degrees")
        response = self._call(lambda: self.client.rotate("cw", 90))
        if not self._is_ok(response):
            raise RuntimeError(f"clockwise rotation failed: {response}")
        self._emit("flight", "sending counter-clockwise rotation 90 degrees")
        response = self._call(lambda: self.client.rotate("ccw", 90))
        if not self._is_ok(response):
            raise RuntimeError(f"counter-clockwise rotation failed: {response}")

    def run(self, *, confirm: bool) -> RunReport:
        if not confirm:
            raise RuntimeError("real flight requires explicit confirmation")

        report: RunReport | None = None
        self.history.clear()
        self.video_frame = None

        try:
            self._emit("run_started", "starting preflight")
            self.step_executor.start_legacy(video_only=self.video_only)
            if self.video_only:
                self._record_video_only()
                report = self._make_report("PASS")
                self._emit("run_complete", "video-only capture passed")
            else:
                self._fly_sequence()
                self._emit("flight", "sending hover/stop")
                try:
                    response = self._call(self.client.stop)
                except Exception as exc:  # noqa: BLE001 - re-raised unless it is the one known reply
                    if "unknown command" not in str(exc).lower():
                        raise
                    response = str(exc)
                if "unknown command" in str(response).lower():
                    # This Tello firmware has no `stop` (measured 2026-09-21); a Tello
                    # already hovers once a move has completed, so there is nothing to stop.
                    self._emit("flight", "firmware has no stop command; already hovering")
                elif not self._is_ok(response):
                    raise RuntimeError(f"hover/stop failed: {response}")
                self._emit("flight", "sending landing")
                outcome = self.step_executor.land()
                if outcome.state != "LANDED":
                    raise RuntimeError("landing failed")
                report = self._make_report("PASS")
                self._emit("run_complete", "flight sequence passed")
        except KeyboardInterrupt:
            self._attempt_normal_landing()
            report = self._make_report(
                "FAIL", "operator interrupted; controlled landing attempted"
            )
            self._emit("run_failed", report.error or "operator interrupted")
        except EmergencyAborted as exc:
            self.emergency_event.set()
            report = self._make_report(
                "EMERGENCY_ABORTED", self._emergency_report_error(exc)
            )
            self._emit("emergency_aborted", report.error or str(exc))
        except Exception as exc:
            emergency = self.emergency_event.is_set() or bool(
                getattr(self.client, "emergency_triggered", False)
            )
            if emergency:
                self.emergency_event.set()
                report = self._make_report(
                    "EMERGENCY_ABORTED", self._emergency_report_error(exc)
                )
                self._emit("emergency_aborted", report.error or str(exc))
            else:
                self._attempt_normal_landing()
                report = self._make_report("FAIL", str(exc))
                self._emit("run_failed", report.error or str(exc))
        finally:
            try:
                self.step_executor.close()
            except Exception as exc:  # pragma: no cover - hardware boundary
                if report is not None and report.status == "PASS":
                    report = self._make_report("FAIL", f"cleanup failed: {exc}")

        assert report is not None
        return report

    def abort_from_emergency(self) -> RunReport:
        """Return a terminal report for an emergency latched elsewhere."""

        self.emergency_event.set()
        return self._make_report(
            "EMERGENCY_ABORTED",
            self._emergency_report_error(
                EmergencyAborted("emergency stop triggered")
            ),
        )
