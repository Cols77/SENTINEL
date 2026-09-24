"""Default, production factories for RoomController's injected observation
collaborators (``resolve_checkpoint``, ``perception_capture``,
``perception_provider``) -- Plan 06 Task 4's live wiring.

Nothing in this module constructs a drone client, HTTP session or detector
at import time or merely by building one of these factories: every real
object (the flight client behind ``perception_capture``'s frame read, the
HTTP-backed detector behind ``perception_provider``) is supplied by the
caller or built lazily, on the returned collaborator's first actual
invocation -- never eagerly. Tests exercise every factory with fakes only
and assert that constructing (without calling) the default collaborators
touches no real transport.

Each factory only *composes* existing, already-tested modules:
``sentinel.room.semantic``/``sentinel.cube.checkpoint`` for geometry,
``sentinel.survey.live_detect``/``scripts.survey_run`` for the existing
OpenRouter Gemini live-detection path. It defines no new geometry, detector
or protocol of its own.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping
import uuid

from sentinel.camera import TELLO_MEASURED, CameraModel
from sentinel.cube.checkpoint import CheckpointPlan, CommandLimits, PlannedCommand, plan_checkpoint
from sentinel.models import Position
from sentinel.motion_bounds import rotate_reply_upper_bound_s
from sentinel.survey.live_detect import LIVE_CLASSES, detect_frame

from . import calibration
from .artifacts import atomic_write_bytes
from .grounding import CountGeometry
from .spatial import SupportDeclaration, SupportKind
from .ingest import PerceptionRequest, PerceptionResult, capture_request, frame_digest, result_from_dict
from .semantic import resolve_pose_id
from .site import RoomSite
from .store import RoomStore


class WiringError(ValueError):
    """A production collaborator cannot be built from what it was given."""


# ---------------------------------------------------------------------------
# resolve_checkpoint: semantic pose_id -> cube checkpoint plan
# ---------------------------------------------------------------------------

#: Three looks around each resolved pose yaw. The final restore/retarget
#: rotation keeps the checkpoint's terminal heading at that declared yaw.
_SWEEP_OFFSETS_DEG = (-30, 0, 30)


def _retarget_terminal_heading(plan: CheckpointPlan, target_heading_deg: float, limits: CommandLimits) -> CheckpointPlan:
    """Extend *plan* with one more ROTATE to *target_heading_deg*, if it doesn't already end there.

    ``plan_checkpoint`` always ends a plan back at its own
    ``limits.canonical_heading_deg`` (the one heading its axis-mapped MOVE
    commands are geometrically valid at -- see ``make_resolve_checkpoint``);
    it refuses outright to be given a different ``terminal_heading_deg``. The
    controller's declared-pose check (``_checkpoint_targets_declared_pose``)
    requires the opposite: the checkpoint must end facing the *pose's* own
    declared yaw. This reconciles the two by appending one more rotate after
    ``plan_checkpoint`` has already built a geometrically-correct translation
    plan, rather than touching ``plan_checkpoint``'s own invariant or its
    private rotation helper.
    """
    delta = (target_heading_deg - plan.terminal_heading_deg + 180.0) % 360.0 - 180.0
    if abs(delta) < 1e-9:
        return plan
    direction = "ccw" if delta > 0 else "cw"
    rotate = PlannedCommand.from_mapping(
        "ROTATE", {"degrees": abs(delta), "direction": direction, "phase": "retarget"},
        upper_bound_s=rotate_reply_upper_bound_s(
            degrees=abs(delta), base_timeout_s=limits.transport_limits.reply_timeout_s,
        ),
        interruptible=False,
    )
    return CheckpointPlan(
        checkpoint_id=plan.checkpoint_id, start=plan.start, target=plan.target,
        start_heading_deg=plan.start_heading_deg, terminal_heading_deg=target_heading_deg,
        commands=plan.commands + (rotate,), worst_case_s=plan.worst_case_s + rotate.upper_bound_s,
    )


def make_resolve_checkpoint(
    *, site: RoomSite, profile: calibration.CalibrationProfile,
    pose_source: Callable[[], object], limits: CommandLimits,
    sweep_offsets_deg: tuple[int, ...] = _SWEEP_OFFSETS_DEG,
) -> Callable[[object, object], CheckpointPlan]:
    """Build the production ``resolve_checkpoint`` collaborator.

    ``pose_source`` returns the aircraft's current measured pose (an object
    with ``.position``/``.heading_deg``, e.g. ``supervisor.tracker.health``)
    each time it is called -- never cached, since it must reflect the
    aircraft's actual position at resolution time, not whatever it was when
    this factory ran. ``profile`` supplies the only site speed this wiring
    ever commands: the slowest one the calibration profile actually
    validated (``profile.speeds_cm_s``), never a guessed or hardcoded
    default. ``limits`` is the SafetySupervisor's own injected
    ``CommandLimits`` (fix round 1, Q3) -- the same calibrated constants
    ``calibration_hashes`` binds the grant to and ``SafetySupervisor._path_clear``
    assesses every plan against -- used here unchanged, never a fresh
    default ``CommandLimits()`` a supervisor was never calibrated to accept.

    Every translation is planned at ``limits.canonical_heading_deg`` (fix
    round 1, Q1): ``plan_checkpoint``'s axis-to-direction mapping (x ->
    left/right, y -> back/forward) is only geometrically valid while the
    aircraft actually holds that one heading -- ``LocalizationTracker``
    resolves a MOVE's displacement relative to whatever heading the aircraft
    is *currently at*, not a fixed world frame. Planning translations at the
    resolved pose's own declared yaw instead (the prior bug) silently
    rotated every commanded displacement by ``pose_yaw - canonical``,
    landing the aircraft anywhere but the declared target for any pose whose
    look is not the canonical direction. The sweep faces the pose's yaw plus
    each offset via ``looks_deg`` relative to canonical, and the
    checkpoint holds that yaw as its terminal heading (added back on with
    ``_retarget_terminal_heading``) -- so the controller's declared-pose
    check (``_checkpoint_targets_declared_pose``) still sees the checkpoint
    end exactly where its candidate ID says it will.

    Raises ``WiringError`` immediately (never lazily, inside the returned
    closure) if *profile* has no validated speed to fly at.
    """
    if not profile.speeds_cm_s:
        raise WiringError("resolve_checkpoint requires a calibration profile with a validated speed")
    if not sweep_offsets_deg:
        raise WiringError("resolve_checkpoint requires at least one look")
    speed_cm_s = min(profile.speeds_cm_s)
    canonical_heading_deg = limits.canonical_heading_deg

    def resolve_checkpoint(candidate, snapshot) -> CheckpointPlan:
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        resolved = resolve_pose_id(site, pose_id)
        if resolved is None:
            raise WiringError(f"pose_id {pose_id!r} does not resolve against this site's semantic frame")
        pose = pose_source()
        target = Position(resolved.x_cm, resolved.y_cm, resolved.z_cm)
        pose_yaw_deg = float(resolved.yaw_deg)
        look_deg = pose_yaw_deg - canonical_heading_deg
        looks_deg = tuple(look_deg + offset for offset in sweep_offsets_deg)
        plan = plan_checkpoint(
            checkpoint_id=candidate.candidate_id, start=pose.position, target=target,
            start_heading_deg=pose.heading_deg, terminal_heading_deg=canonical_heading_deg,
            speed_cm_s=speed_cm_s, looks_deg=looks_deg,
            capture_count=len(looks_deg), limits=limits,
        )
        return _retarget_terminal_heading(plan, pose_yaw_deg, limits)

    return resolve_checkpoint


def make_chained_sweep(*, site: RoomSite, profile, pose_source: Callable, limits: CommandLimits,
                       sweep_offsets_deg: tuple[int, ...] = (-30, 0, 30)) -> tuple[Callable, ...]:
    """One resolver per look of a zone's sweep, chained: the first look flies
    to the zone and photographs; each following look only turns on from where
    the previous one stopped, settles and photographs; the last turns back to
    the zone's heading. Four turns for three photos, instead of turning out
    and back for every photo (live 2026-09-24: 72 s per zone).

    Each look is still its own checkpoint, admitted by the supervisor, and
    planned from the tracker's measured heading when it is resolved."""
    from sentinel.cube.checkpoint import _command, _rotation

    if not sweep_offsets_deg:
        raise WiringError("a sweep needs at least one look")
    first = make_resolve_checkpoint(site=site, profile=profile, pose_source=pose_source, limits=limits,
                                    sweep_offsets_deg=(sweep_offsets_deg[0],))
    canonical = limits.canonical_heading_deg
    last = len(sweep_offsets_deg) - 1

    def plan(candidate_id, start, start_heading, target, terminal_heading, commands):
        commands = tuple(commands)
        return CheckpointPlan(checkpoint_id=candidate_id, start=start, target=target,
                              start_heading_deg=start_heading, terminal_heading_deg=terminal_heading,
                              commands=commands, worst_case_s=sum(c.upper_bound_s for c in commands))

    def leg(index, offset):
        def resolve(candidate, snapshot) -> CheckpointPlan:
            pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
            resolved = resolve_pose_id(site, pose_id)
            if resolved is None:
                raise WiringError(f"pose_id {pose_id!r} does not resolve against this site's semantic frame")
            target = Position(resolved.x_cm, resolved.y_cm, resolved.z_cm)
            look_heading = float(resolved.yaw_deg) + offset
            if index == 0:
                full = first(candidate, snapshot)
                if last == 0:
                    return full
                # Stop facing this look: the next look turns on from here.
                cut = max(i for i, c in enumerate(full.commands) if c.kind == "CAPTURE") + 1
                return plan(candidate.candidate_id, full.start, full.start_heading_deg, target, look_heading,
                            full.commands[:cut])
            pose = pose_source()
            rotation, _ = _rotation(pose.heading_deg, look_heading, limits, "look")
            commands = [*rotation, _command("SETTLE", limits.look_settle_s, True, after="look"),
                        _command("CAPTURE", limits.capture_s, True, index=0,
                                 look_deg=float(resolved.yaw_deg) - canonical + offset)]
            terminal = look_heading
            if index == last:
                restore, _ = _rotation(look_heading, float(resolved.yaw_deg), limits, "restore")
                commands += [*restore, _command("READ_TELEMETRY", limits.telemetry_s, True, phase="final")]
                terminal = float(resolved.yaw_deg)
            return plan(candidate.candidate_id, pose.position, pose.heading_deg, target, terminal, commands)
        return resolve

    return tuple(leg(index, offset) for index, offset in enumerate(sweep_offsets_deg))


