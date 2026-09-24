"""Frame sources: where frames come from, and whether you can go back.

Three sources, one interface:

  FileSource       a recording, every frame, as fast as it decodes.
                   Rewindable: the post-flight pipeline can make multiple passes.

  PacedFileSource  a recording replayed at its native frame rate, dropping
                   frames when the consumer falls behind. Behaves like a live
                   stream, so the live view can be developed and demonstrated
                   against recorded footage with realistic timing. Running at
                   disk speed instead would hide every latency problem.

  StreamSource     a live feed (e.g. Tello UDP), decoded on a background thread
                   into a one-slot buffer, optionally recorded to disk.

`rewindable` is what callers branch on. Anything needing the whole run -- the
derived rotate/dwell threshold, best frame per dwell, loop closure, loop dedup
-- requires a rewindable source. Non-rewindable sources feed the live view only.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Protocol

import cv2
import numpy as np

from .video import iter_frames, probe


@dataclass
class Frame:
    index: int                 # position in the source, counting dropped frames
    t_s: float                 # seconds from source start, by frame index
    image: np.ndarray          # BGR
    wall_time: float           # epoch seconds when this frame became available
    dropped_before: int = 0    # frames skipped since the previous delivery


class FrameSource(Protocol):
    rewindable: bool
    fps: float

    def frames(self) -> Iterator[Frame]: ...
    def close(self) -> None: ...


class FileSource:
    """A recorded file. Every frame, no pacing."""

    rewindable = True

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.fps = probe(self.path).fps or 30.0

    def frames(self) -> Iterator[Frame]:
        # Reuses video.iter_frames so there is exactly one decode path in the
        # codebase. Two decoders would eventually disagree about frame indices
        # on a stream with corrupt macroblocks, which this footage has.
        for i, img in iter_frames(self.path):
            yield Frame(i, i / self.fps, img, time.time())

    def close(self) -> None:
        pass


class PacedFileSource(FileSource):
    """A recording replayed in real time, dropping frames like a live feed.

    Declared non-rewindable even though the file underneath is: the point is
    to behave like a stream, and code that accidentally relies on rewinding
    should fail here in testing rather than in the air.
    """

    rewindable = False

    def __init__(self, path: str | Path, speed: float = 1.0):
        super().__init__(path)
        self.speed = speed

    def frames(self) -> Iterator[Frame]:
        t0 = time.time()
        last_yield = -1
        for f in super().frames():
            due = t0 + f.t_s / self.speed
            now = time.time()
            if now > due + 1.0 / self.fps:
                # The consumer is more than a frame behind. A live stream would
                # have overwritten this frame already, so drop it.
                continue
            if due > now:
                time.sleep(due - now)
            f.dropped_before = f.index - last_yield - 1
            f.wall_time = time.time()
            last_yield = f.index
            yield f

    @property
    def stats(self) -> dict:
        return {"source": "paced_file", "path": str(self.path), "speed": self.speed}


class StreamSource:
    """A live feed, decoded on a background thread into a one-slot buffer.

    Recording happens on the reader thread, before the frame reaches the
    buffer, so a slow consumer can never cost a frame of the recording.
    """

    rewindable = False

    def __init__(self, url: str, record_to: Optional[str | Path] = None,
                 fps: float = 30.0, max_read_failures: int = 300):
        self.url = url
        self.record_to = Path(record_to) if record_to else None
        self.fps = fps
        # UDP read failures are usually packet loss, so a failed read is not
        # end-of-stream. But a source that never recovers should end rather
        # than spin forever: 300 consecutive failures is ~3 s of nothing.
        self.max_read_failures = max_read_failures

        self._slot: queue.Queue[Frame] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._ended = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._decoded = 0
        self._dropped = 0
        self._read_failures = 0

    def _publish(self, frame: Frame) -> None:
        try:
            self._slot.put_nowait(frame)
            return
        except queue.Full:
            pass
        try:
            self._slot.get_nowait()                 # discard the stale frame
            self._dropped += 1
        except queue.Empty:
            pass
        try:
            self._slot.put_nowait(frame)
        except queue.Full:
            self._dropped += 1

    def _reader(self) -> None:
        cap = cv2.VideoCapture(self.url)
        writer = None
        consecutive_failures = 0
        try:
            while not self._stop.is_set():
                ok, img = cap.read()
                if not ok:
                    self._read_failures += 1
                    consecutive_failures += 1
                    if consecutive_failures >= self.max_read_failures:
                        break
                    time.sleep(0.01)
                    continue
                consecutive_failures = 0

                if self.record_to is not None:
                    if writer is None:
                        self.record_to.parent.mkdir(parents=True, exist_ok=True)
                        h, w = img.shape[:2]
                        writer = cv2.VideoWriter(
                            str(self.record_to),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            self.fps, (w, h))
                    writer.write(img)

                self._publish(Frame(self._decoded, self._decoded / self.fps,
                                    img, time.time()))
                self._decoded += 1
        finally:
            cap.release()
            if writer is not None:
                writer.release()
            self._ended.set()

    def frames(self) -> Iterator[Frame]:
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        seen_dropped = 0
        while not self._stop.is_set():
            try:
                f = self._slot.get(timeout=0.5)
            except queue.Empty:
                if self._ended.is_set():
                    return                          # reader finished, buffer empty
                continue
            f.dropped_before = self._dropped - seen_dropped
            seen_dropped = self._dropped
            yield f

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    @property
    def stats(self) -> dict:
        return {
            "source": "stream", "url": self.url,
            "decoded": self._decoded, "dropped": self._dropped,
            "drop_rate": round(self._dropped / max(1, self._decoded), 4),
            "read_failures": self._read_failures,
            "recording": str(self.record_to) if self.record_to else None,
        }


def tello_source(record_to: str | Path | None = None) -> StreamSource:
    """Tello H.264 over UDP. The drone must have received `streamon` first.

    Only one process can bind port 11111. If the SENTINEL controller is already
    consuming the stream through DJITelloPy, this will not receive frames -- the
    live view must read from the controller's decoder instead.
    """
    return StreamSource("udp://0.0.0.0:11111", record_to=record_to)