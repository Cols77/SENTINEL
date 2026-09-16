"""JSONL serialization for SENTINEL run reports."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import RunReport


class ProgressLogger:
    """Flush human-readable and JSONL diagnostics while a run is active."""

    def __init__(
        self,
        path: Path | str,
        *,
        console: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")
        self._console = console or (lambda message: print(message, flush=True))

    def emit(self, event: str, message: str, **fields: Any) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "message": message,
            **fields,
        }
        json.dump(payload, self._handle, default=str, sort_keys=True)
        self._handle.write("\n")
        self._handle.flush()
        self._console(f"[{event}] {message}")

    def close(self) -> None:
        self._handle.close()


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


def write_video_manifest(
    video_path: Path | str,
    report: RunReport,
    path: Path | str | None = None,
) -> Path:
    """Write reproducibility metadata for an explicitly recorded H.264 stream."""

    video = Path(video_path)
    if not video.is_file():
        raise FileNotFoundError(video)
    digest = hashlib.sha256()
    with video.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    frame = report.video_frame
    manifest = {
        "schema_version": "sentinel.perception.video.v1",
        "video_path": video.as_posix(),
        "format": "h264-annex-b",
        "size_bytes": video.stat().st_size,
        "sha256": digest.hexdigest(),
        "source": {
            "device": "DJI Tello",
            "sdk_documentation": "https://dl-cdn.ryzerobotics.com/downloads/tello/20180910/Tello%20SDK%20Documentation%20EN_1.3.pdf",
            "video_port": 11111,
        },
        "decoded_frame_probe": (
            frame.as_dict() if frame is not None else None
        ),
        "run_status": report.status,
    }
    output = Path(path) if path is not None else video.with_suffix(".manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
