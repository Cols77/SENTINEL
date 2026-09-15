"""SENTINEL's narrow DJITelloPy compatibility boundary.

DJITelloPy 2.5.0 keeps command and state sockets in process-global receiver
threads and binds them to wildcard addresses.  This adapter supplies a
short-lived socket compatibility layer while the Tello object is constructed,
and supplies the same explicit binding to the dedicated video receiver.
"""

from __future__ import annotations

import inspect
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

from .video import VideoReceiver


try:  # The Task 1 client boundary is supplied by the surrounding branch.
    from .client import DroneError as _DroneError
    from .client import EmergencyAborted
except ImportError:  # pragma: no cover - used only before Task 1 lands
    class _DroneError(RuntimeError):
        """Fallback base exception for isolated Task 3 tests."""

    class EmergencyAborted(_DroneError):
        """Raised after the emergency command has latched."""


class DjitellopyBackendError(_DroneError):
    """Raised when the DJITelloPy boundary cannot perform an operation."""


class DjitellopyDependencyError(DjitellopyBackendError):
    """Raised when DJITelloPy is missing from a real-mode environment."""


@dataclass(frozen=True)
class SocketBinding:
    """An observed explicit bind made through the compatibility boundary."""

    local_ip: str
    port: int


_DJITELLOPY_FACTORY_LOCK = threading.Lock()


class _BoundSocket:
    def __init__(
        self,
        raw_socket: Any,
        local_ip: str,
        bindings: list[SocketBinding],
        condition: threading.Condition,
    ) -> None:
        self._raw_socket = raw_socket
        self._local_ip = local_ip
        self._bindings = bindings
        self._condition = condition

    def bind(self, address: tuple[str, int]) -> Any:
        host, port = address
        if host in ("", "0.0.0.0", "::"):
            address = (self._local_ip, port)
        result = self._raw_socket.bind(address)
        with self._condition:
            self._bindings.append(SocketBinding(address[0], address[1]))
            self._condition.notify_all()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_socket, name)


