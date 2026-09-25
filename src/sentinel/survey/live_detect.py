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
import atexit
import math
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .consent import ConsentMissing, consent_policy
from .detector import DetectionBatch
from .observability import IMAGE_HEIGHT, IMAGE_WIDTH, NOMINAL_WIDTH_CM
from .store import SurveyStore

#: The survey's classes, the same set the survey resolves (observability).
LIVE_CLASSES = tuple(sorted(NOMINAL_WIDTH_CM))
_DETECTION_FIELDS = frozenset({
    "class", "matched_prompt", "raw_score", "score_kind", "bbox",
    "bbox_coordinate_space", "description", "provider_item_id",
})
_DETECTION_CLASSES = frozenset({"computer", "person", "phone", "mug"})
_SCORE_KINDS = frozenset({"DETECTOR_RAW", "VLM_CONFIDENCE_BAND", "NONE"})
_BANDS = frozenset({"high", "medium", "low"})
_RESULT_STATUSES = frozenset({"OK", "PROVIDER_ERROR", "CLASS_MAP_MISS", "DETECTOR_ERROR"})


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clean_detection(document: object) -> tuple[dict, str, object] | None:
    """Validate a follower document and remove its display-only metadata."""
    if not isinstance(document, dict):
        return None
    band = document.get("band", "low")
    tile = document.get("tile")
    clean = {key: value for key, value in document.items() if key not in {"band", "tile"}}
    if set(clean) != _DETECTION_FIELDS or band not in _BANDS:
        return None
    if clean["class"] not in _DETECTION_CLASSES:
        return None
    if clean["matched_prompt"] is not None and not isinstance(clean["matched_prompt"], str):
        return None
    if clean["raw_score"] is not None and not _number(clean["raw_score"]):
        return None
    if clean["score_kind"] not in _SCORE_KINDS or clean["bbox_coordinate_space"] != "PIXELS_TOP_LEFT":
        return None
    if clean["description"] is not None and not isinstance(clean["description"], str):
        return None
    if clean["provider_item_id"] is not None and not isinstance(clean["provider_item_id"], str):
        return None
    bbox = clean["bbox"]
    if bbox is not None:
        if not isinstance(bbox, dict) or set(bbox) != {"x", "y", "width", "height"}:
            return None
        if not all(_number(bbox[key]) for key in ("x", "y", "width", "height")):
            return None
        if bbox["x"] < 0 or bbox["y"] < 0 or bbox["width"] <= 0 or bbox["height"] <= 0:
            return None
        if bbox["x"] + bbox["width"] > IMAGE_WIDTH \
                or bbox["y"] + bbox["height"] > IMAGE_HEIGHT:
            return None
    if tile is not None and not isinstance(tile, str):
        return None
    return clean, band, tile


def _terminate_process_tree(process, timeout_s: float) -> bool:
    """Terminate a Popen process and descendants, with finite psutil waits."""
    try:
        import psutil
        root = psutil.Process(process.pid)
        members = root.children(recursive=True) + [root]
        for member in members:
            try:
                member.terminate()
            except psutil.NoSuchProcess:
                pass
        _gone, alive = psutil.wait_procs(members, timeout=timeout_s)
        for member in alive:
            try:
                member.kill()
            except psutil.NoSuchProcess:
                pass
        _gone, alive = psutil.wait_procs(alive, timeout=timeout_s)
        return not alive and process.poll() is not None
    except (AttributeError, OSError, ImportError):
        try:
            process.kill()
            process.wait(timeout=timeout_s)
            return process.poll() is not None
        except Exception:
            return False


class LiveDetectRefused(RuntimeError):
    """A gate is not met; the flight must not start with live detection."""


def _frame_key(path: str | Path | None, sha256: str | None) -> tuple[str, str]:
    """Return the stable identity used at both sides of the process boundary.

    The capture directory is unique per sweep, so a resolved path plus the
    bytes hash is sufficient to reject both stale rows and a row for a
    different image that happened to use the same basename.
    """
    return (str(Path(path).resolve()) if path is not None else "", str(sha256 or ""))


