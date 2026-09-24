"""Production live adapters: the real-Tello flight stack and the L0 probes.

Task A (room-live-adapters). ``scripts/room_session.py live`` uses these as
its defaults once every gate has passed. This module composes existing,
tested parts and defines no new safety rule:

- the client: ``sentinel.cli.build_real_client`` (``sentinel.network`` adapter
  selection over ``DjitellopyClient``);
- the runner: ``sentinel.runner.FlightTestRunner`` (its preflight, envelope and
  emergency latch) under ``sentinel.cube.step_executor.StepExecutor``;
- tag poses: the WP5 stack, bursts of ``sentinel.pose.stream.run_pose_stream``
  reports reduced by ``sentinel.pose.localise.localise``, exactly as
  ``CubeMissionRunner`` bursts them;
- frames: the survey live-detect capture path, a ``FrameRecorder`` PNG per
  capture, as ``CubeMissionRunner._capture`` saves them.

Nothing here opens a socket, reads a key or reaches the aircraft at import
time or when an object is built. ``TelloLink`` connects on first use; the
probes run only inside ``connectivity.check``; ``TelloFlightStackFactory``
builds its stack only when ``live_session`` calls it after every CLI gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from typing import Callable, Mapping

from sentinel.cube.checkpoint import CommandOutcome, HomeDefinition, PlannedCommand
from sentinel.cube.energy import EnergySample
from sentinel.cube.localization import LocalizationState, PoseFix
from sentinel.cube.recovery import SINGLE_TAG_MAX_HEADING_DEG, SINGLE_TAG_MAX_XY_CM, SINGLE_TAG_SOURCE, PoseReport
from sentinel.cube.mission import BURST_MAX_S, BURST_TARGET_REPORTS, CM_PER_UNIT, SETTLE_S, _StreamHeld
from sentinel.cube.site import MAX_LEG_CM
from sentinel.cube.step_executor import HeightTelemetry, HomeState, StepExecutor
from sentinel.envelope import FlightEnvelope
from sentinel.models import Position, Telemetry
from sentinel.network import TELLO_ENDPOINT
from sentinel.pose.localise import LocalisationFix, localise
from sentinel.pose.persistence import ensure_schema
from sentinel.pose.replay import FrameRecorder
from sentinel.pose.scoring import heading_delta_deg
from sentinel.pose.stream import run_pose_stream
from sentinel.pose.tagmap import TagMap
from sentinel.runner import FlightTestRunner

from .connectivity import ProbeResult
from .live_session import FlightStack
from .semantic import resolve_pose_id
from .site import RoomSite

#: The one Tello this build flies: the SDK's fixed access-point address.
TELLO_IP = TELLO_ENDPOINT
#: The key and model ``scripts/survey_run.py``'s ``gemini-openrouter`` detector
#: reads -- the perception provider a live session calls.
PROVIDER_KEY_ENV = "OPENROUTER_API_KEY"
PROVIDER_MODEL_ENV = "GEMINI_OPENROUTER_MODEL"
#: OpenRouter's key-information endpoint: answers only for a valid key and
#: costs nothing, so reachability never spends a paid call.
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
PROVIDER_TIMEOUT_S = 5.0
#: A sink operation ends this far inside its command's upper bound, which is
#: also the executor's own deadline for it: a completed settle or capture must
#: never race the watchdog that would otherwise pre-empt it.
DEADLINE_MARGIN_S = 0.25
#: Time reserved after a tag burst for the altimeter read that completes a fix.
HEIGHT_READ_SLACK_S = 1.0
#: refresh() runs inside SafetySupervisor.prepare(), which the watchdog's
#: monitor thread calls in a loop with a ~10 ms poll (room.supervisor.run_wait)
#: -- a burst here must return quickly even with no tags in view, so a tagless
#: refresh never blocks the watchdog anywhere near the full WP5 burst bound
#: (BURST_MAX_S) that measure_home and LOCALISE use, where thoroughness matters
#: more than latency.
REFRESH_BURST_S = 1.5
#: The airborne home is wherever the tags place the aircraft after takeoff,
#: not the declared pad mark: the flight envelope (validated on the home)
#: bounds it. The first live run measured home 43 cm from the mark.
HOME_TOLERANCE_CM = 150.0
#: A single-tag fix re-anchors only within these of the current estimate,
#: and carries at least these uncertainties (one tag cannot rule out a flip).
SINGLE_TAG_XY_UNCERTAINTY_CM = 15.0
SINGLE_TAG_HEADING_UNCERTAINTY_DEG = 5.0
#: Airborne home bursts: one retry when the first gave no two-tag fix but
#: showed the camera framing the tags (``_frames_the_tags``).
HOME_BURST_ATTEMPTS = 2
#: Listening time per home attempt. A hovering Tello that frames both tags
#: delivers its readings in under a second (recorded bursts: 12 reports in
#: 0.5-1.1 s, so localise's 8 samples take at most ~0.7 s); one that sees
#: nothing waited the full 12 s burst, twice, drifting blind (live 2026-09-24
#: 18:31 and 18:54: 33-34 s airborne before landing).
HOME_BURST_MAX_S = 4.0
#: A burst with at least this many two-tag reports is localised from those
#: alone (``localise``'s own minimum sample count).
MIN_JOINT_REPORTS = 8
#: A height reading further than this (or than the drift profile's
#: max_height_error_cm, if smaller) from the dead-reckoned height is ignored
#: (the Tello altimeter drifts); it bounds any climb chasing a drift.
HEIGHT_TRUST_CM = 40
#: A drone that never answers ``sdk?`` (DJITelloPy's own timeout text) is not
#: proof of a live SDK link; SENTINEL prefers to say so plainly.
SDK_ABORT_PREFIX = "aborting command"
SDK_UNKNOWN_PREFIX = "unknown command"


class LiveStackError(RuntimeError):
    """A live adapter refused: bad binding, bad telemetry or no measured pose."""


def _close_quietly(client) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001 - closing is best effort; the refusal already stands
        pass


# ---------------------------------------------------------------------------
# the one Tello connection
# ---------------------------------------------------------------------------


def build_tello_client(*, local_ip: str | None = None):
    """The real client, through the existing ``sentinel.cli`` network selection."""
    from sentinel.cli import build_real_client

    return build_real_client(SimpleNamespace(local_ip=local_ip, interface_name=None, interface_index=None,
                                             tello_ip=TELLO_IP, timeout=7.0, video_path=None))


def write_recording_status(run_dir: str | Path, *, error: str | None = None) -> dict:
    """Describe the closed raw stream without granting it decision authority."""
    path = Path(run_dir) / "flight.h264"
    digest = hashlib.sha256()
    size = 0
    if path.is_file():
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    status = {"status": "FAILED" if error else "READY" if size else "MISSING",
              "bytes": size, "sha256": digest.hexdigest() if size else None, "error": error}
    (Path(run_dir) / "recording.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


class _SessionClient:
    """One connected ``DroneClient`` for the whole session: ``connect`` is idempotent.

    DJITelloPy 2.5.0's receivers are process-global (``DjitellopyClient``
    refuses a second construction in one process), so the preflight probe,
    the runner's own preflight ``connect`` and the flight share this single
    connection. Every other call is the wrapped client's own.
    """

    def __init__(self, client) -> None:
        self._client = client
        self._connected = False

    def connect(self) -> None:
        if not self._connected:
            self._client.connect()
            self._connected = True

    def __getattr__(self, name):
        return getattr(self._client, name)


class TelloLink:
    """Builds and connects the real client on first use, then shares it."""

    def __init__(self, *, build_client: Callable[[], object] | None = None, local_ip: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        # Resolved at call time, so nothing is built until a probe or the stack asks.
        self._build_client = build_client or (lambda: build_tello_client(local_ip=local_ip))
        self._clock = clock
        self._lock = threading.Lock()
        self._client: _SessionClient | None = None
        self._closed = False

    def client(self) -> _SessionClient:
        with self._lock:
            if self._closed:
                raise LiveStackError("the Tello link is closed")
            if self._client is None:
                client = _SessionClient(self._build_client())
                try:
                    client.connect()
                except BaseException:
                    _close_quietly(client)
                    raise
                if self._closed:  # closed while connecting: never hand out a late connection
                    _close_quietly(client)
                    raise LiveStackError("the Tello link was closed while connecting")
                self._client = client
            return self._client

    def probe(self) -> ProbeResult:
        """Motors-off telemetry read: SDK command mode, battery, height, SDK version.

        Sends no takeoff, motor or RC command. The connection stays open for
        the flight stack; ``tello_drone_probe`` is the probe that closes it.
        """
        client = self.client()
        battery, height, sdk = client.battery(), client.height_cm(), client.sdk_version()
        if type(battery) is not int or not 0 <= battery <= 100:
            raise LiveStackError(f"invalid battery telemetry: {battery!r}")
        if type(height) is not int or height < 0:
            raise LiveStackError(f"invalid height telemetry: {height!r}")
        if not isinstance(sdk, str) or not sdk.strip():
            raise LiveStackError(f"invalid SDK version reply: {sdk!r}")
        sdk = sdk.strip()
        if sdk.lower().startswith(SDK_ABORT_PREFIX):
            # DJITelloPy never answered: this is its own timeout text
            # ("Aborting command 'sdk?'. Did not receive a response after N
            # seconds"), not a reply from the aircraft. Accepting it would
            # both fabricate a live SDK link and, because DJITelloPy never
            # clears its response queue, risk a late real reply being popped
            # as the answer to the next command on this shared connection.
            raise LiveStackError(f"the drone never answered sdk?: {sdk!r}")
        if sdk.lower().startswith(SDK_UNKNOWN_PREFIX):
            # SDK 1.3 does not define an sdk? read command; an explicit
            # "unknown command" reply still proves a live SDK link, so record
            # that plainly instead of echoing the raw protocol string.
            sdk = "unsupported"
        checked_at = self._clock()
        tello_ip = getattr(client, "tello_ip", TELLO_IP)
        return ProbeResult(f"tello {tello_ip} sdk={sdk} battery={battery}% height={height}cm", checked_at)

    def close(self) -> None:
        self._closed = True
        client = self._client
        if client is not None:
            _close_quietly(client)

    def recording_error(self) -> str | None:
        return (getattr(self._client, "video_recording_error", None)
                if self._client is not None else None)


def tello_drone_probe(*, build_client: Callable[[], object] | None = None, local_ip: str | None = None,
                      clock: Callable[[], float] = time.monotonic) -> ProbeResult:
    """The L0 drone probe: fresh telemetry, no motor command, connection closed."""
    link = TelloLink(build_client=build_client, local_ip=local_ip, clock=clock)
    try:
        return link.probe()
    finally:
        link.close()


# ---------------------------------------------------------------------------
# provider reachability
# ---------------------------------------------------------------------------


def provider_environ() -> Mapping[str, str]:
    """The process environment, filled from the nearest ``.env`` (explicit values win)."""
    from sentinel.env_file import load_env_file

    load_env_file()
    return os.environ


def _http_get(url: str, *, headers: Mapping[str, str], timeout: float):
    import httpx

    return httpx.get(url, headers=dict(headers), timeout=timeout)


def openrouter_provider_probe(*, environ: Mapping[str, str] | None = None, http_get=None,
                              clock: Callable[[], float] = time.monotonic) -> ProbeResult:
    """Reachability: OpenRouter answers and accepts the configured key.

    Refuses, naming the variable, when the key or the detector model is not
    set. The key never appears in a message or the returned identity.
    """
    settings = provider_environ() if environ is None else environ
    key, model = settings.get(PROVIDER_KEY_ENV), settings.get(PROVIDER_MODEL_ENV)
    if not key:
        raise LiveStackError(f"provider reachability needs {PROVIDER_KEY_ENV} to be set")
    if not model:
        raise LiveStackError(f"provider reachability needs {PROVIDER_MODEL_ENV} to be set")
    response = (http_get or _http_get)(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key}"},
                                       timeout=PROVIDER_TIMEOUT_S)
    status = getattr(response, "status_code", None)
    if status != 200:
        raise LiveStackError(f"OpenRouter did not accept {PROVIDER_KEY_ENV}: HTTP {status}")
    return ProbeResult(f"openrouter:{model}", clock())


# ---------------------------------------------------------------------------
# L3 defaults: the live Jev judge and the live detector
# ---------------------------------------------------------------------------

#: OpenRouter root: JevJudge appends ``/api/alpha/decisions`` itself (the
#: proposer factory and ``room_jev_check.py`` use the same root).
JEV_BASE_URL = "https://openrouter.ai"
#: Below the controller's provider wait (``live_session`` passes 10 s), so a
#: slow reply fails as a provider error instead of preempting the turn.
JEV_TIMEOUT_S = 9.0


class IdentifiedJudge:
    """A judgment model plus the ``.identity`` the CLI records before flight."""

    def __init__(self, judge, identity: str) -> None:
        self._judge, self.identity = judge, identity

    def choice(self, state, instruction, **kwargs):
        return self._judge.choice(state, instruction, **kwargs)


def default_jev(*, environ: Mapping[str, str] | None = None) -> IdentifiedJudge:
    """The live Jev judge (``sentinel.judgment.JevJudge`` over OpenRouter).

    Built only by ``room_session.py live`` after every gate and a ready
    preflight; building it makes no call. Refuses, naming the variable,
    without ``OPENROUTER_API_KEY``. The model is pinned to the proposer's
    ``JEV_PROPOSER_MODEL``: JevJudge's default alias is rejected by OpenRouter."""
    from sentinel.coordinator.proposer_factory import JEV_PROPOSER_MODEL
    from sentinel.judgment import JevJudge

    settings = provider_environ() if environ is None else environ
    if not settings.get(PROVIDER_KEY_ENV):
        raise LiveStackError(f"the live Jev judge needs {PROVIDER_KEY_ENV} to be set")
    judge = JevJudge(api_key_env=PROVIDER_KEY_ENV, base_url=JEV_BASE_URL, timeout_s=JEV_TIMEOUT_S,
                     model=JEV_PROPOSER_MODEL)
    return IdentifiedJudge(judge, f"jev:{judge._model}")


