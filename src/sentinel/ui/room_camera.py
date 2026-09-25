"""Read-only camera presentation for the active room run.

Live frames are peeked from the existing decoder. Analysis boxes are read from
the verified result for their own saved capture, never projected onto a later
live frame.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def live_frame(run):
    source = run.camera_source
    if run.status != "RUNNING" or source is None:
        return None
    frame = source.peek_frame()
    if frame is None:
        return None

    import cv2
    import numpy as np

    pixels = np.asarray(frame.data)
    if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.shape[0] != frame.height or pixels.shape[1] != frame.width:
        return None
    bgr = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR) if frame.pixel_format == "rgb24" else pixels
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
        cv2.aruco.DetectorParameters(),
    )
    corners, ids, _ = detector.detectMarkers(gray)
    tags = [dict(id=int(tag_id), corners=[[round(float(x), 1), round(float(y), 1)] for x, y in square.reshape(4, 2)])
            for square, tag_id in zip(corners, ids.flatten())] if ids is not None else []
    encoded, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 76])
    if not encoded:
        return None
    timestamp = getattr(frame, "timestamp", None)
    observed_at = timestamp.isoformat() if timestamp is not None else datetime.now(timezone.utc).isoformat()
    return dict(payload=jpg.tobytes(), width=frame.width, height=frame.height,
                observed_at=observed_at, frame_number=getattr(frame, "frame_number", None), tags=tags)


def capture_record(run, observation_id=None):
    """Return a saved capture; include boxes only from its verified VLM result."""
    root = run.frame_root
    database = run.store_path
    if root is None:
        return None
    if database is None or not Path(database).is_file():
        return _saved_capture(run, observation_id)
    newest_saved = _saved_capture(run) if observation_id is None else None
    with closing(sqlite3.connect(str(database), timeout=1.0)) as connection:
        connection.row_factory = sqlite3.Row
        predicate = "AND o.observation_id = ?" if observation_id is not None else ""
        arguments = (run.id, observation_id) if observation_id is not None else (run.id,)
        row = connection.execute(
            "SELECT o.observation_id, o.frame_path, o.frame_sha256, o.captured_at, "
            "r.provider, r.resolved_model FROM room_observations o "
            "JOIN room_perception_results r ON r.session_id = o.session_id "
            "AND r.observation_id = o.observation_id "
            "WHERE o.session_id = ? AND r.status = 'SUCCEEDED' "
            "AND r.identity_status = 'VERIFIED' AND r.authorizes_current_state = 1 "
            f"{predicate} ORDER BY o.captured_at DESC, o.observation_id DESC LIMIT 1", arguments,
        ).fetchone()
        if row is None:
            return _saved_capture(run, observation_id)
        if newest_saved is not None and newest_saved["captured_at"] > row["captured_at"]:
            return newest_saved
        path = Path(row["frame_path"]).resolve()
        if not path.is_relative_to(Path(root).resolve()) or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
            return None
        detections = connection.execute(
            "SELECT class_name, confidence_band, raw_score, score_kind, full_frame_bbox_json "
            "FROM room_detection_evidence WHERE session_id = ? AND observation_id = ? ORDER BY detection_id",
            (run.id, row["observation_id"]),
        ).fetchall()
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
    boxes = [dict(label=d["class_name"], confidence=d["confidence_band"],
                  raw_score=d["raw_score"], score_kind=d["score_kind"],
                  bbox=json.loads(d["full_frame_bbox_json"])) for d in detections]
    return dict(observation_id=row["observation_id"], path=path, sha256=row["frame_sha256"],
                captured_at=row["captured_at"], provider=row["provider"], model=row["resolved_model"],
                width=width, height=height, detections=boxes, verified=True)


def capture_index(run):
    """Every saved capture of the run, oldest first: the analysed ones (with
    their box count) and the frames no verified result exists for -- a zone
    that failed on its last look never sends its frames to the detector."""
    root = run.frame_root
    if root is None:
        return []
    root = Path(root).resolve()
    entries, seen = [], set()
    database = run.store_path
    if database is not None and Path(database).is_file():
        with closing(sqlite3.connect(str(database), timeout=1.0)) as connection:
            rows = connection.execute(
                "SELECT o.observation_id, o.frame_path, o.captured_at, "
                "(SELECT COUNT(*) FROM room_detection_evidence d WHERE d.session_id = o.session_id "
                "AND d.observation_id = o.observation_id) FROM room_observations o "
                "JOIN room_perception_results r ON r.session_id = o.session_id "
                "AND r.observation_id = o.observation_id "
                "WHERE o.session_id = ? AND r.status = 'SUCCEEDED' "
                "AND r.identity_status = 'VERIFIED' AND r.authorizes_current_state = 1", (run.id,),
            ).fetchall()
        for observation_id, frame_path, captured_at, count in rows:
            seen.add(Path(frame_path).resolve())
            entries.append(dict(observation_id=observation_id, captured_at=captured_at,
                                verified=True, detections=count))
    for observation_id, record in dict(getattr(run, "capture_records", {}) or {}).items():
        path = Path(record[0]).resolve()
        if path not in seen:
            seen.add(path)
            entries.append(dict(observation_id=observation_id, captured_at=record[1],
                                verified=False, detections=0))
    # A past run's frames outlive the flight's in-memory capture records.
    for path in sorted((root / "captures").glob("*.png")):
        if path.resolve() not in seen:
            captured_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            entries.append(dict(observation_id=f"file:{path.name}", captured_at=captured_at,
                                verified=False, detections=0))
    return sorted(entries, key=lambda entry: (entry["captured_at"], entry["observation_id"]))


def _saved_capture(run, observation_id=None):
    """Show the flight's saved frame when analysis never produced verified boxes."""
    records = dict(getattr(run, "capture_records", {}) or {})
    if observation_id is not None and observation_id.startswith("file:"):
        path = Path(run.frame_root).resolve() / "captures" / observation_id[len("file:"):]
        if path.parent != Path(run.frame_root).resolve() / "captures" or not path.is_file():
            return None
        records[observation_id] = (path, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
    if observation_id is None:
        if not records:
            return None
        observation_id = max(records, key=lambda key: records[key][1])
    record = records.get(observation_id)
    if record is None:
        return None
    path = Path(record[0]).resolve()
    root = Path(run.frame_root).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        return None
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
    return dict(observation_id=observation_id, path=path,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                captured_at=record[1], provider=None, model=None,
                width=width, height=height, detections=[], verified=False)


def capture_bytes(record):
    payload = record["path"].read_bytes()
    return payload if hashlib.sha256(payload).hexdigest() == record["sha256"] else None
