"""Live per-stop detection: what Sentinel sees, reported while it flies.

The flight runner (``cube/mission.py``) appends one line to ``frames.jsonl`` the
moment each frame is saved. This module follows that file from a separate
process and runs a detector on every new frame: one console line per frame
(``[detect] STOP: 1 computer (high), 0 phone``) and one JSONL row per frame
beside the mission log, so the report quotes exactly what the detector said.

It is a separate process on purpose: a detector that raises, hangs or is slow
cannot touch the flight. Nothing here sends a command to the drone.

Gates, checked before takeoff by ``check_gates`` (``scripts/cube_trial.py``
calls it through ``scripts/live_detect.py --check-only``): filming consent
recorded for the location and date (``RETAIN``) and the operator's
``--i-authorise-paid-calls``. Classes are the survey's own.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .consent import ConsentMissing, consent_policy
from .observability import NOMINAL_WIDTH_CM
from .store import SurveyStore

#: The survey's classes, the same set the survey resolves (observability).
LIVE_CLASSES = tuple(sorted(NOMINAL_WIDTH_CM))


class LiveDetectRefused(RuntimeError):
    """A gate is not met; the flight must not start with live detection."""


def check_gates(*, consent_db: Path | None, location: str | None, date: str | None,
                authorised: bool) -> None:
    if not authorised:
        raise LiveDetectRefused("live detection makes paid calls: pass --i-authorise-paid-calls")
    if not (consent_db and location and date):
        raise LiveDetectRefused("live detection sends frames to a provider: give --consent-db, "
                                "--location and --date for recorded filming consent")
    if not Path(consent_db).exists():
        raise LiveDetectRefused(f"no consent database at {consent_db}")
    store = SurveyStore.open(consent_db)
    try:
        consent_policy(store, location, date)
    except ConsentMissing as missing:
        raise LiveDetectRefused(str(missing)) from missing
    finally:
        store.connection.close()


def summarise(stop: str, look_deg, status: str, detections: list[dict],
              classes: tuple[str, ...] = LIVE_CLASSES) -> str:
    """``[detect] FAR_L_HI: 1 computer (high), 0 pen, 0 person, 0 phone``."""
    where = stop if not look_deg else f"{stop} look {look_deg:+d}"
    if status != "OK":
        return f"[detect] {where}: {status} (nothing counted from this frame)"
    parts = []
    for name in classes:
        # a find only an enlarged crop made (sentinel.survey.tiling) is marked "crop"
        bands = [d["band"] + (" crop" if d.get("tile") else "")
                 for d in detections if d["class"] == name]
        parts.append(f"{len(bands)} {name}" + (f" ({', '.join(bands)})" if bands else ""))
    return f"[detect] {where}: " + ", ".join(parts)


def detect_frame(detector, record: dict, classes: tuple[str, ...] = LIVE_CLASSES, *,
                 provider: str | None = None) -> dict:
    """One frame record from ``frames.jsonl`` -> one result row. Never raises: a
    detector failure is a row with the error named, not a stopped follower."""
    row = {"stop": record.get("stop"), "look_deg": record.get("look_deg"),
           "path": record.get("path"), "sha256": record.get("sha256"),
           "captured_at": record.get("captured_at"), "classes": list(classes)}
    if "perception_request" in record:
        return _detect_correlated(detector, record, row, classes, provider=provider)
    started = time.monotonic()
    try:
        batch = detector.detect(record["path"], classes)
        from_crop = {entry["index"]: entry["tile"]
                     for entry in (batch.raw or {}).get("tile_only", [])}
        row.update(status=batch.status,
                   detections=[{**d, "band": b, **({"tile": from_crop[i]} if i in from_crop else {})}
                               for i, (d, b) in enumerate(zip(batch.detections, batch.bands))],
                   dropped=list(batch.dropped), raw=batch.raw)
    except Exception as error:  # noqa: BLE001 -- reported, never raised into the loop
        row.update(status="DETECTOR_ERROR", detections=[], dropped=[], raw=None,
                   error=f"{type(error).__name__}: {error}")
    row["detect_s"] = round(time.monotonic() - started, 2)
    return row


def _reported_models(raw) -> tuple[str | None, ...]:
    """Only response identity counts; a configured detector.model is not proof."""
    if not isinstance(raw, dict):
        return (None,)
    identities = tuple(raw[key] for key in ("resolved_model", "modelVersion", "model") if key in raw)
    if identities:
        return identities
    if "full" in raw:
        answers = [raw["full"], *raw.get("tiles", {}).values()]
    elif "requests" in raw:
        answers = list(raw["requests"].values())
    else:
        return (None,)
    return tuple(model for answer in answers for model in _reported_models(answer)) or (None,)


def _failure_category(error: Exception) -> str:
    from .providers import (
        ModelIdentityMismatch, ProviderAuthError, ProviderRateLimited, ProviderResponseTooLarge,
        ProviderSchemaError, ProviderTimeout, ProviderTransportError,
    )
    for kind, category in (
        (ModelIdentityMismatch, "IDENTITY_MISMATCH"), (ProviderTimeout, "TIMEOUT"),
        (TimeoutError, "TIMEOUT"), (ProviderAuthError, "AUTH"), (ProviderRateLimited, "RATE_LIMIT"),
        (ProviderTransportError, "TRANSPORT"), (ProviderSchemaError, "SCHEMA"),
        (ProviderResponseTooLarge, "RESPONSE_TOO_LARGE"),
    ):
        if isinstance(error, kind):
            return category
    return "DETECTOR_ERROR"


def _detect_correlated(detector, record, row, classes, *, provider):
    from PIL import Image
    from .providers import ModelIdentityMismatch
    from sentinel.room.ingest import (
        DetectionEvidence, InboxRecordTooLarge, InvalidPerception, MAX_IDENTITY_LENGTH,
        PerceptionResult, detection_digest, inbox_result_line, request_from_dict, validate_request,
        validate_result,
    )
    started = time.monotonic()
    row.update(status="DETECTOR_ERROR", detections=[], dropped=[], raw=None)
    try:
        request = request_from_dict(record["perception_request"])
        validate_request(request)
        if record.get("path") != request.frame_path or record.get("sha256") != request.frame_sha256:
            raise InvalidPerception("frame announcement disagrees with request")
    except InvalidPerception:
        row.update(error="INVALID_REQUEST", detect_s=round(time.monotonic() - started, 2))
        return row
    def valid_identity(value):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_IDENTITY_LENGTH

    configured_provider = provider if provider is not None else getattr(detector, "provider", type(detector).__name__)
    provider_name = configured_provider if valid_identity(configured_provider) else "UNKNOWN"
    resolved_model, identity_status, error_category, detections = None, "UNVERIFIED", None, ()
    try:
        if not valid_identity(configured_provider):
            raise InvalidPerception("invalid configured provider identity")
        batch = detector.detect(request.frame_path, classes)
        raw = batch.raw or {}
        reported_provider = raw.get("provider", provider_name)
        if not valid_identity(reported_provider):
            raise InvalidPerception("invalid provider identity")
        provider_name = reported_provider
        models = _reported_models(raw)
        if any(model is not None and not valid_identity(model) for model in models):
            raise InvalidPerception("invalid provider identity")
        if any(model is not None and model != request.requested_model for model in models):
            error_category = "IDENTITY_MISMATCH"
            resolved_model = next(model for model in models if model is not None and model != request.requested_model)
            identity_status = "MISMATCH"
        elif all(model == request.requested_model for model in models):
            resolved_model, identity_status = request.requested_model, "VERIFIED"
        if error_category is None and batch.status != "OK":
            category = raw.get("error_category")
            error_category = category if category in {
                "TIMEOUT", "AUTH", "TRANSPORT", "RATE_LIMIT", "SCHEMA", "RESPONSE_TOO_LARGE",
            } else "PROVIDER_ERROR"
        if error_category is None and identity_status != "VERIFIED":
            error_category = "IDENTITY_UNVERIFIED"
        if error_category is None:
            if len(batch.detections) != len(batch.bands):
                raise InvalidPerception("detection bands are not parallel")
            with Image.open(request.frame_path) as frame:
                width, height = frame.size
            evidence = []
            for index, (detection, band) in enumerate(zip(batch.detections, batch.bands)):
                box = detection["bbox"]
                coordinate_space = detection.get("bbox_coordinate_space", "PIXELS_TOP_LEFT")
                if coordinate_space == "PIXELS_TOP_LEFT":
                    coordinate_space = "FULL_FRAME_PIXELS"
                origin = detection.get("tile_origin_xy_px")
                item = DetectionEvidence(
                    f"{request.observation_id}:{index}", detection["class"], band,
                    detection.get("raw_score"), detection.get("score_kind"),
                    tuple(box[key] for key in ("x", "y", "width", "height")), coordinate_space,
                    width, height, tuple(origin) if origin is not None else None, "",
                )
                evidence.append(replace(item, evidence_sha256=detection_digest(item)))
            detections = tuple(evidence)
        result = PerceptionResult(
            request, "FAILED" if error_category else "SUCCEEDED", provider_name, resolved_model,
            identity_status, datetime.now(timezone.utc).isoformat(), detections, error_category,
        )
        validate_result(result, check_frame=False)
    except ModelIdentityMismatch as error:
        reported = (error.provider, error.requested_model, error.resolved_model)
        if all(value is None for value in reported):
            # Legacy message-only exceptions contain no trustworthy identity.
            resolved_model, identity_status, error_category = None, "UNVERIFIED", "IDENTITY_MISMATCH"
        elif (all(valid_identity(value) for value in reported)
              and error.requested_model == request.requested_model
              and error.resolved_model != error.requested_model):
            provider_name = error.provider
            resolved_model, identity_status, error_category = error.resolved_model, "MISMATCH", "IDENTITY_MISMATCH"
        else:
            resolved_model, identity_status, error_category = None, "UNVERIFIED", "SCHEMA"
        result = PerceptionResult(
            request, "FAILED", provider_name, resolved_model, identity_status,
            datetime.now(timezone.utc).isoformat(), (), error_category,
        )
    except Exception as error:  # never expose provider text, headers, or credentials
        result = PerceptionResult(
            request, "FAILED", provider_name, None, "UNVERIFIED",
            datetime.now(timezone.utc).isoformat(), (),
            "SCHEMA" if isinstance(error, (InvalidPerception, KeyError, TypeError, ValueError))
            else _failure_category(error),
        )
    # The fallback follows the same contract as a provider response; no invalid
    # identity survives into an envelope merely because its first validation failed.
    try:
        inbox_result_line(result)
    except InboxRecordTooLarge:
        # Identity fields are individually bounded by the request contract. A
        # timestamp may have arbitrarily many fractional digits, so canonicalize
        # that representation while preserving the capture instant and IDs.
        bounded_request = replace(request, captured_at=datetime.fromisoformat(request.captured_at).isoformat())
        result = PerceptionResult(
            bounded_request, "FAILED", provider_name, None, "UNVERIFIED",
            datetime.now(timezone.utc).isoformat(), (), "RESPONSE_TOO_LARGE",
        )
        inbox_result_line(result)
    row["perception_result"] = asdict(result)
    row["detect_s"] = round(time.monotonic() - started, 2)
    if result.status == "SUCCEEDED":
        row.update(status="OK", detections=[{
            "class": item.class_name, "band": item.confidence_band,
            "raw_score": item.raw_score, "score_kind": item.score_kind,
            "bbox": dict(zip(("x", "y", "width", "height"), item.bbox_xywh)),
        } for item in result.detections])
    else:
        row["error"] = result.error_category
    return row


def follow(frames_log: Path, out_log: Path, detector, *,
           done: Callable[[], bool], print_fn: Callable[[str], None] = print,
           sleep_fn: Callable[[float], None] = time.sleep, poll_s: float = 0.25,
           classes: tuple[str, ...] = LIVE_CLASSES, inbox_log: Path | None = None,
           provider: str | None = None) -> int:
    """Detect on every line appended to ``frames_log`` until ``done()`` is true and
    every line has been handled. Returns how many frames were detected."""
    handled = 0
    out_log.parent.mkdir(parents=True, exist_ok=True)
    while True:
        finished = done()   # read BEFORE the file, so a last line written just before is seen
        text = frames_log.read_text(encoding="utf-8") if frames_log.exists() else ""
        # only whole lines: the last one may still be being written
        complete = [line for line in text[:text.rfind("\n") + 1].splitlines() if line.strip()]
        for line in complete[handled:]:
            row = detect_frame(detector, json.loads(line), classes, provider=provider)
            if inbox_log is not None and "perception_result" in row:
                from sentinel.room.ingest import append_inbox_result, result_from_dict
                append_inbox_result(inbox_log, result_from_dict(row["perception_result"]))
            with out_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
            print_fn(summarise(row["stop"], row["look_deg"], row["status"], row["detections"],
                               classes)
                     + (f" -- {row['error']}" if row.get("error") else ""))
            handled += 1
        if finished and handled >= len(complete):
            return handled
        sleep_fn(poll_s)
