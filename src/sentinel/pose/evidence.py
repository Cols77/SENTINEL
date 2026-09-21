"""Internal diagnostic bindings. Image bytes are never serialized into public facts."""
from dataclasses import dataclass
from sentinel.contracts.pose import DronePoseReport

@dataclass(frozen=True)
class PoseTagEvidence:
    tag_id: int
    corners_xy_normalized: tuple[tuple[float,float],...]
    status: str
    reason_code: str | None
    zone_id: str | None
    reprojection_error_px: float | None

@dataclass(frozen=True)
class PoseEvidenceFrame:
    run_id: str
    report_id: str
    artifact_id: str
    sha256: str
    captured_at: str
    width_px: int
    height_px: int
    source_kind: str
    tags: tuple[PoseTagEvidence,...]

@dataclass(frozen=True)
class PoseEstimate:
    report: DronePoseReport
    evidence: PoseEvidenceFrame
    encoded_png: bytes

@dataclass(frozen=True)
class PoseStreamHealth:
    frames_seen: int = 0
    reports_emitted: int = 0
    no_tag_frames: int = 0
    gated_frames: int = 0
    timeouts: int = 0


class LatestDisplaySink:
    """One pending display update; a stalled consumer cannot stall a pose producer."""
    def __init__(self, callback):
        from queue import Queue
        from threading import Event, Thread
        self.queue=Queue(maxsize=1)
        self.stopped=Event()
        self.failed=False
        self.callback=callback
        self.thread=Thread(target=self._consume,daemon=True,name='sentinel-pose-display')
        self.thread.start()

    @property
    def pending_count(self):
        return self.queue.qsize()

    def push(self, value):
        from queue import Full, Empty
        if self.stopped.is_set() or self.failed:
            return
        try:
            self.queue.put_nowait(value)
        except Full:
            try:self.queue.get_nowait()
            except Empty:pass
            try:self.queue.put_nowait(value)
            except Full:pass

    def _consume(self):
        from queue import Empty
        while not self.stopped.is_set() or not self.queue.empty():
            try:value=self.queue.get(timeout=.1)
            except Empty:continue
            try:self.callback(value)
            except Exception:
                self.failed=True
                return

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=.1)