def default_detector(*, environ: Mapping[str, str] | None = None):
    """The live detector: ``scripts.survey_run.build_provider_detector``'s
    ``gemini-openrouter`` (the one sanctioned construction path), with the
    ``.identity`` the CLI records. Building it makes no call."""
    from scripts.survey_run import build_provider_detector

    settings = provider_environ() if environ is None else environ
    try:
        detector = build_provider_detector("gemini-openrouter", environ=settings)
    except RuntimeError as exc:
        raise LiveStackError(str(exc)) from exc
    detector.identity = f"gemini-openrouter:{detector.model}"
    return detector


# ---------------------------------------------------------------------------
# bindings: tag map and commanded envelope
# ---------------------------------------------------------------------------


def load_bound_tag_map(path: str | Path, *, expected_sha256: str) -> TagMap:
    """The tag map whose file bytes hash to the manifest's ``tag_map_sha256``.

    Task B item 4: the hash of the LF-normalized bytes (the git blob content,
    what ``canningst-site.json`` binds) is accepted as well as the raw
    bytes, so a Windows checkout's CRLF working copy of the same committed
    map still binds. Any other content change still refuses."""
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    normalized = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    if expected_sha256 not in (digest, normalized):
        raise LiveStackError(f"tag map {str(path)!r} (sha256 {digest}) is not the manifest's tag_map_sha256")
    try:
        return TagMap(json.loads(raw.decode("utf-8")))
    except (ValueError, KeyError, TypeError) as exc:
        raise LiveStackError(f"tag map {str(path)!r} is invalid: {exc}") from exc


