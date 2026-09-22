"""Append-only event stream.

Run history must be reconstructable by replaying one file in order: records
are appended, never rewritten, carry a contiguous sequence number, and share a
single run_id. A gap in `seq` means events were lost.

Provenance is mandatory on every record:

    LIVE              captured, or computed from captured data
    SIMULATED         produced by a stub or mock
    REPLAYED          from a previous run's stored data
    OPERATOR_PROVIDED entered by a human
    INFERRED          derived under assumptions -- e.g. bearing from commanded yaw
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

VALID_PROVENANCE = {"LIVE", "SIMULATED", "REPLAYED", "OPERATOR_PROVIDED", "INFERRED"}


def run_stream_path(outdir: str | Path, name: str = "perception") -> Path:
    """Timestamped filename so each run gets its own stream.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(outdir) / f"{name}_{stamp}.jsonl"


class EvidenceWriter:
    def __init__(self, path: str | Path, run_id: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self._seq = 0

        # Backstop for the timestamped filename, which only has one-second
        # resolution. One stream holds exactly one run; refuse at open time
        # rather than silently producing a file that cannot be replayed.
        if self.path.exists() and self.path.stat().st_size > 0:
            with self.path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
            if first_line.strip():
                existing = json.loads(first_line)["run_id"]
                if existing != self.run_id:
                    raise FileExistsError(
                        f"{self.path} already holds run {existing}. "
                        f"An append-only stream holds exactly one run."
                    )
                with self.path.open(encoding="utf-8") as fh:
                    self._seq = sum(1 for line in fh if line.strip())

    def emit(self, event_type: str, provenance: str, payload: dict) -> dict:
        if provenance not in VALID_PROVENANCE:
            raise ValueError(f"invalid provenance {provenance!r}")
        self._seq += 1
        rec = {
            "run_id": self.run_id, "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type, "provenance": provenance, "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        return rec


def replay(path: str | Path) -> list[dict]:
    """Read back a stream, verifying integrity."""
    lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    records = [json.loads(l) for l in lines]
    if not records:
        return []
    if len({r["run_id"] for r in records}) != 1:
        raise ValueError("multiple run_ids in one stream")
    if [r["seq"] for r in records] != list(range(1, len(records) + 1)):
        raise ValueError("sequence gap: events lost or stream corrupted")
    return records