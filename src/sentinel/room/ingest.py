"""Correlated, non-commanding perception envelopes and append-only ingestion.

An observation is session-local even when its immutable provider payload is
shared. No association across observations is inferred here: each detection
starts a stable hypothesis; later tracking may append revisions explicitly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Literal, Mapping
import uuid

from .store import IdempotencyConflict, RoomStore

SCHEMA_VERSION = "room-perception-v1"
MAX_IDENTITY_LENGTH = 4096
_INBOX_LOCK = threading.RLock()
_ERROR_CATEGORIES = frozenset({
    "TIMEOUT", "AUTH", "TRANSPORT", "RATE_LIMIT", "SCHEMA", "RESPONSE_TOO_LARGE",
    "PROVIDER_ERROR", "IDENTITY_UNVERIFIED", "IDENTITY_MISMATCH", "DETECTOR_ERROR",
})


class InvalidPerception(ValueError):
    """Invalid envelope; messages deliberately exclude payloads and file paths."""


@dataclass(frozen=True)
class PerceptionRequest:
    schema_version: str
    session_id: str
    action_id: str
    observation_id: str
    frame_id: str
    run_id: str
    snapshot_version: int
    request_digest: str
    frame_path: str
    frame_sha256: str
    captured_at: str
    pose_revision_id: str
    requested_model: str


@dataclass(frozen=True)
class DetectionEvidence:
    detection_id: str
    class_name: str
    confidence_band: Literal["high", "medium", "low"]
    raw_score: float | None
    score_kind: str | None
    bbox_xywh: tuple[float, float, float, float]
    bbox_coordinate_space: Literal["FULL_FRAME_PIXELS", "TILE_PIXELS"]
    frame_width_px: int
    frame_height_px: int
    tile_origin_xy_px: tuple[int, int] | None
    evidence_sha256: str


@dataclass(frozen=True)
class PerceptionResult:
    request: PerceptionRequest
    status: Literal["SUCCEEDED", "FAILED"]
    provider: str
    resolved_model: str | None
    identity_status: Literal["VERIFIED", "MISMATCH", "UNVERIFIED"]
    completed_at: str
    detections: tuple[DetectionEvidence, ...]
    error_category: str | None = None


@dataclass(frozen=True)
class IngestOutcome:
    result_id: str
    payload_id: str
    duplicate: bool
    authorizes_current_state: bool
    hypothesis_ids: tuple[str, ...]


def _json(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise InvalidPerception("invalid JSON value") from None


def canonical_line(value) -> bytes:
    return (_json(value) + "\n").encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _detection_payload(evidence: DetectionEvidence) -> dict:
    payload = asdict(evidence)
    # Delivery-local IDs must never prevent cache sharing across observations.
    payload.pop("detection_id")
    payload.pop("evidence_sha256")
    return payload


def detection_digest(evidence: DetectionEvidence) -> str:
    """Hash coordinate/score evidence, independent of its delivery-local ID."""
    return _digest(_detection_payload(evidence))


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_IDENTITY_LENGTH


def _hash(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _integer(value, minimum=0) -> bool:
    return type(value) is int and value >= minimum


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _timestamp(value) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp
    except (TypeError, ValueError):
        raise InvalidPerception("invalid timestamp") from None


def frame_digest(path: str | Path) -> str:
    try:
        with Path(path).open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except (OSError, ValueError):
        raise InvalidPerception("frame is unavailable") from None
    return digest


def validate_request(request: PerceptionRequest, *, check_frame: bool = True) -> None:
    if not isinstance(request, PerceptionRequest):
        raise InvalidPerception("invalid perception request")
    if request.schema_version != SCHEMA_VERSION or not _integer(request.snapshot_version):
        raise InvalidPerception("invalid perception request version")
    if not all(_text(getattr(request, key)) for key in (
        "session_id", "action_id", "observation_id", "frame_id", "run_id", "frame_path",
        "pose_revision_id", "requested_model",
    )):
        raise InvalidPerception("missing perception correlation")
    if "://" in request.frame_path or not _hash(request.frame_sha256) or not _hash(request.request_digest):
        raise InvalidPerception("invalid frame reference or request digest")
    _timestamp(request.captured_at)
    if check_frame and frame_digest(request.frame_path) != request.frame_sha256:
        raise InvalidPerception("frame hash mismatch")


def capture_request(context: Mapping[str, object], *, frame_path: str | Path, run_id: str,
                    captured_at: str, classes: tuple[str, ...]) -> PerceptionRequest:
    """Assign observation/frame IDs when the producer saves a frame.

    A caller may supply the digest of its full provider request. Otherwise the
    digest covers this boundary's model, class order, and frame content, with
    no session IDs, credentials or path-dependent values.
    """
    frame_sha256 = frame_digest(frame_path)
    try:
        requested_model = context["requested_model"]
        request = PerceptionRequest(
            SCHEMA_VERSION, context["session_id"], context["action_id"], str(uuid.uuid4()),
            str(uuid.uuid4()), run_id, context["snapshot_version"],
            context.get("request_digest") or _digest({
                "schema_version": SCHEMA_VERSION, "requested_model": requested_model,
                "classes": classes, "frame_sha256": frame_sha256,
            }), str(frame_path), frame_sha256, captured_at, context["pose_revision_id"], requested_model,
        )
    except (KeyError, TypeError):
        raise InvalidPerception("missing perception correlation") from None
    validate_request(request, check_frame=False)
    return request


def full_frame_bbox(evidence: DetectionEvidence) -> tuple[float, float, float, float]:
    """Resolve valid pixel evidence without changing its original coordinates."""
    if not isinstance(evidence, DetectionEvidence):
        raise InvalidPerception("invalid detection evidence")
    if not _integer(evidence.frame_width_px, 1) or not _integer(evidence.frame_height_px, 1):
        raise InvalidPerception("invalid frame dimensions")
    box = evidence.bbox_xywh
    if not isinstance(box, tuple) or len(box) != 4 or not all(_number(n) for n in box):
        raise InvalidPerception("invalid detection box")
    x, y, width, height = box
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise InvalidPerception("invalid detection box")
    if evidence.bbox_coordinate_space == "TILE_PIXELS":
        origin = evidence.tile_origin_xy_px
        if not isinstance(origin, tuple) or len(origin) != 2 or not all(_integer(n) for n in origin):
            raise InvalidPerception("invalid tile origin")
        x, y = x + origin[0], y + origin[1]
    elif evidence.bbox_coordinate_space != "FULL_FRAME_PIXELS" or evidence.tile_origin_xy_px is not None:
        raise InvalidPerception("invalid coordinate space")
    if x + width > evidence.frame_width_px or y + height > evidence.frame_height_px:
        raise InvalidPerception("detection box exceeds frame dimensions")
    return x, y, width, height


def validate_result(result: PerceptionResult, *, check_frame: bool = True) -> None:
    if not isinstance(result, PerceptionResult):
        raise InvalidPerception("invalid perception result")
    validate_request(result.request, check_frame=check_frame)
    if not _text(result.provider) or result.status not in ("SUCCEEDED", "FAILED"):
        raise InvalidPerception("invalid perception result status")
    if result.identity_status not in ("VERIFIED", "UNVERIFIED", "MISMATCH"):
        raise InvalidPerception("invalid model identity status")
    if result.identity_status == "MISMATCH":
        if result.status != "FAILED" or not _text(result.resolved_model) \
                or result.resolved_model == result.request.requested_model \
                or result.error_category != "IDENTITY_MISMATCH":
            raise InvalidPerception("provider identity mismatch")
    elif result.resolved_model is not None and result.resolved_model != result.request.requested_model:
        raise InvalidPerception("provider identity mismatch")
    if result.identity_status == "VERIFIED" and not _text(result.resolved_model):
        raise InvalidPerception("verified identity requires a resolved model")
    if result.identity_status == "UNVERIFIED" and result.resolved_model is not None:
        raise InvalidPerception("unverified identity cannot claim a resolved model")
    if result.status == "SUCCEEDED":
        if result.identity_status != "VERIFIED" or result.error_category is not None:
            raise InvalidPerception("successful perception requires verified identity")
    elif not isinstance(result.error_category, str) or result.error_category not in _ERROR_CATEGORIES or result.detections:
        raise InvalidPerception("invalid typed perception failure")
    if _timestamp(result.completed_at) < _timestamp(result.request.captured_at):
        raise InvalidPerception("completion precedes capture")
    if not isinstance(result.detections, tuple):
        raise InvalidPerception("invalid detection collection")
    identities = set()
    for evidence in result.detections:
        full_frame_bbox(evidence)
        if not _text(evidence.detection_id) or evidence.detection_id in identities or not _text(evidence.class_name):
            raise InvalidPerception("invalid detection identity")
        identities.add(evidence.detection_id)
        if evidence.confidence_band not in ("high", "medium", "low"):
            raise InvalidPerception("invalid confidence band")
        if evidence.score_kind is not None and not _text(evidence.score_kind):
            raise InvalidPerception("invalid score kind")
        if evidence.raw_score is not None and (
            not _number(evidence.raw_score) or evidence.score_kind in (None, "NONE", "VLM_CONFIDENCE_BAND")
        ):
            raise InvalidPerception("invalid raw detector score")
        if not _hash(evidence.evidence_sha256) or evidence.evidence_sha256 != detection_digest(evidence):
            raise InvalidPerception("detection evidence hash mismatch")


def _exact_fields(document, contract):
    if not isinstance(document, dict) or set(document) != {field.name for field in fields(contract)}:
        raise InvalidPerception("malformed perception envelope")
    return document


def request_from_dict(document) -> PerceptionRequest:
    request = PerceptionRequest(**_exact_fields(document, PerceptionRequest))
    validate_request(request, check_frame=False)
    return request


def result_from_dict(document) -> PerceptionResult:
    """Strict wire decoder. Never echo malformed provider data in diagnostics."""
    try:
        data = dict(_exact_fields(document, PerceptionResult))
        data["request"] = request_from_dict(data["request"])
        if not isinstance(data["detections"], (list, tuple)):
            raise InvalidPerception("invalid detection collection")
        detections = []
        for item in data["detections"]:
            item = dict(_exact_fields(item, DetectionEvidence))
            item["bbox_xywh"] = tuple(item["bbox_xywh"])
            if item["tile_origin_xy_px"] is not None:
                item["tile_origin_xy_px"] = tuple(item["tile_origin_xy_px"])
            detections.append(DetectionEvidence(**item))
        data["detections"] = tuple(detections)
        result = PerceptionResult(**data)
        validate_result(result, check_frame=False)
        return result
    except (TypeError, KeyError, ValueError):
        raise InvalidPerception("malformed perception envelope") from None


def ingest_result(store: RoomStore, result: PerceptionResult, *, current_snapshot_version: int) -> IngestOutcome:
    """Validate before a transaction; atomically persist or recover one delivery."""
    validate_result(result)
    if not _integer(current_snapshot_version):
        raise InvalidPerception("invalid current snapshot version")
    request = result.request
    result_json = _json(asdict(result))
    payload_json = _json({
        "status": result.status, "identity_status": result.identity_status,
        "error_category": result.error_category,
        "detections": [_detection_payload(item) for item in result.detections],
    })
    payload_id = _digest((result.provider, result.resolved_model, request.request_digest, request.frame_sha256))
    result_id = _digest((request.session_id, request.observation_id))
    authorized = result.status == "SUCCEEDED" and request.snapshot_version == current_snapshot_version
    with store._lock:
        store._require_open()
        connection = store.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute(
                "SELECT state_version FROM room_sessions WHERE session_id = ?", (request.session_id,),
            ).fetchone()
            if session is None:
                raise InvalidPerception("unknown perception session")
            authorized = authorized and session["state_version"] == current_snapshot_version
            existing = connection.execute(
                "SELECT * FROM room_perception_results WHERE session_id = ? AND observation_id = ?",
                (request.session_id, request.observation_id),
            ).fetchone()
            if existing is not None:
                if existing["result_json"] != result_json:
                    raise IdempotencyConflict("observation has different perception content")
                hypotheses = _hypothesis_ids(connection, result_id)
                connection.commit()
                return IngestOutcome(result_id, existing["payload_id"], True,
                                     authorized and bool(existing["authorizes_current_state"]), hypotheses)
            payload = connection.execute(
                "SELECT payload_json FROM room_perception_payloads WHERE payload_id = ?", (payload_id,),
            ).fetchone()
            if payload is not None and payload["payload_json"] != payload_json:
                raise IdempotencyConflict("provider cache identity has different payload content")
            if payload is None:
                connection.execute(
                    "INSERT INTO room_perception_payloads VALUES (?, ?, ?, ?, ?, ?)",
                    (payload_id, result.provider, result.resolved_model, request.request_digest,
                     request.frame_sha256, payload_json),
                )
            connection.execute(
                "INSERT INTO room_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request.session_id, request.observation_id, request.action_id, request.frame_id,
                 request.run_id, request.snapshot_version, request.request_digest, request.frame_path,
                 request.frame_sha256, request.captured_at, request.pose_revision_id,
                 request.requested_model, request.schema_version, _json(asdict(request))),
            )
            connection.execute(
                "INSERT INTO room_perception_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, request.session_id, request.observation_id, payload_id, result.status,
                 result.provider, result.resolved_model, result.identity_status, result.completed_at,
                 result.error_category, int(authorized), result_json),
            )
            for evidence in result.detections:
                bbox_json = _json(full_frame_bbox(evidence))
                connection.execute(
                    "INSERT INTO room_detection_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (request.session_id, request.observation_id, evidence.detection_id, result_id,
                     evidence.class_name, evidence.confidence_band, evidence.raw_score, evidence.score_kind,
                     _json(evidence.bbox_xywh), evidence.bbox_coordinate_space, evidence.frame_width_px,
                     evidence.frame_height_px, _json(evidence.tile_origin_xy_px), bbox_json, evidence.evidence_sha256),
                )
                hypothesis_id = _digest((request.session_id, request.observation_id, evidence.detection_id))
                connection.execute("INSERT INTO room_object_hypotheses VALUES (?, ?, ?, ?, ?)",
                                   (request.session_id, hypothesis_id, request.observation_id,
                                    evidence.detection_id, evidence.class_name))
                connection.execute("INSERT INTO room_hypothesis_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                                   (request.session_id, hypothesis_id, 1, result_id, request.snapshot_version,
                                    int(authorized), result.completed_at))
            hypotheses = _hypothesis_ids(connection, result_id)
            connection.commit()
            return IngestOutcome(result_id, payload_id, False, authorized, hypotheses)
        except Exception:
            connection.rollback()
            raise


def _hypothesis_ids(connection, result_id) -> tuple[str, ...]:
    return tuple(row[0] for row in connection.execute(
        "SELECT hypothesis_id FROM room_hypothesis_revisions WHERE result_id = ? ORDER BY hypothesis_id",
        (result_id,),
    ))


def append_jsonl_record(path: Path, document) -> None:
    """Publish an entire fsynced replacement under the single-writer process lock.

    The temporary file lives beside the inbox so os.replace is atomic. Readers
    open a fresh snapshot each poll; even a crash during writing leaves only
    the preceding complete file visible. This is a single sidecar writer API,
    not a lock shared between independent writer processes.
    """
    line = canonical_line(document)
    path = Path(path)
    with _INBOX_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_bytes() if path.exists() else b""
        if previous and not previous.endswith(b"\n"):
            raise InvalidPerception("inbox ends with an incomplete record")
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(previous)
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def append_inbox_result(path: Path, result: PerceptionResult) -> None:
    validate_result(result, check_frame=False)
    append_jsonl_record(path, asdict(result))


def _unique_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise InvalidPerception("duplicate JSON field")
        document[key] = value
    return document


def poll_inbox(store: RoomStore, path: Path, *, current_snapshot_version: int,
               wait_s: float = 0, poll_s: float = 0.1,
               monotonic_fn=time.monotonic, sleep_fn=time.sleep) -> tuple[IngestOutcome, ...]:
    """Wait at most wait_s for whole records; retries recover immutable results.

    Partial tails are ignored until a later poll. Malformed complete records
    raise a sanitized error and are never treated as successful observations.
    """
    if not _number(wait_s) or wait_s < 0 or not _number(poll_s) or poll_s <= 0:
        raise ValueError("invalid inbox polling interval")
    deadline = monotonic_fn() + wait_s
    while True:
        with _INBOX_LOCK:
            content = Path(path).read_bytes() if Path(path).exists() else b""
        complete = content[:content.rfind(b"\n") + 1]
        outcomes = []
        for line in complete.splitlines():
            if not line.strip():
                continue
            try:
                result = result_from_dict(json.loads(line, object_pairs_hook=_unique_keys))
            except (ValueError, UnicodeError):
                raise InvalidPerception("malformed inbox record") from None
            outcomes.append(ingest_result(store, result, current_snapshot_version=current_snapshot_version))
        if outcomes:
            return tuple(outcomes)
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            return ()
        sleep_fn(min(poll_s, remaining))
