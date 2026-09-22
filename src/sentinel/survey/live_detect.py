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


def detect_frame(detector, record: dict, classes: tuple[str, ...] = LIVE_CLASSES) -> dict:
    """One frame record from ``frames.jsonl`` -> one result row. Never raises: a
    detector failure is a row with the error named, not a stopped follower."""
    row = {"stop": record.get("stop"), "look_deg": record.get("look_deg"),
           "path": record.get("path"), "sha256": record.get("sha256"),
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
           classes: tuple[str, ...] = LIVE_CLASSES) -> int:
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
            row = detect_frame(detector, json.loads(line), classes)
            with out_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
            print_fn(summarise(row["stop"], row["look_deg"], row["status"], row["detections"],
                               classes)
                     + (f" -- {row['error']}" if row.get("error") else ""))
            handled += 1
        if finished and handled >= len(complete):
            return handled
        sleep_fn(poll_s)
