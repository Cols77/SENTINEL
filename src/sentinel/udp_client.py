"""Real UDP command and diagnostic state transport for a DJI Tello."""

from collections.abc import Callable
from datetime import datetime, timezone
import socket
from queue import Empty
from threading import Event, Lock, RLock, Thread, current_thread
from typing import Any, Final

from .client import DroneError, EmergencyAborted
from .models import CommandRecord, VideoFrame
from .video import UdpVideoReceiver


MOVEMENT_DIRECTIONS: Final = frozenset(
    {"forward", "back", "left", "right", "up", "down"}
)
ROTATION_DIRECTIONS: Final = frozenset({"cw", "ccw"})


def format_move(direction: str, distance_cm: int) -> str:
    if direction not in MOVEMENT_DIRECTIONS:
        raise ValueError(f"unsupported movement direction: {direction}")
    if (
        isinstance(distance_cm, bool)
        or not isinstance(distance_cm, int)
        or not 20 <= distance_cm <= 500
    ):
        raise ValueError("Tello movement distance must be between 20 and 500 cm")
    return f"{direction} {distance_cm}"


def format_rotate(direction: str, degrees: int) -> str:
    if direction not in ROTATION_DIRECTIONS:
        raise ValueError(f"unsupported rotation direction: {direction}")
    if (
        isinstance(degrees, bool)
        or not isinstance(degrees, int)
        or not 1 <= degrees <= 360
    ):
        raise ValueError("Tello rotation must be between 1 and 360 degrees")
    return f"{direction} {degrees}"


def parse_response(response: bytes) -> str:
    return response.decode("utf-8", errors="replace").strip().lower()


def parse_state(payload: bytes | str) -> dict[str, str]:
    """Parse one Tello state datagram into a normalized string dictionary."""

    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace")
    else:
        text = payload

    state: dict[str, str] = {}
    for field in text.split(";"):
        if ":" not in field:
            continue
        key, value = field.split(":", 1)
        key = key.strip().lower()
        if key:
            state[key] = value.strip()
    return state


class TelloStateListener:
    """Best-effort diagnostic listener for Tello's UDP state stream."""

    def __init__(
        self,
        local_ip: str,
        port: int = 8890,
        timeout: float = 0.25,
        *,
        socket_factory: Callable[..., Any] = socket.socket,
    ) -> None:
        self.local_ip = local_ip
        self.port = port
        self.timeout = timeout
        self.stop_event = Event()
        self._state_lock = Lock()
        self._started = False
        self._closed = False

        state_socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket = state_socket
        try:
            state_socket.bind((local_ip, port))
            state_socket.settimeout(timeout)
        except Exception:
            try:
                state_socket.close()
            finally:
                self.socket = None
            raise

        self._latest_state: dict[str, str] = {}
        self.thread = Thread(target=self._receive, daemon=True)

    @property
    def latest_state(self) -> dict[str, str]:
        with self._state_lock:
            return dict(self._latest_state)

    @property
    def state(self) -> dict[str, str]:
        return self.latest_state

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("state listener is closed")
            if self._started:
                return
            self._started = True
            self.thread.start()

    def _receive(self) -> None:
        state_socket = self.socket
        if state_socket is None:
            return
        while not self.stop_event.is_set():
            try:
                payload, _ = state_socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if self.stop_event.is_set():
                    return
                return

            parsed = parse_state(payload)
            if parsed:
                with self._state_lock:
                    self._latest_state = parsed

    def close(self) -> None:
        """Close once, clear the socket reference, and stop the listener thread."""

        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self.stop_event.set()
            state_socket = self.socket
            self.socket = None

        if state_socket is not None:
            try:
                state_socket.close()
            except OSError:
                pass
        if self._started and self.thread is not current_thread():
            self.thread.join(timeout=1.0)


def _require_ok(response: str, command: str) -> str:
    if response != "ok":
        raise DroneError(f"Tello rejected {command!r}: {response}")
    return response


def _parse_int(response: str, command: str) -> int:
    try:
        return int(response)
    except (TypeError, ValueError) as exc:
        raise DroneError(
            f"Tello returned a non-integer for {command!r}: {response}"
        ) from exc