# ---------------------------------------------------------------------------
# count_geometry: the calibrated Tello camera plus the site's support planes
# ---------------------------------------------------------------------------


def camera_model_sha256(camera: CameraModel = TELLO_MEASURED) -> str:
    """The canonical hash of a camera model (the manifest's ``camera_sha256``
    for a session that counts with it)."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(asdict(camera), sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def make_count_geometry(site: RoomSite, *, camera: CameraModel = TELLO_MEASURED) -> CountGeometry:
    """Task B item 3: the production ``CountGeometry``.

    The camera is ``sentinel.camera.TELLO_MEASURED`` (stream fx ~893 and the
    measured 8.6 degree down pitch -- the single real-pixel camera model per
    coherence spec section 10.5). Each class a site support plane names rests
    on it: the FLOOR plane gives FLOOR support, any other plane a declared
    horizontal plane at its surveyed height. A class no plane names keeps
    UNKNOWN support, so its detections are reported unplaced, never as zero.
    """
    supports = []
    for plane in site.support_planes:
        for class_name in plane.classes:
            if plane.plane_id == "FLOOR":
                supports.append((class_name, SupportDeclaration(SupportKind.FLOOR)))
            else:
                supports.append((class_name, SupportDeclaration(SupportKind.DECLARED_HORIZONTAL_PLANE,
                                                                plane.plane_id, float(plane.z_cm))))
    return CountGeometry(camera, tuple(supports))


# ---------------------------------------------------------------------------
# perception_capture: checkpoint frames from the connected flight stack
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapturedRequest:
    request: PerceptionRequest
    heading_deg: float


def _frame_to_png_bytes(frame) -> bytes:
    """Decode a ``VideoFrame`` (rgb24/bgr24 raw bytes) into a PNG payload."""
    import numpy as np
    from PIL import Image

    pixel_format = getattr(frame, "pixel_format", "rgb24")
    if pixel_format not in ("rgb24", "bgr24"):
        raise WiringError(f"unsupported frame pixel_format {pixel_format!r}")
    array = np.frombuffer(frame.data, dtype=np.uint8)
    array = array.reshape(int(frame.height), int(frame.width), -1)[:, :, :3]
    if pixel_format == "bgr24":
        array = array[:, :, ::-1]
    buffer = __import__("io").BytesIO()
    Image.fromarray(array, mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def make_perception_capture(
    *, store: RoomStore, flight_client, frames_dir: str | Path, requested_model: str, run_id: str,
    frame_timeout_s: float = 10.0, clock: Callable[[], str] | None = None,
    capture_records: Mapping[str, tuple[Path, str, float]] | None = None,
) -> Callable[[object, object, object], PerceptionRequest | tuple[CapturedRequest, ...]]:
    """Build the production ``perception_capture`` collaborator.

    ``capture_records`` maps completed checkpoint observation IDs to their
    saved PNG, capture time and tracked heading. The optional legacy path
    reads ``flight_client``'s already-open stream. This factory never opens a
    connection itself. The request's
    ``snapshot_version`` is read fresh from *store* at capture time (fix
    round 1, Q4/S1's rule: never the stale pre-turn snapshot a candidate was
    computed against), and ``pose_revision_id`` reuses the session's own
    ``pose_revision`` counter -- one definition of "which pose generation
    this frame was captured under", not a second one invented here.
    """
    frames_dir = Path(frames_dir)
    clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def perception_capture(candidate, checkpoint_outcome, snapshot) -> PerceptionRequest | tuple[CapturedRequest, ...]:
        context = {
            "session_id": snapshot.session_id,
            "action_id": candidate.candidate_id,
            "snapshot_version": store.session_version(snapshot.session_id),
            "pose_revision_id": str(snapshot.pose_revision),
            "requested_model": requested_model,
        }
        if capture_records is not None:
            captures = []
            for observation_id in checkpoint_outcome.observation_ids:
                frame_path, captured_at, heading_deg = capture_records[observation_id]
                # A repeated image still represents a distinct look. Keep its
                # provider identity separate so two correlated results cannot
                # collide in the durable payload cache.
                import hashlib
                digest = hashlib.sha256(
                    f"{observation_id}:{frame_digest(frame_path)}:{requested_model}".encode("utf-8")
                ).hexdigest()
                request = capture_request({**context, "request_digest": digest}, frame_path=frame_path, run_id=run_id,
                                          captured_at=captured_at, classes=LIVE_CLASSES)
                captures.append(CapturedRequest(request, heading_deg))
            return tuple(captures)
        frame = flight_client.read_frame(timeout=frame_timeout_s)
        frame_bytes = _frame_to_png_bytes(frame)
        frame_path = frames_dir / f"{uuid.uuid4()}.png"
        atomic_write_bytes(frame_path, frame_bytes)
        captured_at = clock()
        return capture_request(context, frame_path=frame_path, run_id=run_id,
                               captured_at=captured_at, classes=LIVE_CLASSES)

    return perception_capture


# ---------------------------------------------------------------------------
# perception_provider: the existing survey live detector / OpenRouter Gemini path
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDER_NAME = "gemini-openrouter"


def _default_detector(environ: Mapping[str, str] | None, provider_name: str):
    # Imported lazily so building (not calling) a production perception_provider
    # never even imports httpx/the real detector classes, and so a fixture that
    # only exercises resolve_checkpoint/perception_capture is never coupled to
    # scripts/survey_run.py's own import surface.
    from scripts.survey_run import build_provider_detector

    return build_provider_detector(provider_name, environ=environ)


def make_perception_provider(
    *, environ: Mapping[str, str] | None = None, provider_name: str = _DEFAULT_PROVIDER_NAME,
    classes: tuple[str, ...] = LIVE_CLASSES, detector_factory: Callable[[], object] | None = None,
) -> Callable[[PerceptionRequest], PerceptionResult]:
    """Build the production ``perception_provider`` collaborator.

    Reuses ``sentinel.survey.live_detect.detect_frame``'s correlated path
    (the same one ``scripts/live_detect.py`` runs against a live flight) and
    ``scripts.survey_run.build_provider_detector`` (the one sanctioned,
    already-gated production call site for a real OpenRouter Gemini
    detector) rather than defining a second detector construction path here.

    The detector -- real by default, or whatever ``detector_factory``
    returns -- is built lazily and cached on the returned collaborator's
    *first actual call*, never merely by calling this factory: a test that
    builds a default ``perception_provider`` and never calls it constructs
    no detector, opens no HTTP session, and reads no API key.
    """
    factory = detector_factory or (lambda: _default_detector(environ, provider_name))
    detector_box: list[object] = []
    detector_lock = Lock()

    def perception_provider(request: PerceptionRequest) -> PerceptionResult:
        if not detector_box:
            with detector_lock:
                if not detector_box:
                    detector_box.append(factory())
        detector = detector_box[0]
        record = {
            "stop": request.observation_id, "look_deg": 0, "path": request.frame_path,
            "sha256": request.frame_sha256, "captured_at": request.captured_at,
            "perception_request": asdict(request),
        }
        row = detect_frame(detector, record, classes, provider=provider_name)
        if "perception_result" not in row:
            raise WiringError(f"perception provider produced no correlated result: {row.get('error')!r}")
        return result_from_dict(row["perception_result"])

    return perception_provider
