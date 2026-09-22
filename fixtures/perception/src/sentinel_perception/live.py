"""Live operator view. Read-only by design.

Writes nothing. No evidence stream, no scene.json, no task-eligible records.
The recording made alongside is the evidence input; the post-flight `analyse`
run over that recording is the only authoritative result. A live detection
that disagrees with post-flight is expected and harmless, because only one of
them was ever evidence.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import cv2
import numpy as np

from .choreography import OnlinePhaseDetector
from .config import DEFAULT
from .detection import clean, deduplicate
from .quality import score_frame
from .render import draw
from .scene import bearing_deg


@dataclass
class LiveResult:
    frame_index: int
    captured_at: float
    finished_at: float
    sector: Optional[int]
    detections: list = field(default_factory=list)

    @property
    def age_s(self) -> float:
        return time.time() - self.captured_at


class DetectionWorker(threading.Thread):
    """Runs the detector on the newest submitted frame, forever.

    One-slot inbox: a new frame overwrites any unprocessed one. The worker
    never builds a backlog, so it is always working on something recent.
    """

    def __init__(self, detector, queries, group_size, width, height, keep_stuff):
        super().__init__(daemon=True)
        self.detector = detector
        self.groups = [queries[i:i + group_size]
                       for i in range(0, len(queries), group_size)]
        self.queries = queries
        self.w, self.h = width, height
        self.keep_stuff = keep_stuff
        self._inbox: Optional[tuple] = None
        self._result: Optional[LiveResult] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.calls = 0

    def submit(self, image, frame_index, sector, captured_at):
        with self._lock:
            self._inbox = (image, frame_index, sector, captured_at)

    def latest(self) -> Optional[LiveResult]:
        with self._lock:
            return self._result

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            with self._lock:
                job, self._inbox = self._inbox, None
            if job is None:
                time.sleep(0.01)
                continue
            image, idx, sector, captured = job
            raw = []
            for g in self.groups:
                res = self.detector.detect_array(image, g, idx, sector or 0)
                if not res.error:
                    raw.extend(res.detections)
            kept, _ = clean(raw, self.w, self.h, self.queries,
                            drop_stuff=not self.keep_stuff)
            self.calls += 1
            with self._lock:
                self._result = LiveResult(idx, captured, time.time(), sector,
                                          deduplicate(kept))


class LiveState:
    """What the HTTP endpoints serve. Latest JPEG plus a status snapshot."""

    def __init__(self):
        self.jpeg: Optional[bytes] = None
        self.status: dict = {}
        self.lock = threading.Lock()


def make_handler(state: LiveState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass                                    # keep the terminal quiet

        def do_GET(self):
            if self.path == "/status":
                with state.lock:
                    body = json.dumps(state.status).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path in ("/", "/stream"):
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        with state.lock:
                            jpg = state.jpeg
                        if jpg:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                            self.wfile.write(jpg)
                            self.wfile.write(b"\r\n")
                        time.sleep(1 / 30)
                except (BrokenPipeError, ConnectionResetError):
                    pass                            # browser tab closed
                return

            self.send_response(404)
            self.end_headers()

    return Handler


def run_live(source, detector, queries, group_size=3, keep_stuff=False,
             port=8080, detect_during_rotate=False, max_age_s=1.5):
    cfg = DEFAULT
    state = LiveState()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"live view : http://127.0.0.1:{port}/")
    print(f"status    : http://127.0.0.1:{port}/status")
    print("read-only : nothing from this session is written as evidence")

    phases = OnlinePhaseDetector()
    worker = None
    prev_small = None
    t_start = time.time()
    shown = dropped_total = 0

    try:
        for fr in source.frames():
            if worker is None:
                h, w = fr.image.shape[:2]
                worker = DetectionWorker(detector, queries, group_size,
                                         w, h, keep_stuff)
                worker.start()

            q, prev_small = score_frame(fr.image, fr.index, fr.t_s,
                                        prev_small, cfg.quality)
            phase, sector, _ = phases.update(q.motion)
            dropped_total += fr.dropped_before

            # Feed the worker only in dwells. Rotation frames are blurred by
            # construction, and a detection finishing 700 ms later would be
            # drawn on a view that has moved on.
            if phase == "DWELL" or detect_during_rotate:
                worker.submit(fr.image, fr.index, sector, fr.wall_time)

            res = worker.latest()
            boxes = []
            stale = True
            if (res and phase == "DWELL" and res.sector == sector
                    and res.age_s <= max_age_s):
                stale = False
                for d in res.detections:
                    cx = (d.box_xyxy[0] + d.box_xyxy[2]) / 2 / fr.image.shape[1]
                    boxes.append((d.query, d.score, d.box_xyxy,
                                  bearing_deg(cx, sector, cfg.choreography.turn_deg,
                                              cfg.camera.hfov_deg),
                                  True))

            flags = ["LIVE - NOT EVIDENCE"]
            if res and not stale:
                flags.append(f"det age {res.age_s:.1f}s")
            out = draw(fr.image, boxes, sector, phase, fr.index, fr.t_s, flags=flags)
            ok, jpg = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 75])
            shown += 1

            elapsed = time.time() - t_start
            with state.lock:
                if ok:
                    state.jpeg = jpg.tobytes()
                state.status = {
                    "provenance": "LIVE_DISPLAY_ONLY",
                    "authoritative": False,
                    "frame": fr.index,
                    "phase": phase,
                    "sector": sector,
                    "rotations_seen": phases.rotations,
                    "display_fps": round(shown / max(elapsed, 1e-6), 1),
                    "detect_hz": round(worker.calls / max(elapsed, 1e-6), 2),
                    "frames_dropped": dropped_total,
                    "detections": [
                        {"label": d.query, "score": round(d.score, 3),
                         "box": list(d.box_xyxy)}
                        for d in (res.detections if res and not stale else [])
                    ],
                    "detection_age_s": round(res.age_s, 2) if res else None,
                }
    except KeyboardInterrupt:
        pass
    finally:
        if worker:
            worker.stop()
        source.close()
        server.shutdown()