def mapped_heading_tag_ids(tag_map: TagMap | None) -> tuple[tuple[float, tuple[int, ...]], ...]:
    """The headings (room convention: degrees CCW from +x) at which two or
    more mapped tags face the camera, for the recovery policy's yaw scan.

    A wall tag's ``yaw_deg`` is the direction it faces; the aircraft sees it
    by looking the opposite way. Headings with fewer than two tags cannot
    give a joint-tag fix and are left out; no tag map means no scan."""
    if tag_map is None:
        return ()
    by_heading: dict[float, set[int]] = {}
    for tag in tag_map.doc.get("tags", ()):
        heading = float((round(float(tag["yaw_deg"])) + 180) % 360)
        by_heading.setdefault(heading, set()).add(int(tag["tag_id"]))
    return tuple(sorted((heading, tuple(sorted(tags))) for heading, tags in by_heading.items() if len(tags) >= 2))


def site_envelope(site: RoomSite) -> FlightEnvelope:
    """The runner's commanded envelope: the site's semantic envelope volume.

    ``RoomSite.validate`` already requires that volume to be a declared,
    axis-aligned free-space rectangle, so its bounding box is the volume.
    """
    frame = site.semantic_frame
    if frame is None:
        raise LiveStackError("the live flight stack needs a site with a semantic frame: its envelope "
                             "volume bounds every commanded position")
    # The declared flight volume bounds where the aircraft may be (the
    # observation volume only bounds where poses sit): a pad outside the
    # observation box, as flown on 2026-09-24 at y 240, is still flyable.
    volume = next((v for v in site.free_space_volumes if v.volume_id == "flight"),
                  next(v for v in site.free_space_volumes if v.volume_id == frame.envelope_volume_id))
    xs = [x for x, _ in volume.polygon_xy_cm]
    ys = [y for _, y in volume.polygon_xy_cm]
    low_z, high_z = volume.z_interval_cm
    return FlightEnvelope(min_x_cm=math.ceil(min(xs)), max_x_cm=math.floor(max(xs)),
                          min_y_cm=math.ceil(min(ys)), max_y_cm=math.floor(max(ys)),
                          min_z_cm=math.ceil(low_z), max_z_cm=math.floor(high_z), max_move_cm=MAX_LEG_CM)


