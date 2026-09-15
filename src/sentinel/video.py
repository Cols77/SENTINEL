"""Explicitly bound Tello video reception using PyAV's H.264 decoder.

The receiver deliberately gives each UDP datagram to the codec parser.  It
does not infer frame boundaries from packet sizes; H.264/PyAV owns that job.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .models import VideoFrame


class VideoError(RuntimeError):
    """Base exception for video lifecycle, transport, and decode failures."""


class VideoDependencyError(VideoError):
    """Raised when the real PyAV dependency is unavailable."""


class VideoDecoderError(VideoError):
    """Raised when PyAV cannot decode an incoming packet."""


class VideoReadTimeout(VideoError):
    """Raised when no decoded frame arrives before the requested deadline."""


class VideoNotRunning(VideoError):
    """Raised when a frame is requested outside the running lifecycle."""


DecodedVideoFrame = VideoFrame


class VideoReceiver:
    """Receive and decode Tello H.264 datagrams on one selected local IP.

    ``decoder_factory`` and ``packet_factory`` are injectable so contract
    tests can use a fake codec without importing PyAV or opening hardware
    sockets.  In production they default to ``av.CodecContext`` and
    ``av.Packet`` respectively.
    """

    _VALID_PIXEL_FORMATS = {"rgb24", "bgr24"}

    def __init__(
        self,
        *,
        local_ip: str,
        port: int = 11111,
        socket_factory: Callable[..., Any] | None = None,
        decoder_factory: Callable[[], Any] | None = None,
        packet_factory: Callable[[bytes], Any] | None = None,
        pixel_format: str = "rgb24",
        recv_size: int = 65535,
        poll_interval: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not local_ip:
            raise ValueError("local_ip is required for the video receiver")
        if not 1 <= port <= 65535:
            raise ValueError("video port must be between 1 and 65535")
        if pixel_format not in self._VALID_PIXEL_FORMATS:
            raise ValueError("pixel_format must be 'rgb24' or 'bgr24'")

        self.local_ip = local_ip
        self.port = port
        self.pixel_format = pixel_format
        self._socket_factory = socket_factory or socket.socket
        self._decoder_factory = decoder_factory or self._default_decoder
        self._packet_factory = packet_factory or self._default_packet
        self._recv_size = recv_size
        self._poll_interval = poll_interval
        self._clock = clock

        self._socket: Any | None = None
        self._decoder: Any | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._condition = threading.Condition()
        self._frame: DecodedVideoFrame | None = None
        self._last_error: VideoError | None = None
        self._last_frame_at: datetime | None = None
        self._frames_decoded = 0
        self._running = False

    @staticmethod
    def _default_decoder() -> Any:
        try:
            import av
        except ImportError as exc:  # pragma: no cover - depends on runtime env
            raise VideoDependencyError(
                "PyAV is required for real Tello video decoding"
            ) from exc
        return av.CodecContext.create("h264", "r")

    @staticmethod
    def _default_packet(payload: bytes) -> Any:
        try:
            import av
        except ImportError as exc:  # pragma: no cover - depends on runtime env
            raise VideoDependencyError(
                "PyAV is required for real Tello video decoding"
            ) from exc
        return av.Packet(payload)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def bound_address(self) -> tuple[str, int]:
        return (self.local_ip, self.port)

    @property
    def last_error(self) -> VideoError | None:
        return self._last_error

    @property
    def last_frame_at(self) -> datetime | None:
        return self._last_frame_at

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded

    def start(self) -> None:
        """Bind the receiver and start the decoder worker."""

        with self._condition:
            if self._running:
                return
            self._last_error = None
            self._last_frame_at = None
            self._frames_decoded = 0
            self._frame = None
            self._stop_event.clear()

            receiver_socket = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket = receiver_socket
            try:
                set_reuse = getattr(receiver_socket, "setsockopt", None)
                if set_reuse is not None:
                    set_reuse(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                set_timeout = getattr(receiver_socket, "settimeout", None)
                if set_timeout is not None:
                    set_timeout(self._poll_interval)
                receiver_socket.bind(self.bound_address)
                self._decoder = self._decoder_factory()
            except Exception:
                self._close_socket()
                raise

            self._running = True
            self._thread = threading.Thread(
                target=self._receive_loop,
                name="sentinel-tello-video",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop decoding and close the bound receiver, even after failure."""

        with self._condition:
            self._stop_event.set()
            self._close_socket()
            thread = self._thread
            self._running = False
            self._condition.notify_all()

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._poll_interval * 4))
        self._thread = None
        self._decoder = None

    def read(self, timeout: float) -> DecodedVideoFrame:
        """Return the next decoded frame or expose a failure/stall."""

        if timeout < 0:
            raise ValueError("timeout must not be negative")
        deadline = self._clock() + timeout
        with self._condition:
            if not self._running and self._last_error is None:
                raise VideoNotRunning("video receiver is not running")

            while self._frame is None and self._last_error is None and self._running:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)

            if self._last_error is not None:
                raise self._last_error
            if self._frame is not None:
                frame = self._frame
                self._frame = None
                return frame

        raise VideoReadTimeout(
            f"no decoded video frame received within {timeout:.3f}s "
            f"from {self.local_ip}:{self.port}"
        )

    def _close_socket(self) -> None:
        receiver_socket = self._socket
        self._socket = None
        if receiver_socket is not None:
            try:
                receiver_socket.close()
            except OSError:
                pass

    def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            receiver_socket = self._socket
            decoder = self._decoder
            if receiver_socket is None or decoder is None:
                return
            try:
                payload, _address = receiver_socket.recvfrom(self._recv_size)
            except (socket.timeout, TimeoutError):
                continue
            except OSError as exc:
                if not self._stop_event.is_set():
                    self._set_error(VideoError(f"video socket failed: {exc}"))
                return

            try:
                for packet in self._packets_from_payload(decoder, payload):
                    decoded_frames: Iterable[Any] = decoder.decode(packet)
                    for decoded in decoded_frames:
                        frame = self._convert_frame(decoded)
                        with self._condition:
                            self._frame = frame
                            self._last_frame_at = frame.timestamp
                            self._frames_decoded += 1
                            self._condition.notify_all()
            except Exception as exc:
                self._set_error(VideoDecoderError(f"video decoder failed: {exc}"))
                return

    def _packets_from_payload(self, decoder: Any, payload: bytes) -> Iterable[Any]:
        """Use PyAV's H.264 parser to join NAL fragments across UDP packets.

        Test doubles may expose only ``decode``; those use the injected packet
        factory directly.  The production ``CodecContext`` exposes ``parse``
        and therefore receives an elementary-stream parser instead of a
        datagram-size frame-boundary heuristic.
        """

        parse = getattr(decoder, "parse", None)
        if not callable(parse):
            yield self._packet_factory(payload)
            return

        parsed = parse(payload)
        if parsed is None:
            return
        if isinstance(parsed, (bytes, bytearray, memoryview)):
            yield self._packet_factory(bytes(parsed))
            return
        try:
            yield from parsed
        except TypeError:
            yield parsed

    def _convert_frame(self, decoded: Any) -> DecodedVideoFrame:
        try:
            data = decoded.to_ndarray(format=self.pixel_format)
        except AttributeError as exc:
            raise VideoDecoderError(
                "decoded PyAV frame does not expose to_ndarray()"
            ) from exc

        shape = getattr(data, "shape", ())
        height = int(shape[0]) if len(shape) >= 2 else int(decoded.height)
        width = int(shape[1]) if len(shape) >= 2 else int(decoded.width)
        if width <= 0 or height <= 0:
            raise VideoDecoderError(
                f"decoder returned invalid dimensions {width}x{height}"
            )
        return VideoFrame(
            width=width,
            height=height,
            timestamp=datetime.now(timezone.utc),
            pixel_format=self.pixel_format,
            data=data,
        )

    def _set_error(self, error: VideoError) -> None:
        with self._condition:
            self._last_error = error
            self._running = False
            self._condition.notify_all()
