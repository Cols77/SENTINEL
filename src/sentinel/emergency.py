"""Independent emergency sender and Windows keyboard boundary."""

from __future__ import annotations

import os
import socket
import time
from threading import Event, Thread
from typing import Callable


class EmergencySender:
    """Send Tello's motor-stop command from a dedicated UDP socket."""

    def __init__(
        self,
        local_ip: str | None = None,
        tello_ip: str = "192.168.10.1",
        *,
        socket_factory: Callable[..., socket.socket] | None = None,
    ) -> None:
        self.local_ip = local_ip
        self.address = (tello_ip, 8889)
        # Resolve the module attribute at instance creation so tests and
        # callers can inject a socket factory without relying on a default
        # argument captured during module import.
        self.socket_factory = socket_factory or socket.socket

    def send(self) -> int:
        if not self.local_ip:
            raise ValueError(
                "emergency sender requires the selected Tello adapter local IP"
            )
        if self.local_ip == "0.0.0.0":
            raise ValueError("wildcard local IP is unsafe for emergency sending")

        payload = b"emergency"
        sock = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.local_ip, 0))
            bytes_sent = sock.sendto(payload, self.address)
            if bytes_sent != len(payload):
                raise OSError(
                    "emergency datagram incomplete: "
                    f"sent {bytes_sent} of {len(payload)} bytes"
                )
            return bytes_sent
        finally:
            sock.close()


class EmergencyHotkey:
    """Listen for ``E`` on Windows and invoke a supplied emergency callback."""

    def __init__(self, on_emergency: Callable[[], None]) -> None:
        self.on_emergency = on_emergency
        self.stop_event = Event()
        self.triggered = Event()
        self.error: Exception | None = None
        self.thread = Thread(target=self._listen, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=0.2)

    def _listen(self) -> None:
        if os.name != "nt":
            return

        import msvcrt

        while not self.stop_event.is_set():
            if msvcrt.kbhit() and msvcrt.getwch().lower() == "e":
                if not self.triggered.is_set():
                    self.triggered.set()
                    try:
                        self.on_emergency()
                    except Exception as exc:
                        self.error = exc
                return
            time.sleep(0.02)