class BoundSocketFactory:
    """Wrap socket creation and rewrite wildcard binds to one local IPv4."""

    def __init__(
        self,
        local_ip: str,
        socket_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not local_ip:
            raise ValueError("local_ip is required for socket binding")
        self.local_ip = local_ip
        self._socket_factory = socket_factory or socket.socket
        self.bindings: list[SocketBinding] = []
        self._condition = threading.Condition()

    def __call__(self, *args: Any, **kwargs: Any) -> _BoundSocket:
        raw_socket = self._socket_factory(*args, **kwargs)
        return _BoundSocket(raw_socket, self.local_ip, self.bindings, self._condition)

    def wait_for_ports(self, ports: set[int], timeout: float) -> bool:
        deadline = __import__("time").monotonic() + timeout
        with self._condition:
            while not ports.issubset({item.port for item in self.bindings}):
                remaining = deadline - __import__("time").monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True


def _default_tello_factory(
    *,
    local_ip: str,
    tello_ip: str,
    video_port: int,
    retry_count: int,
    socket_factory: BoundSocketFactory,
    timeout: float = 7.0,
) -> Any:
    """Construct DJITelloPy while its receiver threads use bound sockets."""

    try:
        from djitellopy import Tello
        import djitellopy.tello as tello_module
    except ImportError as exc:  # pragma: no cover - depends on runtime env
        raise DjitellopyDependencyError(
            "DJITelloPy 2.5.0 is required for real Tello control"
        ) from exc

    with _DJITELLOPY_FACTORY_LOCK:
        # DJITelloPy 2.5.0 owns process-global command/state receivers and
        # does not provide a way to rebind them after initialization.  A
        # second real client would silently use the first adapter, so fail
        # closed and require a fresh process for every real run.
        if getattr(tello_module, "threads_initialized", False):
            raise DjitellopyBackendError(
                "DJITelloPy process-global receivers are already initialized; "
                "run SENTINEL real mode in a fresh process"
            )

        previous_socket_factory = tello_module.socket.socket
        tello_module.socket.socket = socket_factory
        try:
            tello = Tello(
                host=tello_ip,
                retry_count=retry_count,
                vs_udp=video_port,
            )
            if not socket_factory.wait_for_ports({8889, 8890}, timeout=2.0):
                raise DjitellopyBackendError(
                    f"DJITelloPy did not bind command/state sockets to {local_ip}"
                )
            # Keep the requested timeout available to adapter callers.  The
            # upstream high-level methods retain their own takeoff timeout.
            tello.RESPONSE_TIMEOUT = timeout
            return tello
        finally:
            tello_module.socket.socket = previous_socket_factory


class DjitellopyClient:
    """Implement the SENTINEL ``DroneClient`` contract with DJITelloPy."""

    backend_name = "djitellopy-2.5.0"

    def __init__(
        self,
        *,
        local_ip: str,
        tello_ip: str = "192.168.10.1",
        video_port: int = 11111,
        retry_count: int = 3,
        timeout: float = 7.0,
        pixel_format: str = "rgb24",
        tello_factory: Callable[..., Any] | None = None,
        socket_factory: Callable[..., Any] | None = None,
        video_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.local_ip = local_ip
        self.tello_ip = tello_ip
        self.video_port = video_port
        self.retry_count = retry_count
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.pixel_format = pixel_format
        self._tello_factory = tello_factory or _default_tello_factory
        self._socket_factory = socket_factory or socket.socket
        self._video_factory = video_factory or VideoReceiver

        self._tello: Any | None = None
        self._video: Any | None = None
        self._bound_sockets: BoundSocketFactory | None = None
        self._connected = False
        self._streaming = False
        self._closed = False
        self._emergency_triggered = False
        self._command_lock = threading.RLock()
        self.records: list[Any] = []

    @property
    def emergency_triggered(self) -> bool:
        return self._emergency_triggered

    @property
    def emergency_latched(self) -> bool:
        return self._emergency_triggered

    @property
    def command_history(self) -> tuple[Any, ...]:
        """Return the immutable audit stream required by ``DroneClient``."""

        return tuple(self.records)

    def _ensure_available(self) -> None:
        if self._emergency_triggered:
            raise EmergencyAborted("emergency stop has been triggered")
        if self._tello is None:
            raise DjitellopyBackendError("Tello client is not connected")

    def _call_factory(self, factory: Callable[..., Any], values: dict[str, Any]) -> Any:
        try:
            parameters = inspect.signature(factory).parameters
        except (TypeError, ValueError):
            return factory(**values)
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_kwargs:
            return factory(**values)
        accepted = {key: value for key, value in values.items() if key in parameters}
        return factory(**accepted)

    def _make_record(self, command: str, response: str) -> Any:
        try:
            from .models import CommandRecord

            return CommandRecord(
                command=command,
                response=response,
                position=None,
                timestamp=datetime.now(timezone.utc),
            )
        except (ImportError, TypeError):
            return SimpleNamespace(
                command=command,
                response=response,
                position=None,
                timestamp=datetime.now(timezone.utc),
            )

    def _record(self, command: str, response: Any = None) -> str:
        normalized = self._normalize_response(response)
        self.records.append(self._make_record(command, normalized))
        return normalized

    @staticmethod
    def _normalize_response(response: Any) -> str:
        if response is None:
            return "ok"
        if isinstance(response, bytes):
            return response.decode("utf-8", errors="replace").strip()
        if isinstance(response, bool):
            return "ok" if response else "error"
        return str(response).strip()

    def connect(self) -> None:
        with self._command_lock:
            self._ensure_not_emergency()
            self._bound_sockets = BoundSocketFactory(self.local_ip, self._socket_factory)
            values = {
                "local_ip": self.local_ip,
                "tello_ip": self.tello_ip,
                "video_port": self.video_port,
                "retry_count": self.retry_count,
                "timeout": self.timeout,
                "socket_factory": self._bound_sockets,
                "host": self.tello_ip,
                "vs_udp": self.video_port,
            }
            self._tello = self._call_factory(self._tello_factory, values)
            try:
                response = self._tello.connect()
            except Exception:
                self._close_tello()
                raise
            self._connected = True
            self._closed = False
            self._record("command", response)

    def enter_command_mode(self) -> None:
        with self._command_lock:
            self._ensure_available()
            # DJITelloPy's connect() already sends the SDK command-mode command.
            if not self.records or self.records[-1].command != "command":
                self._record(
                    "command",
                    self._send_control_command("command", timeout=self.timeout),
                )

    def close(self) -> None:
        with self._command_lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._closed:
            return
        if self._video is not None:
            try:
                if self._streaming and not self._emergency_triggered:
                    self._call_optional(self._tello, ("streamoff",), "streamoff")
            finally:
                self._video.stop()
                self._streaming = False
        self._close_tello()
        self._closed = True

    def _close_tello(self) -> None:
        tello = self._tello
        self._tello = None
        self._connected = False
        if tello is not None:
            # DJITelloPy calls its lifecycle method ``end``.  Suppress its
            # optional landing branch after an emergency because a normal
            # landing command must never follow the motor-stop datagram.
            if self._emergency_triggered:
                setattr(tello, "is_flying", False)
                setattr(tello, "stream_on", False)
            end = getattr(tello, "end", None)
            close = getattr(tello, "close", None)
            if end is not None:
                end()
            elif close is not None:
                close()

    def send(self, command: str) -> str:
        with self._command_lock:
            self._ensure_available()
            response = self._send_control_command(command)
            return self._record(command, response)

    def _send_control_command(self, command: str, *, timeout: float | None = None) -> Any:
        method = self._tello.send_control_command
        if timeout is None:
            return method(command)
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "timeout" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return method(command, timeout=timeout)
        return method(command)

    def _telemetry(self, methods: tuple[str, ...], field: str) -> Any:
        self._ensure_available()
        for method_name in methods:
            method = getattr(self._tello, method_name, None)
            if method is not None:
                value = method()
                if value is not None:
                    return value
        get_field = getattr(self._tello, "get_state_field", None)
        if get_field is not None:
            value = get_field(field)
            if value is not None:
                return value
        raise DjitellopyBackendError(f"DJITelloPy did not provide telemetry field {field}")

    def battery(self) -> int:
        return int(self._telemetry(("get_battery",), "bat"))

    def height_cm(self) -> int:
        return int(self._telemetry(("get_height",), "h"))

    def speed_cm_per_second(self) -> int:
        return int(self._telemetry(("get_speed", "query_speed"), "vgx"))

    def sdk_version(self) -> str:
        return str(self._telemetry(("query_sdk_version", "get_sdk_version"), "sdk"))

    def telemetry(self) -> Any:
        """Return the protocol's normalized telemetry snapshot."""

        from .models import Telemetry

        flight_time = self._optional_telemetry(
            ("get_flight_time", "query_flight_time"), "time", 0
        )
        temperature = self._optional_telemetry(
            ("get_temperature", "query_temperature"), "temph", None
        )
        return Telemetry(
            battery_percent=self.battery(),
            height_cm=self.height_cm(),
            flight_time_s=int(flight_time),
            temperature_c=int(temperature) if temperature is not None else None,
        )

    def _optional_telemetry(
        self,
        methods: tuple[str, ...],
        field: str,
        default: Any,
    ) -> Any:
        try:
            return self._telemetry(methods, field)
        except DjitellopyBackendError:
            return default

    def _command_method(self, methods: tuple[str, ...], command: str, *args: Any) -> str:
        with self._command_lock:
            self._ensure_available()
            for method_name in methods:
                method = getattr(self._tello, method_name, None)
                if method is not None:
                    return self._record(command, method(*args))
            return self.send(command)

    def set_speed(self, cm_per_second: int) -> str:
        return self._command_method(("set_speed",), f"speed {cm_per_second}", cm_per_second)

    def takeoff(self) -> str:
        return self._command_method(("takeoff",), "takeoff")

    def land(self) -> str:
        return self._command_method(("land",), "land")

    def move(self, direction: str, distance_cm: int) -> str:
        return self._command_method(("move",), f"{direction} {distance_cm}", direction, distance_cm)

    def rotate(self, direction: str, degrees: int) -> str:
        methods = {
            "cw": ("rotate_clockwise",),
            "ccw": ("rotate_counter_clockwise",),
        }
        try:
            candidates = methods[direction]
        except KeyError as exc:
            raise ValueError("rotation direction must be 'cw' or 'ccw'") from exc
        return self._command_method(candidates, f"{direction} {degrees}", degrees)

    def stop(self) -> str:
        return self._command_method(("stop",), "stop")

    def stream_on(self) -> str:
        with self._command_lock:
            return self._stream_on_unlocked()

    def _stream_on_unlocked(self) -> str:
        self._ensure_available()
        if self._streaming:
            return "ok"
        if self._bound_sockets is None:
            raise DjitellopyBackendError("connect() must be called before stream_on()")
        video_values = {
            "local_ip": self.local_ip,
            "port": self.video_port,
            "socket_factory": self._bound_sockets,
            "pixel_format": self.pixel_format,
        }
        video = self._call_factory(self._video_factory, video_values)
        video.start()
        self._video = video
        try:
            response = self._tello.streamon()
        except Exception:
            video.stop()
            self._video = None
            raise
        self._streaming = True
        return self._record("streamon", response)

    def start_video(self) -> None:
        self.stream_on()

    def stream_off(self) -> str:
        with self._command_lock:
            return self._stream_off_unlocked()

    def _stream_off_unlocked(self) -> str:
        self._ensure_available()
        response: Any = None
        error: Exception | None = None
        try:
            response = self._tello.streamoff()
        except Exception as exc:
            error = exc
        finally:
            if self._video is not None:
                self._video.stop()
                self._video = None
            self._streaming = False
        if error is not None:
            raise DjitellopyBackendError(f"streamoff failed: {error}") from error
        return self._record("streamoff", response)

    def stop_video(self) -> None:
        self.stream_off()

    def read_frame(self, timeout: float) -> Any:
        self._ensure_available()
        if not self._streaming or self._video is None:
            raise DjitellopyBackendError("video stream is not enabled")
        return self._video.read(timeout)

    def emergency(self) -> str:
        with self._command_lock:
            if self._emergency_triggered:
                return "ok"
            self._emergency_triggered = True
            try:
                # Do not route this through DJITelloPy's shared response socket:
                # a normal command may be waiting for a response there.  Emergency
                # gets a fresh, explicitly bound socket and never waits for ACK.
                response = self._send_emergency_datagram()
                return self._record("emergency", response)
            except Exception as exc:
                self.records.append(self._make_record("emergency", f"error: {exc}"))
                raise DjitellopyBackendError(f"emergency send failed: {exc}") from exc

    def _send_emergency_datagram(self) -> str:
        emergency_socket = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            set_blocking = getattr(emergency_socket, "setblocking", None)
            if set_blocking is not None:
                set_blocking(False)
            emergency_socket.bind((self.local_ip, 0))
            emergency_socket.sendto(
                b"emergency",
                (self.tello_ip, 8889),
            )
            return "ok"
        finally:
            emergency_socket.close()

    def _ensure_not_emergency(self) -> None:
        if self._emergency_triggered:
            raise EmergencyAborted("emergency stop has been triggered")

    @staticmethod
    def _call_optional(tello: Any, methods: tuple[str, ...], command: str) -> Any:
        for method_name in methods:
            method = getattr(tello, method_name, None)
            if method is not None:
                return method()
        return None
