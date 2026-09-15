"""Tello H.264 packet assembly, decoding, and video UDP reception."""

from datetime import datetime, timezone
import socket
from queue import Queue
from threading import Event, Lock, Thread, current_thread
from typing import Any, Callable, Protocol

from .models import VideoFrame


class FrameDecoder(Protocol):
    def decode(self, payload: bytes) -> list[tuple[int, int]]: ...


class H264FrameAssembler:
    """Collect Tello's 1460-byte chunks until a short packet ends a frame."""

    PACKET_SIZE = 1460

    def __init__(self, decoder: FrameDecoder) -> None:
        self.decoder = decoder
        self._payload = bytearray()

    def push(self, packet: bytes) -> list[tuple[int, int]]:
        self._payload.extend(packet)
        if len(packet) == self.PACKET_SIZE:
            return []

        payload = bytes(self._payload)
        self._payload.clear()
        return self.decoder.decode(payload)

    def reset(self) -> None:
        """Discard the incomplete frame currently being assembled."""

        self._payload.clear()


class PyAVDecoder:
    """Decode H.264 payloads with PyAV, importing it on first use."""

    def __init__(self) -> None:
        self._av: Any | None = None
        self._codec: Any | None = None

    def _ensure_codec(self) -> tuple[Any, Any]:
        if self._codec is None:
            import av

            self._av = av
            self._codec = av.CodecContext.create("h264", "r")
        return self._av, self._codec

    def decode(self, payload: bytes) -> list[tuple[int, int]]:
        av, codec = self._ensure_codec()
        return [
            (frame.width, frame.height)
            for frame in codec.decode(av.Packet(payload))
        ]


class UdpVideoReceiver:
    """Receive and decode one-way Tello video on a selected local adapter."""

    def __init__(
        self,
        local_ip: str,
        port: int = 11111,
        timeout: float = 0.25,
        *,
        socket_factory: Callable[..., Any] = socket.socket,
        decoder_factory: Callable[[], FrameDecoder] = PyAVDecoder,
        assembler_factory: Callable[[FrameDecoder], H264FrameAssembler] = H264FrameAssembler,
    ) -> None:
        self.local_ip = local_ip
        self.port = port
        self.timeout = timeout
        self.frames: Queue[VideoFrame] = Queue()
        self.stop_event = Event()
        self._state_lock = Lock()
        self._started = False
        self._closed = False
        self.last_error: Exception | None = None

        video_socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket = video_socket
        try:
            video_socket.bind((local_ip, port))
            video_socket.settimeout(timeout)
            decoder = decoder_factory()
            self.assembler = assembler_factory(decoder)
        except Exception:
            try:
                video_socket.close()
            finally:
                self.socket = None
            raise

        self.thread = Thread(target=self._receive, daemon=True)

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("video receiver is closed")
            if self._started:
                return
            self._started = True
            self.thread.start()

    def _receive(self) -> None:
        video_socket = self.socket
        if video_socket is None:
            return
        while not self.stop_event.is_set():
            try:
                packet, _ = video_socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if self.stop_event.is_set():
                    return
                return

            try:
                decoded_frames = self.assembler.push(packet)
                for width, height in decoded_frames:
                    self.frames.put(
                        VideoFrame(width, height, datetime.now(timezone.utc))
                    )
            except Exception as exc:
                self.last_error = exc
                reset = getattr(self.assembler, "reset", None)
                if callable(reset):
                    try:
                        reset()
                    except Exception as reset_error:
                        self.last_error = reset_error
                continue

    @property
    def error(self) -> Exception | None:
        """Alias for the most recent packet/decode error, if any."""

        return self.last_error

    def read(self, timeout: float | None = None) -> VideoFrame:
        return self.frames.get(timeout=timeout)

    def close(self) -> None:
        """Close once, clear the socket reference, and stop the receive thread."""

        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self.stop_event.set()
            video_socket = self.socket
            self.socket = None

        if video_socket is not None:
            try:
                video_socket.close()
            except OSError:
                pass
        if self._started and self.thread is not current_thread():
            self.thread.join(timeout=1.0)
