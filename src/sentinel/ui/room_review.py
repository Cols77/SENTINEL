"""Current-run recorded review; isolated from flight control and live perception."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Callable


class RoomReview:
    def __init__(self, run_dir: str | Path, project: str | Path,
                 *, launch: Callable = subprocess.Popen):
        self.run_dir = Path(run_dir)
        self.project = Path(project)
        self.launch = launch
        self.process = None
        self._recording = None
        self._start_lock = threading.Lock()

    @property
    def source(self) -> Path:
        return self.run_dir / "flight.h264"

    @property
    def output_dir(self) -> Path:
        return self.run_dir / "review"

    def _python(self) -> Path:
        return self.project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def recording(self) -> dict:
        if self._recording is not None:
            return self._recording
        source = self.source
        meta_path = self.run_dir / "recording.json"
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        if not source.is_file() or source.stat().st_size == 0:
            return {"status": "MISSING", "error": metadata.get("error")}
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        status = "FAILED" if metadata.get("error") else "READY"
        self._recording = {"status": status, "bytes": source.stat().st_size,
                           "sha256": digest.hexdigest(), "error": metadata.get("error")}
        return self._recording

    def _setup_reason(self) -> str | None:
        try:
            manifest = json.loads((self.project / "models" / "manifest.json").read_text(encoding="utf-8"))
            detector = manifest["detector_openvocab"]
            if not detector.get("id") or not detector.get("sha"):
                raise ValueError("detector identity missing")
        except (OSError, ValueError, KeyError, TypeError):
            return "MODEL_NOT_STAGED"
        if not self._python().is_file():
            return "PERCEPTION_ENV_NOT_STAGED"
        return None

    def status(self) -> dict:
        recording = self.recording()
        if (self.output_dir / "review.json").is_file() and (self.output_dir / "annotated.mp4").is_file():
            analysis = {"status": "COMPLETE"}
        elif self.process is not None:
            code = self.process.poll()
            analysis = {"status": "RUNNING" if code is None else "FAILED"}
            if code is not None:
                analysis["reason_code"] = (f"ANALYSIS_EXIT_{code}" if code
                                           else "ANALYSIS_OUTPUT_MISSING")
                log = self.output_dir / "analysis.log"
                if log.is_file():
                    with log.open("rb") as stream:
                        stream.seek(max(0, log.stat().st_size - 4096))
                        lines = stream.read().decode("utf-8", errors="replace").splitlines()
                    if lines:
                        analysis["detail"] = lines[-1][:300]
        elif recording["status"] != "READY":
            analysis = {"status": "UNAVAILABLE", "reason_code": "RECORDING_UNAVAILABLE"}
        elif reason := self._setup_reason():
            analysis = {"status": "UNAVAILABLE", "reason_code": reason}
        else:
            analysis = {"status": "READY"}
        return {"recording": recording, "analysis": analysis}

    def start(self) -> dict:
        with self._start_lock:
            state = self.status()
            if state["recording"]["status"] != "READY":
                return {"status": "UNAVAILABLE", "reason_code": "RECORDING_UNAVAILABLE"}
            if self.process is not None or state["analysis"]["status"] == "COMPLETE":
                return {"status": "CONFLICT", "reason_code": "ANALYSIS_ALREADY_STARTED"}
            if reason := self._setup_reason():
                return {"status": "UNAVAILABLE", "reason_code": reason}
            self.output_dir.mkdir(parents=True, exist_ok=True)
            command = [str(self._python()), "-m", "sentinel_perception.review", str(self.source),
                       "--outdir", str(self.output_dir)]
            environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            try:
                with (self.output_dir / "analysis.log").open("wb") as log:
                    self.process = self.launch(command, cwd=self.project, stdout=log,
                                               stderr=subprocess.STDOUT, env=environment)
            except OSError as exc:
                return {"status": "UNAVAILABLE", "reason_code": "ANALYSIS_LAUNCH_FAILED",
                        "detail": str(exc)}
            return {"status": "RUNNING"}

    def result(self) -> dict | None:
        if self.status()["analysis"]["status"] != "COMPLETE":
            return None
        return json.loads((self.output_dir / "review.json").read_text(encoding="utf-8"))

    def video_path(self) -> Path | None:
        return (self.output_dir / "annotated.mp4"
                if self.status()["analysis"]["status"] == "COMPLETE" else None)
