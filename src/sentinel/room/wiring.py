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

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
import uuid

from sentinel.cube.checkpoint import CheckpointPlan, CommandLimits, plan_checkpoint
from sentinel.models import Position
from sentinel.survey.live_detect import LIVE_CLASSES, detect_frame

from . import calibration
from .artifacts import atomic_write_bytes
from .ingest import PerceptionRequest, PerceptionResult, capture_request, result_from_dict
from .semantic import resolve_pose_id
from .site import RoomSite
from .store import RoomStore


class WiringError(ValueError):
    """A production collaborator cannot be built from what it was given."""


# ---------------------------------------------------------------------------
# resolve_checkpoint: semantic pose_id -> cube checkpoint plan
# ---------------------------------------------------------------------------

#: A single straight-ahead capture. Real waypoint look-arcs (multiple looks
#: per checkpoint) are a later task; this wiring only needs a checkpoint that
#: actually reaches the declared pose and captures once there, matching what
#: the controller's own pose-verification guard requires
#: (``_checkpoint_targets_declared_pose``/``_measured_pose_matches_declared_pose``).
_SINGLE_CAPTURE_LOOKS_DEG: tuple[int, ...] = (0,)
_SINGLE_CAPTURE_COUNT = 1


def make_resolve_checkpoint(
    *, site: RoomSite, profile: calibration.CalibrationProfile,
    pose_source: Callable[[], object],
) -> Callable[[object, object], CheckpointPlan]:
    """Build the production ``resolve_checkpoint`` collaborator.

    ``pose_source`` returns the aircraft's current measured pose (an object
    with ``.position``/``.heading_deg``, e.g. ``supervisor.tracker.health``)
    each time it is called -- never cached, since it must reflect the
    aircraft's actual position at resolution time, not whatever it was when
    this factory ran. ``profile`` supplies the only site speed this wiring
    ever commands: the slowest one the calibration profile actually
    validated (``profile.speeds_cm_s``), never a guessed or hardcoded
    default. Each checkpoint's own canonical/terminal heading is the
    resolved pose's declared yaw -- the checkpoint holds that heading
    throughout, exactly like a calibration route (see calibration.py) -- so
    the controller's declared-pose heading check
    (``_checkpoint_targets_declared_pose``) can pass without this wiring
    ever emitting a sustained off-canonical heading.

    Raises ``WiringError`` immediately (never lazily, inside the returned
    closure) if *profile* has no validated speed to fly at.
    """
    if not profile.speeds_cm_s:
        raise WiringError("resolve_checkpoint requires a calibration profile with a validated speed")
    speed_cm_s = min(profile.speeds_cm_s)

    def resolve_checkpoint(candidate, snapshot) -> CheckpointPlan:
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        resolved = resolve_pose_id(site, pose_id)
        if resolved is None:
            raise WiringError(f"pose_id {pose_id!r} does not resolve against this site's semantic frame")
        pose = pose_source()
        target = Position(resolved.x_cm, resolved.y_cm, resolved.z_cm)
        heading_deg = float(resolved.yaw_deg)
        limits = CommandLimits(canonical_heading_deg=heading_deg)
        return plan_checkpoint(
            checkpoint_id=candidate.candidate_id, start=pose.position, target=target,
            start_heading_deg=pose.heading_deg, terminal_heading_deg=heading_deg,
            speed_cm_s=speed_cm_s, looks_deg=_SINGLE_CAPTURE_LOOKS_DEG,
            capture_count=_SINGLE_CAPTURE_COUNT, limits=limits,
        )

    return resolve_checkpoint


# ---------------------------------------------------------------------------
# perception_capture: a frame from the connected flight client's stream
# ---------------------------------------------------------------------------


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
) -> Callable[[object, object, object], PerceptionRequest]:
    """Build the production ``perception_capture`` collaborator.

    ``flight_client`` is the connected drone client (e.g. ``flight_executor.
    client``); this never opens a connection itself, only reads whatever
    stream ``flight_client`` already has open. The request's
    ``snapshot_version`` is read fresh from *store* at capture time (fix
    round 1, Q4/S1's rule: never the stale pre-turn snapshot a candidate was
    computed against), and ``pose_revision_id`` reuses the session's own
    ``pose_revision`` counter -- one definition of "which pose generation
    this frame was captured under", not a second one invented here.
    """
    frames_dir = Path(frames_dir)
    clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def perception_capture(candidate, checkpoint_outcome, snapshot) -> PerceptionRequest:
        frame = flight_client.read_frame(timeout=frame_timeout_s)
        frame_bytes = _frame_to_png_bytes(frame)
        frame_path = frames_dir / f"{uuid.uuid4()}.png"
        atomic_write_bytes(frame_path, frame_bytes)
        captured_at = clock()
        context = {
            "session_id": snapshot.session_id,
            "action_id": candidate.candidate_id,
            "snapshot_version": store.session_version(snapshot.session_id),
            "pose_revision_id": str(snapshot.pose_revision),
            "requested_model": requested_model,
        }
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

    def perception_provider(request: PerceptionRequest) -> PerceptionResult:
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
