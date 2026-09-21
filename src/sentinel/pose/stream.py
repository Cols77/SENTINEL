"""WP5 live-stream pose harness.

Pulls video frames from a client, converts rgb24 -> bgr24 (the detector
needs BGR), runs them through ``estimate_pose`` and appends
``DRONE_POSE_REPORTED`` rows via the pose persistence layer. Fail-closed:
a frame with no detectable tag raises ValueError from estimate_pose, is
logged as a fail-closed diagnostic, produces no persisted row, and the
stream keeps going. When the stream ends or ``max_frames`` frames have
been consumed, ``stop_video()`` is called and the collected reports are
returned.
"""
import dataclasses
import logging
import math
import time
import uuid
from typing import Callable

import numpy as np

from sentinel.contracts.pose import DronePoseReport
from sentinel.pose.detector import NoTagDetected, estimate_pose
from sentinel.pose.persistence import append_pose_event
from sentinel.video import VideoReadTimeout

logger = logging.getLogger(__name__)

FRAME_READ_TIMEOUT_S = 10.0
DEFAULT_MAX_CONSECUTIVE_TIMEOUTS = 5


def _read_altitude(altitude_source: Callable[[], float | None] | None) -> float | None:
    """Best-effort read-only altitude in metres; any failure yields None."""
    if altitude_source is None:
        return None
    try:
        value = altitude_source()
        if value is None:
            return None
        value = float(value)
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the stream
        logger.debug("altitude source failed: %s", exc)
        return None
    return value if math.isfinite(value) else None


def _to_bgr(frame) -> np.ndarray:
    """Convert a client frame to a BGR uint8 ndarray for the detector."""
    pixel_format = getattr(frame, "pixel_format", "rgb24")
    data = frame.data
    # Defensive: some client backends nest a frame-like object inside .data.
    while not isinstance(data, (bytes, bytearray, np.ndarray)) and hasattr(data, "data"):
        data = data.data
    arr = np.asarray(data, dtype=np.uint8)
    if arr.ndim == 1 and arr.size:
        arr = arr.reshape(int(frame.height), int(frame.width), -1)
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    if pixel_format == "rgb24":
        arr = arr[..., ::-1]
    return np.ascontiguousarray(arr)


def throttled_altitude_source(
    read: Callable[[], float | None],
    *,
    scale: float = 1.0,
    min_interval_s: float = 1.0,
    max_failures: int = 3,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], float | None]:
    """Wrap a read-only height query: cached, rate-limited, self-disabling.

    Returns a callable yielding ``read() * scale`` at most every
    ``min_interval_s`` (cached value in between). After ``max_failures``
    consecutive failures the source is disabled and yields ``None`` forever,
    so a dead telemetry channel cannot stall the video loop.
    """
    state = {"last_t": None, "value": None, "failures": 0}

    def source() -> float | None:
        if state["failures"] >= max_failures:
            return None
        now = clock()
        last = state["last_t"]
        if last is not None and now - last < min_interval_s:
            return state["value"]
        state["last_t"] = now
        try:
            value = read()
            state["value"] = None if value is None else float(value) * scale
            state["failures"] = 0
        except Exception as exc:  # noqa: BLE001
            state["failures"] += 1
            state["value"] = None
            logger.debug("altitude read failed (%d): %s", state["failures"], exc)
        return state["value"]

    return source


