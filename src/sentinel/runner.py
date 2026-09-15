"""Conservative flight sequencing and safety orchestration."""

from __future__ import annotations

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
    ) -> None:
        self.client = client
        self.mode = mode
        self.minimum_battery = minimum_battery
        self.adapter = adapter
        self.route_before = route_before
        self.route_after = route_after
        self.route_snapshot_provider = route_snapshot_provider
        self.envelope = FlightEnvelope()
        self.video_frame: VideoFrame | None = None
        self.history: list[CommandRecord] = []
        self.emergency_event = Event()
        self._emergency_error: Exception | None = None
        self._emergency_lock = RLock()

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
            record_count = len(self._client_records())
            try:
                self.client.emergency()
            except Exception as exc:  # pragma: no cover - hardware boundary
                self._emergency_error = exc
            self._capture_records(record_count, self.envelope.position)

    def move_checked(self, direction: str, distance_cm: int) -> None:
        """Validate a movement endpoint before invoking the client."""

        self._raise_if_emergency()
        candidate = self.envelope.validate_move(
            self.envelope.position, direction, distance_cm
        )
        response = self._call(
            lambda: self.client.move(direction, distance_cm), candidate
        )
        if not self._is_ok(response):
            raise RuntimeError(
                f"movement failed: {direction} {distance_cm}: {response}"
            )
        self.envelope.position = candidate
        if direction in {"up", "down"}:
            measured_height = self._require_height(
                self._call(self.client.height_cm)
            )
            if not self.envelope.min_z_cm <= measured_height <= self.envelope.max_z_cm:
                raise EnvelopeViolation(
                    f"measured vertical position is outside the cube: "
                    f"{measured_height} cm"
                )
            self.envelope.position = replace(
                self.envelope.position, z_cm=measured_height
            )

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

        height = self._require_height(self._call(self.client.height_cm))
        if height > self.envelope.max_z_cm:
            raise EnvelopeViolation(
                f"takeoff height exceeds upper face: {height} cm"
            )

        while height < self.envelope.min_z_cm:
            if height + 20 > self.envelope.max_z_cm:
                raise EnvelopeViolation("20 cm altitude step would cross upper face")

            response = self._call(
                lambda: self.client.move("up", 20),
                Position(0, 0, height + 20),
            )
            if not self._is_ok(response):
                raise RuntimeError(f"altitude-entry move failed: {response}")

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
        self._call(self.client.connect)

        battery = self._call(self.client.battery)
        if (
            isinstance(battery, bool)
            or not isinstance(battery, int)
            or not 0 <= battery <= 100
        ):
            raise RuntimeError(f"invalid battery telemetry: {battery!r}")
        if battery < self.minimum_battery:
            raise RuntimeError(
                f"battery is below the {self.minimum_battery}% minimum"
            )

        height = self._require_height(self._call(self.client.height_cm))
        if height > self.envelope.max_z_cm:
            raise EnvelopeViolation(
                f"preflight height exceeds upper face: {height} cm"
            )

        sdk_version = self._call(self.client.sdk_version)
        if not str(sdk_version).strip():
            raise RuntimeError("SDK version telemetry is empty")

        response = self._call(lambda: self.client.set_speed(20))
        if not self._is_ok(response):
            raise RuntimeError(f"speed command failed: {response}")
        if self._call(self.client.speed_cm_per_second) != 20:
            raise RuntimeError("speed readback did not match 20 cm/s")

            self._assert_route_unchanged()

        self._call(self.client.stream_on)
        try:
            frame = self._call(lambda: self.client.read_frame(timeout=10))
            if frame is None or frame.width <= 0 or frame.height <= 0:
                raise RuntimeError("video frame dimensions are invalid")
            self.video_frame = frame
        finally:
            if not self.emergency_event.is_set():
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

    def _attempt_normal_landing(self) -> None:
        if self.emergency_event.is_set():
            return
        try:
            response = self._call(self.client.land)
            if not self._is_ok(response):
                self.history.append(
                    replace(self.history[-1], response=str(response))
                    if self.history
                    else response
                )
        except EmergencyAborted:
            # The emergency latch won the race; importantly, no land command
            # was transmitted after the latch was set.
            pass
        except Exception:
            pass

    def run(self, *, confirm: bool) -> RunReport:
        if not confirm:
            raise RuntimeError("real flight requires explicit confirmation")

        airborne = False
        report: RunReport | None = None
        self.history.clear()
        self.video_frame = None

        try:
            self._preflight()

            # Once the takeoff request is about to be transmitted, an error
            # can mean the drone is airborne even when no ACK arrived.
            airborne = True
            self._assert_route_unchanged()
            response = self._call(self.client.takeoff)
            if not self._is_ok(response):
                raise RuntimeError(f"takeoff failed: {response}")
            if self.emergency_event.is_set() or bool(
                getattr(self.client, "emergency_triggered", False)
            ):
                raise EmergencyAborted("emergency stop triggered during takeoff")
            airborne = True

            self._enter_altitude_band()
            for direction in ("forward", "right", "back", "left", "up", "down"):
                self.move_checked(direction, 50)

            response = self._call(lambda: self.client.rotate("cw", 90))
            if not self._is_ok(response):
                raise RuntimeError(f"clockwise rotation failed: {response}")
            response = self._call(lambda: self.client.rotate("ccw", 90))
            if not self._is_ok(response):
                raise RuntimeError(f"counter-clockwise rotation failed: {response}")
            response = self._call(self.client.stop)
            if not self._is_ok(response):
                raise RuntimeError(f"hover/stop failed: {response}")
            response = self._call(self.client.land)
            if not self._is_ok(response):
                raise RuntimeError(f"landing failed: {response}")
            airborne = False
            report = self._make_report("PASS")
        except KeyboardInterrupt:
            if airborne:
                self._attempt_normal_landing()
            report = self._make_report(
                "FAIL", "operator interrupted; controlled landing attempted"
            )
        except EmergencyAborted as exc:
            self.emergency_event.set()
            report = self._make_report("EMERGENCY_ABORTED", str(exc))
        except Exception as exc:
            emergency = self.emergency_event.is_set() or bool(
                getattr(self.client, "emergency_triggered", False)
            )
            if emergency:
                self.emergency_event.set()
                report = self._make_report("EMERGENCY_ABORTED", str(exc))
            else:
                if airborne:
                    self._attempt_normal_landing()
                report = self._make_report("FAIL", str(exc))
        finally:
            try:
                self.client.close()
            except Exception as exc:  # pragma: no cover - hardware boundary
                if report is not None and report.status == "PASS":
                    report = self._make_report("FAIL", f"cleanup failed: {exc}")

        assert report is not None
        return report

    def abort_from_emergency(self) -> RunReport:
        """Return a terminal report for an emergency latched elsewhere."""

        self.emergency_event.set()
        return self._make_report("EMERGENCY_ABORTED", "emergency stop triggered")