# ---------------------------------------------------------------------------
# tag poses
# ---------------------------------------------------------------------------


def wp5_pose_burst(client, tag_map: TagMap, conn: sqlite3.Connection, *, run_id: str,
                   duration_s: float) -> list:
    """One stationary burst of LIVE pose reports, as ``CubeMissionRunner._live_burst``.

    The flight keeps one video stream open (``_StreamHeld``); every report is
    persisted as a ``DRONE_POSE_REPORTED`` row.
    """
    count = {"n": 0}

    def on_report(_report) -> None:
        count["n"] += 1

    return run_pose_stream(_StreamHeld(client), tag_map, conn, run_id=run_id, max_frames=None,
                           duration_s=duration_s, source_kind="LIVE",
                           on_frame=lambda _frame: count["n"] < BURST_TARGET_REPORTS, on_report=on_report)


#: ``_TelloStack._tag_fix``'s answer when the video reader is already busy.
_BUSY = object()


def _frames_the_tags(burst: list) -> bool:
    """Whether a burst shows the camera framing the tags, not a blind drone.

    One report that saw two tags, or localise's own sample count of reports,
    is framing. A stray frame is not: the blind 2026-09-24 18:54 hover
    recorded exactly one single-tag report, and retrying on it only doubled
    the time airborne before landing.
    """
    return (len(burst) >= MIN_JOINT_REPORTS
            or any(len({obs.tag_id for obs in report.tag_observations}) >= 2 for report in burst))


@dataclass(frozen=True)
class _TagFix:
    x_cm: int
    y_cm: int
    heading_deg: float
    measured_at: float
    xy_uncertainty_cm: float
    heading_uncertainty_deg: float
    tag_ids: tuple[int, ...]
    #: Every mapped tag seen in the burst, including single-tag reports.
    seen_tag_ids: tuple[int, ...] = ()

    def pose_fix(self, z_cm: int) -> PoseFix:
        return PoseFix(Position(self.x_cm, self.y_cm, z_cm), self.heading_deg, self.measured_at,
                       self.xy_uncertainty_cm, self.heading_uncertainty_deg, self.tag_ids)

    def single_tag_fix(self, z_cm: int) -> PoseFix:
        return PoseFix(Position(self.x_cm, self.y_cm, z_cm), self.heading_deg, self.measured_at,
                       self.xy_uncertainty_cm, self.heading_uncertainty_deg, self.seen_tag_ids,
                       source="mapped_single_tag")


# ---------------------------------------------------------------------------
# the flight stack
# ---------------------------------------------------------------------------


