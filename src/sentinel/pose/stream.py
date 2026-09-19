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
import logging

import numpy as np

from sentinel.contracts.pose import DronePoseReport
from sentinel.pose.detector import estimate_pose
from sentinel.pose.persistence import append_pose_event

logger = logging.getLogger(__name__)

FRAME_READ_TIMEOUT_S = 10.0


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


def run_pose_stream(
    client,
    tag_map,
    conn,
    *,
    run_id: str,
    max_frames: int,
) -> list[DronePoseReport]:
    """Stream ``max_frames`` frames from ``client`` and persist good poses.

    Returns the list of successfully estimated reports (blank/no-tag frames
    contribute nothing). Always calls ``stop_video()`` before returning.
    """
    reports: list[DronePoseReport] = []
    client.start_video()
    try:
        for i in range(max_frames):
            try:
                frame = client.read_frame(timeout=FRAME_READ_TIMEOUT_S)
            except (RuntimeError, EOFError, StopIteration) as exc:
                logger.info("stream ended after %d frame(s): %s", i, exc)
                break
            try:
                report = estimate_pose(
                    _to_bgr(frame),
                    tag_map,
                    run_id=run_id,
                    report_id=f"{run_id}-f{i}",
                )
            except ValueError as exc:
                logger.warning("frame %d fail-closed (no pose persisted): %s", i, exc)
                continue
            append_pose_event(conn, report)
            reports.append(report)
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
    return reports
