"""JSONL serialization for SENTINEL run reports."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .models import RunReport


def _report_dict(report: RunReport) -> dict[str, Any]:
    as_dict = getattr(report, "as_dict", None)
    if callable(as_dict):
        return as_dict()

    if is_dataclass(report):
        payload = asdict(report)
    else:  # pragma: no cover - useful for injected CLI test doubles
        payload = dict(vars(report))

    # Keep compatibility with simple injected test reports.
    payload.setdefault("backend", payload.get("mode"))
    video_frame = payload.get("video_frame")
    if isinstance(video_frame, dict) and "data" in video_frame:
        video_frame["data_bytes"] = len(str(video_frame.pop("data")))
    return payload


def write_report(report: RunReport, path: Path | str) -> None:
    """Write exactly one complete run report as a JSONL record."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(_report_dict(report), handle, default=str, sort_keys=True)
        handle.write("\n")