def run_pose_stream(
    client,
    tag_map,
    conn,
    *,
    run_id: str,
    max_frames: int | None,
    duration_s: float | None = None,
    source_kind: str = "SIMULATED",
    max_consecutive_timeouts: int = DEFAULT_MAX_CONSECUTIVE_TIMEOUTS,
    recorder=None,
    altitude_source: Callable[[], float | None] | None = None,
    on_frame: Callable[[np.ndarray], bool | None] | None = None,
    on_report: Callable[[DronePoseReport], None] | None = None,
    on_health=None,
    on_evidence=None,
    artifact_sink=None,
    on_estimate=None,
) -> list[DronePoseReport]:
    """Stream ``max_frames`` frames from ``client`` and persist good poses.

    Returns the list of successfully estimated reports (blank/no-tag frames
    contribute nothing). Isolated ``VideoReadTimeout`` reads are tolerated;
    the stream aborts after ``max_consecutive_timeouts`` in a row or on EOF.
    ``altitude_source`` is an optional read-only callable returning metres
    (failures yield ``None``); it never issues flight commands.
    ``on_frame(bgr)`` is an optional display hook called for every frame; returning
    ``False`` stops the stream, and an exception disables the hook (never the stream). Always calls ``stop_video()`` before returning.
    """
    if max_frames is None and duration_s is None:
        raise ValueError("max_frames or duration_s is required")
    if max_frames is not None and max_frames < 1:
        raise ValueError("max_frames must be at least 1")
    if duration_s is not None and duration_s <= 0:
        raise ValueError("duration_s must be positive")

    if max_consecutive_timeouts < 1:
        raise ValueError("max_consecutive_timeouts must be at least 1")

    from datetime import datetime, timezone
    from .evidence import LatestDisplaySink, PoseStreamHealth
    from .detector import estimate_pose_with_evidence
    def publish_evidence(estimate):
        if artifact_sink is not None:
            artifact_sink(estimate.evidence.artifact_id, estimate.encoded_png)
        if on_evidence:on_evidence(estimate.evidence)
        if on_estimate:on_estimate(estimate)
    evidence_display = LatestDisplaySink(publish_evidence) if on_evidence or on_estimate else None
    health_display = LatestDisplaySink(on_health) if on_health else None
    gated_frames, total_timeouts = 0, 0
    last_health = 0.0
    reports: list[DronePoseReport] = []
    timeouts = 0
    no_tag_frames = 0
    i = 0
    deadline = time.monotonic() + duration_s if duration_s is not None else None
    client.start_video()
    try:
        while max_frames is None or i < max_frames:
            if deadline is not None and time.monotonic() >= deadline:
                break
            if health_display and time.monotonic() - last_health >= 1:
                health_display.push(PoseStreamHealth(i,len(reports),no_tag_frames,gated_frames,total_timeouts))
                last_health = time.monotonic()
            try:
                timeout = FRAME_READ_TIMEOUT_S
                if deadline is not None:
                    timeout = min(timeout, max(0.001, deadline - time.monotonic()))
                frame = client.read_frame(timeout=timeout)
                i += 1
                timeouts = 0
            except VideoReadTimeout as exc:
                timeouts += 1
                total_timeouts += 1
                logger.warning(
                    "frame read timeout %d/%d: %s", timeouts, max_consecutive_timeouts, exc
                )
                if timeouts >= max_consecutive_timeouts:
                    logger.error("aborting stream after %d consecutive timeouts", timeouts)
                    break
                continue
            except (RuntimeError, EOFError, StopIteration) as exc:
                logger.info("stream ended after %d frame(s): %s", i, exc)
                break
            if recorder is not None:
                try:
                    recorder.save(frame)
                except Exception as exc:  # noqa: BLE001 - recording is best-effort
                    logger.warning("frame %d recording failed: %s", i, exc)
            bgr = _to_bgr(frame)
            if on_frame is not None:
                try:
                    if on_frame(bgr) is False:
                        logger.info("stopped by preview window after %d frame(s)", i)
                        break
                except Exception as exc:  # noqa: BLE001 - display must never break the stream
                    logger.warning("preview disabled: %s", exc)
                    on_frame = None
            try:
                if evidence_display:
                    report_id = str(uuid.uuid4())
                    estimate = estimate_pose_with_evidence(bgr, tag_map, run_id=run_id,
                        report_id=report_id, artifact_id=f"pose:{run_id}:{report_id}",
                        captured_at=datetime.now(timezone.utc).isoformat(), source_kind=source_kind)
                    report = estimate.report
                else:
                    report = estimate_pose(bgr, tag_map, run_id=run_id,
                        report_id=str(uuid.uuid4()), source_kind=source_kind)
            except NoTagDetected:
                no_tag_frames += 1
                logger.debug("frame %d: no tag detected (no pose persisted)", i)
                continue
            except ValueError as exc:
                gated_frames += 1
                logger.warning("frame %d fail-closed (no pose persisted): %s", i, exc)
                continue
            altitude = _read_altitude(altitude_source)
            if altitude is not None:
                try:
                    report = dataclasses.replace(report, altitude_m=altitude)
                except ValueError as exc:  # contract rejects the reading; keep z=None
                    logger.debug("altitude %r rejected: %s", altitude, exc)
            append_pose_event(conn, report)
            if evidence_display:
                evidence_display.push(dataclasses.replace(estimate,report=report))
            reports.append(report)
            if on_report is not None:
                on_report(report)
            logger.info(
                "frame %d pose report_id=%s point=(%s, %s) heading=%.1f deg",
                i,
                report.report_id,
                report.point[0],
                report.point[1],
                report.heading_deg,
            )
    finally:
        client.stop_video()
        if health_display:
            health_display.push(PoseStreamHealth(i,len(reports),no_tag_frames,gated_frames,total_timeouts))
            health_display.close()
        if evidence_display:
            evidence_display.close()
        if no_tag_frames:
            logger.warning(
                "%d of %d frame(s) had no detectable tag (skipped, no pose persisted)",
                no_tag_frames, i,
            )
    return reports