@dataclass(frozen=True)
class DetectionCache:
    """Immutable, idempotent view of one follower's JSONL output."""

    run_id: str
    sweep_id: str | None
    strict: bool
    rows: tuple[dict, ...]
    process_status: str = "OK"
    process_error: str | None = None

    def __init__(self, run_id: str, rows: Iterable[dict], process_status: str = "OK",
                 process_error: str | None = None, *, sweep_id: str | None = None,
                 strict: bool = False) -> None:
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "sweep_id", sweep_id)
        # Supplying a sweep identity opts into Mission Control's strict
        # process-boundary contract; standalone callers that omit it keep the
        # original run/path/hash-compatible behavior.
        object.__setattr__(self, "strict", strict or sweep_id is not None)
        object.__setattr__(self, "rows", tuple(dict(row) for row in rows))
        object.__setattr__(self, "process_status", process_status)
        object.__setattr__(self, "process_error", process_error)

    def for_frame(self, frame, classes: tuple[str, ...] = LIVE_CLASSES):
        """Return the cached batch for *frame*, never invoking a provider.

        A result is retained after it is read, making repeated projection or
        retry reads idempotent. Every failure is an explicit non-OK batch;
        callers must not turn it into a zero-count success.
        """
        key = _frame_key(getattr(frame, "image_path", None), getattr(frame, "sha256", None))
        if not key[0] or not key[1]:
            return self._gap("ANALYSIS_GAP_HASH_MISMATCH", "frame identity/hash missing", classes)
        same_path: list[dict] = []
        foreign = False
        for row in self.rows:
            row_key = _frame_key(row.get("path"), row.get("sha256"))
            if row_key[0] == key[0]:
                same_path.append(row)
            if row_key == key:
                if self.strict:
                    ids_match = (row.get("run_id") == self.run_id and
                                 row.get("sweep_id") == self.sweep_id)
                else:
                    ids_match = row.get("run_id") in (None, self.run_id)
                if not ids_match:
                    foreign = True
                    continue
                status = row.get("status")
                error_document = row.get("error")
                if not isinstance(status, str) or status not in _RESULT_STATUSES:
                    return self._gap("ANALYSIS_GAP_INVALID_RESULT",
                                     "status is invalid", classes)
                if error_document is not None and (
                        not isinstance(error_document, str) or not error_document.strip()):
                    return self._gap("ANALYSIS_GAP_INVALID_RESULT",
                                     "error must be a non-empty string", classes)
                if status == "OK" and error_document is not None:
                    return self._gap("ANALYSIS_GAP_INVALID_RESULT",
                                     "OK result cannot include an error", classes)
                raw_document = row.get("raw")
                if raw_document is not None and not isinstance(raw_document, dict):
                    return self._gap("ANALYSIS_GAP_INVALID_RESULT", "raw must be an object", classes)
                raw = dict(raw_document or {})
                for latency in (row.get("detect_s"), raw.get("detect_s")):
                    if latency is not None and (not _number(latency) or latency < 0):
                        return self._gap("ANALYSIS_GAP_INVALID_RESULT", "detect_s is invalid", classes)
                tile_only = raw.get("tile_only")
                if tile_only is not None:
                    if not isinstance(tile_only, list) or any(
                            not isinstance(item, dict)
                            or not isinstance(item.get("index"), int)
                            or isinstance(item.get("index"), bool)
                            or item.get("index") < 0
                            or not isinstance(item.get("tile"), str)
                            for item in tile_only):
                        return self._gap("ANALYSIS_GAP_INVALID_RESULT",
                                         "raw.tile_only is invalid", classes)
                raw.setdefault("detect_s", row.get("detect_s"))
                if row.get("error"):
                    raw.setdefault("error", row["error"])
                documents = row.get("detections")
                if not isinstance(documents, list):
                    return self._gap("ANALYSIS_GAP_INVALID_RESULT",
                                     "detections must be a JSON list", classes)
                if status in {"PROVIDER_ERROR", "DETECTOR_ERROR"} and documents:
                    return self._gap("ANALYSIS_GAP_INVALID_RESULT",
                                     "failure result cannot include detections", classes)
                cleaned = []
                bands = []
                for index, document in enumerate(documents):
                    result = _clean_detection(document)
                    if result is None:
                        return self._gap("ANALYSIS_GAP_INVALID_RESULT",
                                         f"invalid detection at index {index}", classes)
                    detection, band, tile = result
                    cleaned.append(detection)
                    bands.append(band)
                    if tile is not None:
                        raw.setdefault("tile_only", []).append({"index": index, "tile": tile})
                dropped = row.get("dropped", [])
                if not isinstance(dropped, list) or not all(isinstance(label, str) for label in dropped):
                    return self._gap("ANALYSIS_GAP_INVALID_RESULT", "dropped must be string list", classes)
                return DetectionBatch(tuple(cleaned), status,
                                      bands=tuple(bands), dropped=tuple(dropped),
                                      raw=raw)
        if foreign:
            return self._gap("ANALYSIS_GAP_STALE", "cached result belongs to another run", classes)
        if same_path:
            return self._gap("ANALYSIS_GAP_HASH_MISMATCH", "cached frame hash does not match", classes)
        status = self.process_status.upper()
        if status not in {"OK", ""}:
            return self._gap(f"ANALYSIS_GAP_PROCESS_{status}", self.process_error, classes)
        return self._gap("ANALYSIS_GAP_MISSING", "follower produced no row", classes)

    @staticmethod
    def _gap(status: str, error: str | None, classes: tuple[str, ...]):
        return DetectionBatch((), status, bands=(), dropped=(),
                              raw={"error": error or status, "classes": list(classes)})