class UdpTelloClient:
    """Thread-safe Tello SDK client bound to one explicit local IPv4 address."""

    def __init__(
        self,
        local_ip: str,
        tello_ip: str = "192.168.10.1",
        timeout: float = 7.0,
        socket_factory: Callable[..., Any] = socket.socket,
        video_factory: Callable[[str], Any] = UdpVideoReceiver,
        state_listener_factory: Callable[..., Any] = TelloStateListener,
    ) -> None:
        self.local_ip = local_ip
        self.tello_address = (tello_ip, 8889)
        self.timeout = timeout
        self.socket_factory = socket_factory
        self.video_factory = video_factory
        self.state_listener_factory = state_listener_factory
        self.socket: Any | None = None
        self.video: Any | None = None
        self.state_listener: Any | None = None
        self.airborne = False
        self.emergency_triggered = False
        self.records: list[CommandRecord] = []
        self._lock = RLock()
        self._connected = False

    @property
    def latest_state(self) -> dict[str, str]:
        listener = self.state_listener
        if listener is None:
            return {}
        return dict(listener.latest_state)

    def connect(self) -> None:
        with self._lock:
            if self.emergency_triggered:
                raise EmergencyAborted("commands are disabled after emergency stop")
            if self.socket is not None:
                raise DroneError("Tello client is already connected")
            command_socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket = command_socket
            try:
                command_socket.bind((self.local_ip, 0))
                command_socket.settimeout(self.timeout)
                if self.state_listener_factory is not None:
                    listener = self.state_listener_factory(
                        self.local_ip,
                        socket_factory=self.socket_factory,
                    )
                    self.state_listener = listener
                    listener.start()
                _require_ok(self.send("command"), "command")
                self._connected = True
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            video = self.video
            self.video = None
            listener = self.state_listener
            self.state_listener = None
            command_socket = self.socket
            self.socket = None
            self._connected = False

        if video is not None:
            try:
                video.close()
            except Exception:
                pass
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass
        if command_socket is not None:
            try:
                command_socket.close()
            except Exception:
                pass

    def send(self, command: str) -> str:
        with self._lock:
            if self.emergency_triggered:
                raise EmergencyAborted("commands are disabled after emergency stop")
            if self.socket is None:
                raise DroneError("Tello client is not connected")
            self.socket.sendto(command.encode("utf-8"), self.tello_address)
            payload, _ = self.socket.recvfrom(3000)
            response = parse_response(payload)
            self.records.append(
                CommandRecord(
                    command=command,
                    response=response,
                    position=None,
                    timestamp=datetime.now(timezone.utc),
                )
            )
            return response

    def battery(self) -> int:
        return _parse_int(self.send("battery?"), "battery?")

    def height_cm(self) -> int:
        return _parse_int(self.send("height?"), "height?")

    def speed_cm_per_second(self) -> int:
        return _parse_int(self.send("speed?"), "speed?")

    def sdk_version(self) -> str:
        return self.send("sdk?")

    def set_speed(self, cm_per_second: int) -> str:
        if (
            isinstance(cm_per_second, bool)
            or not isinstance(cm_per_second, int)
            or not 10 <= cm_per_second <= 100
        ):
            raise ValueError("Tello speed must be between 10 and 100 cm/s")
        command = f"speed {cm_per_second}"
        return _require_ok(self.send(command), command)

    def takeoff(self) -> str:
        response = _require_ok(self.send("takeoff"), "takeoff")
        self.airborne = True
        return response

    def land(self) -> str:
        response = _require_ok(self.send("land"), "land")
        self.airborne = False
        return response

    def move(self, direction: str, distance_cm: int) -> str:
        command = format_move(direction, distance_cm)
        return _require_ok(self.send(command), command)

    def rotate(self, direction: str, degrees: int) -> str:
        command = format_rotate(direction, degrees)
        return _require_ok(self.send(command), command)

    def stop(self) -> str:
        return _require_ok(self.send("stop"), "stop")

    def stream_on(self) -> str:
        with self._lock:
            if self.video is not None:
                raise DroneError("video stream is already active")
            video = self.video_factory(self.local_ip)
            self.video = video
            try:
                video.start()
                return _require_ok(self.send("streamon"), "streamon")
            except Exception:
                self.video = None
                try:
                    video.close()
                except Exception:
                    pass
                raise

    def stream_off(self) -> str:
        with self._lock:
            video = self.video
            self.video = None
            try:
                return _require_ok(self.send("streamoff"), "streamoff")
            finally:
                if video is not None:
                    try:
                        video.close()
                    except Exception:
                        pass

    def read_frame(self, timeout: float) -> VideoFrame:
        video = self.video
        if video is None:
            raise DroneError("video stream is not active")
        try:
            return video.read(timeout)
        except Empty as exc:
            raise DroneError("no decodable video frame arrived before timeout") from exc

    def emergency(self) -> str:
        with self._lock:
            if self.emergency_triggered:
                return "ok"
            if self.socket is None:
                raise DroneError("Tello client is not connected")
            self.socket.sendto(b"emergency", self.tello_address)
            self.emergency_triggered = True
            self.records.append(
                CommandRecord(
                    command="emergency",
                    response="ok",
                    position=None,
                    timestamp=datetime.now(timezone.utc),
                )
            )
            return "ok"
