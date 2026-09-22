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
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .consent import ConsentMissing, consent_policy
from .detector import DetectionBatch
from .observability import NOMINAL_WIDTH_CM
from .store import SurveyStore

#: The survey's classes, the same set the survey resolves (observability).
LIVE_CLASSES = tuple(sorted(NOMINAL_WIDTH_CM))


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
                raw = dict(row.get("raw") or {})
                raw.setdefault("detect_s", row.get("detect_s"))
                if row.get("error"):
                    raw.setdefault("error", row["error"])
                detections = tuple(row.get("detections") or ())
                bands = tuple(d.get("band", "low") for d in detections)
                return DetectionBatch(detections, str(row.get("status") or "DETECTOR_ERROR"),
                                      bands=bands, dropped=tuple(row.get("dropped") or ()),
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
                 drain_timeout_s: float = 90.0) -> None:
        self.run_id = run_id
        self.detector_name = detector_name
        self.consent_db, self.location, self.date = consent_db, location, date
        self.tiles, self.authorised = tiles, authorised
        self.launcher = launcher or subprocess.Popen
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
        self._cache: DetectionCache | None = None

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
                    self.process.kill()
                except Exception as kill_error:
                    self.process_error = f"{self.process_error}; kill: {kill_error}"
                try:
                    self.process.wait(timeout=self.drain_timeout_s)
                except (subprocess.TimeoutExpired, TimeoutError):
                    self.process_status = "TIMEOUT_UNREAPED"
                    self.process_error = "follower did not reap after kill"
                except Exception:
                    pass
            except Exception as exc:
                self.process_status = "CRASHED"
                self.process_error = f"{type(exc).__name__}: {exc}"
                try:
                    self.process.kill()
                    self.process.wait(timeout=self.drain_timeout_s)
                except (subprocess.TimeoutExpired, TimeoutError):
                    self.process_status = "TIMEOUT_UNREAPED"
                    self.process_error = "follower did not reap after kill"
                except Exception:
                    pass
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


def follow(frames_log: Path, out_log: Path, detector, *,
           done: Callable[[], bool], print_fn: Callable[[str], None] = print,
           sleep_fn: Callable[[float], None] = time.sleep, poll_s: float = 0.25,
           classes: tuple[str, ...] = LIVE_CLASSES, run_id: str | None = None,
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
            row = detect_frame(detector, json.loads(line), classes,
                               run_id=run_id, sweep_id=sweep_id)
            with out_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
            print_fn(summarise(row["stop"], row["look_deg"], row["status"], row["detections"],
                               classes)
                     + (f" -- {row['error']}" if row.get("error") else ""))
            handled += 1
        if finished and handled >= len(complete):
            return handled
        sleep_fn(poll_s)