class LiveDetectionSupervisor:
    """Bound one separate ``live_detect.py`` process to one live sweep.

    The supervisor owns only files and a child process. It is deliberately
    ignorant of flight control: a launch, detector, or drain failure becomes a
    cache gap while the caller remains responsible for the controlled landing.
    """

    def __init__(self, *, root: str | Path, run_id: str, detector_name: str,
                 consent_db: str | Path | None = None, location: str | None = None,
                 date: str | None = None, tiles: str = "none", authorised: bool = True,
                 launcher: Callable | None = None, script: str | Path | None = None,
                 drain_timeout_s: float = 90.0, tree_terminator: Callable | None = None) -> None:
        self.run_id = run_id
        self.detector_name = detector_name
        self.consent_db, self.location, self.date = consent_db, location, date
        self.tiles, self.authorised = tiles, authorised
        self.launcher = launcher or subprocess.Popen
        self.tree_terminator = tree_terminator or _terminate_process_tree
        self.script = Path(script) if script else Path(__file__).resolve().parents[3] / "scripts" / "live_detect.py"
        self.drain_timeout_s = drain_timeout_s
        self.sweep_id = f"{run_id}-{uuid.uuid4().hex}"
        safe_run = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in run_id)
        self.run_dir = Path(root) / "live-detection" / f"{safe_run}-{uuid.uuid4().hex}"
        self.frames_dir = self.run_dir / "frames"
        self.frames_log = self.frames_dir / "frames.jsonl"
        self.out_log = self.run_dir / "detections.jsonl"
        self.done_file = self.run_dir / "flight.done"
        self.process = None
        self.process_status = "NOT_STARTED"
        self.process_error: str | None = None
        self._cleanup_pending = False
        self._cache: DetectionCache | None = None
        atexit.register(self.close)

    def command(self) -> list[str]:
        command = [sys.executable, str(self.script), "--detector", self.detector_name,
                   "--frames-log", str(self.frames_log), "--out", str(self.out_log),
                   "--done-file", str(self.done_file), "--tiles", self.tiles,
                   "--run-id", self.run_id, "--sweep-id", self.sweep_id]
        if self.consent_db is not None:
            command += ["--consent-db", str(self.consent_db)]
        if self.location is not None:
            command += ["--location", self.location]
        if self.date is not None:
            command += ["--date", self.date]
        if self.authorised:
            command.append("--i-authorise-paid-calls")
        return command

    def start(self) -> "LiveDetectionSupervisor":
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            launch_options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                              if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
                              else {"start_new_session": True})
            try:
                self.process = self.launcher(self.command(), **launch_options)
            except TypeError:
                # Injected test launchers and older wrappers may only accept
                # argv; the real Popen path always receives process isolation.
                self.process = self.launcher(self.command())
            self.process_status = "RUNNING"
        except Exception as exc:  # child failure must not affect flight
            self.process_status = "START_FAILED"
            self.process_error = f"{type(exc).__name__}: {exc}"
        return self

    def finish(self, frames: Iterable | None = None) -> DetectionCache:
        if self._cache is not None:
            return self._cache
        self.done_file.parent.mkdir(parents=True, exist_ok=True)
        self.done_file.touch(exist_ok=True)
        if self.process is not None and self.process_status == "RUNNING":
            try:
                code = self.process.wait(timeout=self.drain_timeout_s)
                self.process_status = "OK" if code in (None, 0) else "CRASHED"
                if self.process_status != "OK":
                    self.process_error = f"follower exited {code}"
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                self.process_status, self.process_error = "TIMEOUT", str(exc)
                try:
                    terminated = self.tree_terminator(self.process, self.drain_timeout_s)
                except Exception as tree_error:
                    terminated = False
                    self.process_error = f"{self.process_error}; tree cleanup: {tree_error}"
                try:
                    self.process.wait(timeout=self.drain_timeout_s)
                except (subprocess.TimeoutExpired, TimeoutError):
                    self.process_status = "TIMEOUT_UNREAPED"
                    self.process_error = "follower did not reap after kill"
                except Exception:
                    pass
                if not terminated or self.process.poll() is None:
                    self.process_status = "FATAL_CLEANUP_UNCONFIRMED"
                    self.process_error = "follower process tree remains alive"
                    self._cleanup_pending = True
            except Exception as exc:
                self.process_status = "CRASHED"
                self.process_error = f"{type(exc).__name__}: {exc}"
                try:
                    terminated = self.tree_terminator(self.process, self.drain_timeout_s)
                    self.process.wait(timeout=self.drain_timeout_s)
                except (subprocess.TimeoutExpired, TimeoutError):
                    self.process_status = "TIMEOUT_UNREAPED"
                    self.process_error = "follower did not reap after kill"
                    terminated = False
                except Exception:
                    terminated = False
                if not terminated or self.process.poll() is None:
                    self.process_status = "FATAL_CLEANUP_UNCONFIRMED"
                    self.process_error = "follower process tree remains alive"
                    self._cleanup_pending = True
        elif self.process is None and self.process_status == "NOT_STARTED":
            self.process_status = "START_FAILED"
        rows: list[dict] = []
        if self.out_log.exists():
            for line in self.out_log.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except (json.JSONDecodeError, TypeError):
                    self.process_status = "MALFORMED_OUTPUT"
                    self.process_error = "invalid JSONL row"
        self._cache = DetectionCache(self.run_id, rows, self.process_status,
                                     self.process_error, sweep_id=self.sweep_id, strict=True)
        return self._cache

    def close(self) -> None:
        """Retry ownership of an unconfirmed cleanup without blocking forever."""
        if not self._cleanup_pending or self.process is None:
            return True
        try:
            terminated = self.tree_terminator(self.process, self.drain_timeout_s)
            self.process.wait(timeout=self.drain_timeout_s)
            if terminated and self.process.poll() is not None:
                self._cleanup_pending = False
        except Exception:
            return False
        return not self._cleanup_pending

    @property
    def cleanup_pending(self) -> bool:
        return self._cleanup_pending

    def detection_for(self, frame, classes: tuple[str, ...] = LIVE_CLASSES):
        return self.finish().for_frame(frame, classes)


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
    """``[detect] FAR_L_HI: 1 computer (high), 0 mug, 0 person, 0 phone``."""
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
                 provider: str | None = None,
                 run_id: str | None = None, sweep_id: str | None = None) -> dict:
    """One frame record from ``frames.jsonl`` -> one result row. Never raises: a
    detector failure is a row with the error named, not a stopped follower."""
    row = {"run_id": run_id if run_id is not None else record.get("run_id"),
           "sweep_id": sweep_id if sweep_id is not None else record.get("sweep_id"),
           "stop": record.get("stop"),
           "look_deg": record.get("look_deg"),
           "path": record.get("path"), "sha256": record.get("sha256"),
           "artifact_id": record.get("artifact_id"),
           "captured_at": record.get("captured_at"), "classes": list(classes)}
    if "perception_request" in record:
        return _detect_correlated(detector, record, row, classes, provider=provider)
    started = time.monotonic()
    try:
        batch = detector.detect(record["path"], classes)
        if len(batch.detections) != len(batch.bands):
            raise ValueError("detection and bands lengths differ")
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
           provider: str | None = None, run_id: str | None = None,
           sweep_id: str | None = None) -> int:
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
            row = detect_frame(detector, json.loads(line), classes, provider=provider,
                               run_id=run_id, sweep_id=sweep_id)
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