class _TelloStack:
    """The measured-home, command-sink, refresh and emergency callbacks of one flight."""

    def __init__(self, *, client, runner: FlightTestRunner, tracker, clock, site: RoomSite, burst,
                 recorder: FrameRecorder, home_settle_s: float) -> None:
        self.client, self.runner, self.tracker, self.clock = client, runner, tracker, clock
        self._burst, self._recorder, self._home_settle_s = burst, recorder, home_settle_s
        self.capture_records: dict[str, tuple[Path, str, float]] = {}
        self._pad_z_cm = round(site.home.nominal_pose.z_cm)
        self._burst_lock = threading.Lock()
        self._climb_applied = False
        self._looking_away = False
        self._last_battery: int | None = None
        tag_headings = tuple(pose.yaw_deg for pose_id, tags in site.pose_tag_visibility
                             if len(tags) >= 2
                             for pose in (resolve_pose_id(site, pose_id),) if pose is not None)
        self._tag_bearing_headings = tag_headings or (site.home.heading_deg,)
        self._last_height: int | None = None
        self._site = site
        self.executor: StepExecutor | None = None
        #: Set when the session's supervisor gets a recovery adapter (mapped
        #: tag headings exist): refresh then leaves RECOVERY_REQUIRED to it.
        self.defer_recovery_fixes = False

    # -- measurements -------------------------------------------------------
    def _height_cm(self) -> int:
        """Altimeter height (``height?``) in the site frame, sanity-checked (``_trusted_height``)."""
        return self._trusted_height(
            lambda: self.runner._require_height(self.runner._call(self.client.height_cm)) + self._pad_z_cm)

    def _passive_height_cm(self) -> int:
        """State-stream height in the site frame, sanity-checked; issues no SDK command."""
        return self._trusted_height(self._read_passive_height_cm)

    def _trusted_height(self, read) -> int:
        """A height the flight can act on, never a reason to abort it.

        The Tello altimeter reads 30-40 cm low, drifts and sometimes fails.
        Once the tracker has a height, a reading more than HEIGHT_TRUST_CM
        from the height dead-reckoned from commanded moves is ignored:
        otherwise every checkpoint would climb to chase a drifting reading.
        A failed read falls back to the last trusted height, then to the
        home hover height. The result is clamped to the flight envelope.
        """
        try:
            raw = read()
        except Exception:
            raw = None
        # Only an airborne, climb-propagated tracker height is a reference:
        # on the pad (or before the takeoff climb is applied) it is not.
        expected = (round(self.tracker.health.position.z_cm)
                    if self._climb_applied and getattr(self.tracker, "initialized", False) else None)
        # Never beyond the tracker's own height gate: a fix whose height jumps
        # more than max_height_error_cm latches LOCALIZATION LOST (and lands).
        profile = getattr(self.tracker, "profile", None)
        trust_cm = min(HEIGHT_TRUST_CM, getattr(profile, "max_height_error_cm", HEIGHT_TRUST_CM))
        if type(raw) is int and (expected is None or abs(raw - expected) <= trust_cm):
            height = raw
        elif expected is not None:
            height = expected
        elif self._last_height is not None:
            height = self._last_height
        else:
            route = next((r for r in self._site.routes if r.route_id == self._site.home.route_id), None)
            height = round(route.checkpoints[-1].z_cm) if route is not None else self._pad_z_cm
        envelope = self.runner.envelope
        height = min(max(height, envelope.min_z_cm), envelope.max_z_cm)
        self._last_height = height
        return height

    def _read_passive_height_cm(self) -> int:
        """Airborne altitude from the live state stream, issuing no SDK command.

        StepExecutor's documented ``measure_home`` contract is that it "must
        return data without publishing state or issuing SDK commands"; the
        explicit ``height?`` read in ``_height_cm`` violates that, so
        ``measure_home`` uses this passive read instead (the same style
        ``battery`` already reads in, and never through the runner's SDK call
        boundary, since no command is sent).
        """
        height = self.client.height_cm_from_state()
        if isinstance(height, bool) or not isinstance(height, int) or height < 0:
            raise LiveStackError(f"invalid height telemetry: {height!r}")
        return height + self._pad_z_cm

    def _tag_fix(self, duration_s: float, *, blocking: bool = True) -> _TagFix | None | object:
        """A tag fix from one burst; with ``blocking`` False, ``_BUSY`` when
        another burst (a recovery burst) already holds the video reader."""
        burst = self._read_burst(duration_s, blocking=blocking)
        return burst if burst is _BUSY else self._fix_from_burst(burst)

    def _read_burst(self, duration_s: float, *, blocking: bool = True) -> list | object:
        """One burst's reports; ``_BUSY`` when not ``blocking`` and the reader is held."""
        if not self._burst_lock.acquire(blocking=blocking):
            return _BUSY
        try:  # one video reader at a time
            return list(self._burst(self.client, duration_s=duration_s))
        finally:
            self._burst_lock.release()

    def _fix_from_burst(self, burst: list) -> _TagFix | None:
        """The tag fix one burst supports, or None; reads no stream."""
        # LocalizationTracker.accept_fix's mapped_joint_tags gate requires at
        # least two distinct tag IDs as evidence that the fix was *jointly*
        # triangulated, not merely that different single tags drifted across
        # the burst. Only a report that itself saw two or more mapped tags is
        # joint-tag evidence; tag_ids is the union over those reports alone.
        joint_reports = [report for report in burst if len({obs.tag_id for obs in report.tag_observations}) >= 2]
        # Live 2026-09-24 18:26: 2 m from the tags and 40 cm off-centre, 4 of
        # 12 frames saw one tag; those single-tag solves scattered 10-22 cm and
        # pushed the burst's spread to 16 cm, so the home was refused twice
        # although its 8 two-tag reports agreed within 3 cm. With enough
        # two-tag reports, the fix is theirs alone.
        used = joint_reports if len(joint_reports) >= MIN_JOINT_REPORTS else burst
        result = localise(used)
        if not isinstance(result, LocalisationFix):
            return None
        measured_at = self.clock()
        deltas = sorted(abs(heading_delta_deg(report.heading_deg, result.heading_deg))
                        for report in used if report.heading_deg is not None)
        if not deltas:
            return None
        tag_ids = tuple(sorted({obs.tag_id for report in joint_reports for obs in report.tag_observations}))
        seen = tuple(sorted({obs.tag_id for report in burst for obs in report.tag_observations}))
        return _TagFix(round(result.x_units * CM_PER_UNIT), round(result.y_units * CM_PER_UNIT),
                       result.heading_deg, measured_at, result.spread_p95_cm, deltas[int(0.95 * (len(deltas) - 1))],
                       tag_ids, seen)

    @staticmethod
    def _within(upper_bound_s: float) -> float:
        return max(0.0, upper_bound_s - DEADLINE_MARGIN_S)

    def _dead_reckon(self, command: PlannedCommand) -> None:
        """Propagate an SDK-acknowledged motion into the session's tracker.

        Acknowledged motion is a physical fact (the executor commits it even
        when pre-empted); without it the next tag fix would read as a jump. A
        LOST tracker refuses propagation and stays LOST for the supervisor.
        """
        try:
            self.tracker.note_command(command, self.clock())
        except ValueError:
            pass

    def _propagate_takeoff_climb(self) -> None:
        """Before the first airborne fix, dead-reckon the takeoff the tracker never saw.

        The tracker was anchored on the pad; the measured home height minus
        that anchored height is the vertical climb, so the first airborne fix
        is judged against where the aircraft went rather than as a jump.
        """
        home = getattr(self.executor, "home", None)
        if self._climb_applied or home is None:
            return
        self._climb_applied = True
        climb = home.pose.z_cm - self.tracker.health.position.z_cm
        if abs(climb) < 1:
            return
        self._dead_reckon(PlannedCommand.from_mapping(
            "MOVE", {"direction": "up" if climb > 0 else "down", "distance_cm": abs(climb), "phase": "takeoff"},
            upper_bound_s=0.0, interruptible=False))

    # -- FlightStack callbacks ----------------------------------------------
    def refresh(self):
        """Tag-pose burst into the tracker, then a timed battery sample.

        A burst with no reliable fix is recorded as an estimator failure; no
        pose is ever assumed. The burst is bounded well below BURST_MAX_S
        (see REFRESH_BURST_S) because this runs inside the safety watchdog's
        polling loop: a tagless refresh must not stall it. A failure while
        the last dispatched command looked away from the canonical heading is
        recorded as an intentional look, so it never counts as an estimator
        failure.
        """
        # Non-blocking: while a recovery burst holds the video reader, the
        # watchdog's refresh must neither starve it nor count the skipped
        # burst as an estimator failure; the tracker keeps propagating.
        fix = self._tag_fix(REFRESH_BURST_S, blocking=False)
        if fix is _BUSY:
            pass
        elif (fix is not None and self.defer_recovery_fixes
              and self.tracker.health.state == LocalizationState.RECOVERY_REQUIRED):
            # Task B2: a pose that needs recovery is re-anchored by the
            # supervisor's recovery policy (its audited POSE_REANCHORED batch
            # and invalidations), never silently by the watchdog's refresh.
            # A refresh fix here superseded the policy's own burst and left
            # its episode unfinished-but-closed, so the *next* recovery of
            # the flight was refused as exhausted and landed in place.
            pass
        elif fix is None:
            # A tagless burst is not a failure: the tracker coasts and its
            # uncertainty keeps growing with time and motion. Positioning is
            # demanded where it matters -- the supervisor admits a translation
            # (a new zone) only within the forecast uncertainty limit, and
            # recovers first otherwise; camera/fix age limits still apply.
            self.tracker.note_camera(self.clock(), estimator_failed=False,
                                     intentional_look_away=self._looking_away)
        else:
            # Climb first, so the height check compares against the airborne home.
            self._propagate_takeoff_climb()
            z_cm = self._height_cm()
            if len(fix.tag_ids) >= 2 or not self.tracker.initialized:
                self.tracker.accept_fix(fix.pose_fix(z_cm))
            elif not self.tracker.accept_consistent_fix(
                    fix.single_tag_fix(z_cm), max_xy_cm=SINGLE_TAG_MAX_XY_CM,
                    max_heading_deg=SINGLE_TAG_MAX_HEADING_DEG,
                    min_xy_uncertainty_cm=SINGLE_TAG_XY_UNCERTAINTY_CM,
                    min_heading_uncertainty_deg=SINGLE_TAG_HEADING_UNCERTAINTY_DEG):
                # A single-tag view that disagrees is not evidence either way:
                # the tracker coasts (it does not demand recovery).
                self.tracker.note_camera(self.clock(), estimator_failed=False,
                                         intentional_look_away=self._looking_away)
        return self.tracker.health, EnergySample(self._battery(), self.clock())

    def _battery(self) -> int:
        """The Tello's battery reading, never a reason to abort a flight.

        The reading is unstable (it drops, jumps back and sometimes fails);
        a failed or out-of-range read reuses the last good value (0 before
        any). Battery is displayed and recorded, not gated on.
        """
        try:
            value = self.runner._call(self.client.battery)
        except Exception:
            value = None
        if type(value) is int and 0 <= value <= 100:
            self._last_battery = value
        return self._last_battery if self._last_battery is not None else 0

    def recovery_burst(self, action) -> tuple[PoseReport, ...]:
        """One stationary tag burst as ``PoseReport``s for the recovery policy.

        ``RecoveryController.assess_burst`` judges the reports itself (count,
        spread, mapped tags, containment); this only converts the WP5 burst.
        Only a report that itself saw two or more mapped tags is joint-tag
        evidence. Height comes from the passive state stream: this runs as a
        supervised wait operation, which must return data and never issue an
        SDK command. An unreliable burst returns no reports (never a guess).
        """
        duration_s = max(0.5, self._within(float(action.upper_bound_s)) - HEIGHT_READ_SLACK_S)
        with self._burst_lock:
            burst = list(self._burst(self.client, duration_s=duration_s))
        result = localise(burst)
        if not isinstance(result, LocalisationFix):
            return ()
        joint = [report for report in burst if report.heading_deg is not None
                 and len({obs.tag_id for obs in report.tag_observations}) >= 2]
        # No joint view: single-tag reports, marked as such, which the recovery
        # policy accepts only when they agree with the current estimate.
        single = not joint
        reports = joint or [report for report in burst if report.heading_deg is not None
                            and len({obs.tag_id for obs in report.tag_observations}) == 1]
        if not reports:
            return ()
        deltas = sorted(abs(heading_delta_deg(report.heading_deg, result.heading_deg)) for report in reports)
        heading_uncertainty = deltas[int(0.95 * (len(deltas) - 1))]
        xy_uncertainty = result.spread_p95_cm
        if single:
            heading_uncertainty = max(heading_uncertainty, SINGLE_TAG_HEADING_UNCERTAINTY_DEG)
            xy_uncertainty = max(xy_uncertainty, SINGLE_TAG_XY_UNCERTAINTY_CM)
        z_cm = self._passive_height_cm()
        measured_at = self.clock()
        return tuple(PoseReport(str(report.report_id), PoseFix(
            Position(round(report.point[0] * CM_PER_UNIT), round(report.point[1] * CM_PER_UNIT), z_cm),
            report.heading_deg % 360, measured_at, xy_uncertainty, heading_uncertainty,
            tuple(sorted({obs.tag_id for obs in report.tag_observations})),
            source=SINGLE_TAG_SOURCE if single else "mapped_joint_tags")) for report in reports)

    def measure_home(self, definition: HomeDefinition) -> HomeState:
        """The airborne home: position and heading from tags, height from the state stream.

        Returns data only: no SDK command is sent (``_passive_height_cm``),
        matching StepExecutor's documented ``measure_home`` contract, so an
        abandoned measurement can never reach the aircraft after landing
        begins.
        """
        time.sleep(self._home_settle_s)
        fix = None
        for _ in range(HOME_BURST_ATTEMPTS):
            # Only a two-tag fix is a home: the tracker's accept_fix refuses a
            # single-tag one, and set_home_offset would shift the semantic
            # frame on one tag's word. A burst that framed the tags without
            # such a fix is retried once: a hovering Tello often frames both
            # tags a moment later. A blind burst (no tag, or a stray frame)
            # is not: the aircraft is lost or drifting, and it lands now
            # rather than hover blind for another attempt.
            burst = self._read_burst(HOME_BURST_MAX_S)
            candidate = self._fix_from_burst(burst)
            if candidate is not None and len(candidate.tag_ids) >= 2:
                fix = candidate
                break
            if not _frames_the_tags(burst):
                break
        if fix is None:
            raise LiveStackError("home: no two-tag fix after takeoff; the home is never assumed")
        z_cm = self._passive_height_cm()
        if not self.tracker.initialized:
            # Takeoff is not gated on a pad fix; this airborne fix is the
            # tracker's first anchor (there is no pad anchor to climb from).
            self._climb_applied = True
            self.tracker.accept_fix(fix.pose_fix(z_cm))
        return HomeState(Position(fix.x_cm, fix.y_cm, z_cm), fix.heading_deg, definition.route_id, fix.measured_at)

    def emergency(self, executor) -> object:
        """A forced landing through the executor, never the motor-stop datagram."""
        return executor.land()

    def command_sink(self, command: PlannedCommand, cancellation: threading.Event):
        kind, args = command.kind, dict(command.arguments)
        runner, client = self.runner, self.client
        if kind == "CAPTURE":
            return self._capture(command)
        if kind == "LOCALISE":
            return self._localise(command, cancellation)
        if kind == "MOVE":
            position = runner.move_checked(args["direction"], args["distance_cm"],
                                           validate_vertical_telemetry=False, commit_position=False)
            self._dead_reckon(command)
            return position
        if kind == "ROTATE":
            response = runner._call(lambda: client.rotate(args["direction"], round(args["degrees"])))
            if not runner._is_ok(response):
                raise RuntimeError(f"rotation failed: {response}")
            self._dead_reckon(command)
            # Look/restore and between-pose retargets can all turn the camera
            # away from the tag-bearing headings. Phase alone is not enough:
            # a side-facing pose must not turn expected tagless refreshes into
            # localization failures before its three-frame sweep begins.
            heading = self.tracker.health.heading_deg
            self._looking_away = all(abs(heading_delta_deg(heading, bearing)) > 20
                                     for bearing in self._tag_bearing_headings)
            return CommandOutcome()
        if kind == "SET_SPEED":
            response = runner._call(lambda: client.set_speed(args["speed_cm_s"]))
            if not runner._is_ok(response):
                raise RuntimeError(f"speed command failed: {response}")
            return CommandOutcome()
        if kind == "READ_TELEMETRY":
            if args.get("phase") == "vertical":
                return HeightTelemetry(self._height_cm())
            return Telemetry(self._battery(), round(runner.envelope.position.z_cm))
        if kind == "SETTLE":
            cancellation.wait(self._within(command.upper_bound_s))
            return CommandOutcome()
        if kind in ("WATCHDOG", "TRIM_ALLOWANCE"):
            return CommandOutcome()
        raise ValueError(f"the live flight stack does not execute {kind}")

    def _capture(self, command: PlannedCommand) -> str:
        """Save one stream frame as a PNG; its observation ID names the file and content."""
        timeout = max(0.1, self._within(command.upper_bound_s))
        # Video is a separate transport: a stalled frame never holds the SDK lock.
        frame = self.runner._call_locked(lambda: self.client.read_frame(timeout=timeout))
        path = self._recorder.save(frame)
        observation_id = f"frame-{path.stem}-{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}"
        self.capture_records[observation_id] = (
            path, datetime.now(timezone.utc).isoformat(), self.tracker.health.heading_deg)
        return observation_id

    def _localise(self, command: PlannedCommand, cancellation: threading.Event) -> Position:
        settle_s = float(dict(command.arguments).get("settle_s", 0.0))
        if cancellation.wait(settle_s):
            raise RuntimeError("localise cancelled")
        duration_s = min(BURST_MAX_S, self._within(command.upper_bound_s) - settle_s - HEIGHT_READ_SLACK_S)
        if duration_s <= 0:
            raise LiveStackError("LOCALISE bound leaves no time for a tag burst")
        fix = self._tag_fix(duration_s)
        if cancellation.is_set():
            raise RuntimeError("localise cancelled")
        if fix is None:
            raise LiveStackError("LOCALISE: no reliable tag fix")
        return Position(fix.x_cm, fix.y_cm, self._height_cm())


class TelloFlightStackFactory:
    """``client_factory(tracker=, clock=, site=)`` for ``live_session.run_count_return``.

    Built by the CLI after every gate; it reaches the aircraft only when
    called. The stack it returns is connected and streaming (tag poses need
    video before takeoff) with motors off.
    """

    def __init__(self, *, link: TelloLink, tag_map: TagMap | None, run_id: str, run_dir: str | Path,
                 pose_burst: Callable[..., list] | None = None, minimum_battery: int = 40,
                 home_settle_s: float = SETTLE_S, max_duration_s: float | None = None,
                 record_video: bool = False) -> None:
        self.link, self.tag_map, self.run_id, self.run_dir = link, tag_map, run_id, Path(run_dir)
        self._pose_burst = pose_burst
        self.minimum_battery, self.home_settle_s = minimum_battery, home_settle_s
        self.record_video = record_video
        # The manifest's own declared flight-duration bound: StepExecutor's
        # deadline enforces it (DEADLINE_EXCEEDED before each command, and it
        # caps SafetySupervisor._land's landing upper bound). Without it, the
        # authorized session duration is never actually enforced in flight.
        self.max_duration_s = max_duration_s

    def _default_burst(self):
        if self.tag_map is None:
            raise LiveStackError("the WP5 tag-pose burst needs the bound tag map")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.run_dir / "pose.sqlite3", check_same_thread=False)
        ensure_schema(conn)
        tag_map, run_id = self.tag_map, self.run_id
        return lambda client, *, duration_s: wp5_pose_burst(client, tag_map, conn, run_id=run_id,
                                                            duration_s=duration_s)

    def __call__(self, *, tracker, clock, site: RoomSite) -> FlightStack:
        envelope = site_envelope(site)
        burst = self._pose_burst or self._default_burst()
        client = self.link.client()
        if self.record_video:
            client.set_video_record_path(self.run_dir / "flight.h264")
        runner = FlightTestRunner(
            client, "real", self.minimum_battery, retain_video_stream=True, envelope=envelope,
            adapter=getattr(client, "_sentinel_adapter", None),
            route_before=getattr(client, "_sentinel_route_before", None),
            route_after=getattr(client, "_sentinel_route_after", None),
            route_snapshot_provider=getattr(client, "_sentinel_route_provider", None))
        stack = _TelloStack(client=client, runner=runner, tracker=tracker, clock=clock, site=site, burst=burst,
                            recorder=FrameRecorder(self.run_dir / "captures"), home_settle_s=self.home_settle_s)
        deadline = None if self.max_duration_s is None else clock() + self.max_duration_s
        executor = StepExecutor(runner, clock=clock, deadline=deadline, measure_home=stack.measure_home,
                                command_sink=stack.command_sink, home_tolerance_cm=HOME_TOLERANCE_CM,
                                home_measurement_timeout_s=(self.home_settle_s + HOME_BURST_ATTEMPTS * HOME_BURST_MAX_S
                                                            + 2 * HEIGHT_READ_SLACK_S))
        stack.executor = executor
        headings = mapped_heading_tag_ids(self.tag_map)
        # live_session gives the supervisor a recovery adapter exactly when
        # both the burst and mapped headings exist; only then does refresh
        # defer a RECOVERY_REQUIRED re-anchor to it.
        stack.defer_recovery_fixes = bool(headings)
        runner._call(client.stream_on)
        return FlightStack(executor=executor, refresh=stack.refresh, emergency=stack.emergency, frame_source=client,
                           capture_records=stack.capture_records,
                           trigger_emergency=runner.trigger_emergency, recovery_burst=stack.recovery_burst,
                           mapped_heading_tag_ids=headings)
