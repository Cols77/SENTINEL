# SENTINEL Flight Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python 3.11+ SENTINEL command-line test harness with real Tello UDP control, deterministic dry-run execution, video-frame validation, a software-tracked 1 m³ envelope from 1.5–2.5 m altitude, and an operator emergency-stop path that preserves the primary internet adapter.

**Architecture:** A narrow `DroneClient` protocol separates the flight runner from the real UDP implementation and the deterministic dry-run implementation. `FlightEnvelope` tracks planned position in centimetres and rejects movement before transmission; `NetworkAdapterSelector` chooses and binds the dedicated Tello adapter without changing the primary interface or default route. The CLI composes these units, writes JSONL logs, and exposes both the supervised test and a standalone emergency command.

**Tech Stack:** Python 3.11+, `socket`, `argparse`, `dataclasses`, `typing`, `psutil`, PyAV (`av`), pytest, and standard-library JSON logging. The upstream `dji-sdk/Tello-Python` repository and official Tello SDK guide are protocol references; the legacy Python 2.7 pose-recognition application is not part of SENTINEL.

---

## File map

Create these files in `C:\coding\sentinel`:

- `pyproject.toml`: package metadata, runtime/dev dependencies, console entry point, and pytest configuration.
- `.gitignore`: Python caches, virtual environments, build outputs, and flight artifacts.
- `README.md`: setup, dual-adapter workflow, CLI examples, limitations, and upstream attribution.
- `docs/safety.md`: preflight and emergency-stop procedure for supervised indoor flights.
- `src/sentinel/__init__.py`: package version.
- `src/sentinel/__main__.py`: `python -m sentinel` entry point.
- `src/sentinel/models.py`: immutable positions, command records, video frames, adapter metadata, and run reports.
- `src/sentinel/envelope.py`: centimetre-based cube bounds and movement projection.
- `src/sentinel/client.py`: `DroneClient` protocol and client-side exceptions.
- `src/sentinel/dry_run.py`: deterministic no-network client.
- `src/sentinel/network.py`: adapter discovery, selection, local-IP routing probes, and default-route preservation checks.
- `src/sentinel/video.py`: H.264 packet assembly, frame decoding protocol, PyAV decoder, and UDP video receiver.
- `src/sentinel/udp_client.py`: real Tello command, telemetry, stream, and socket lifecycle implementation.
- `src/sentinel/runner.py`: preflight, approved flight sequence, envelope assertions, cleanup, and emergency state transitions.
- `src/sentinel/reporting.py`: JSONL run-report writer.
- `src/sentinel/emergency.py`: emergency UDP sender and Windows keyboard listener.
- `src/sentinel/cli.py`: `run` and `emergency` subcommands, configuration, and exit codes.
- `tests/test_envelope.py`: envelope behavior.
- `tests/test_dry_run.py`: deterministic client behavior.
- `tests/test_network.py`: adapter selection and route preservation.
- `tests/test_video.py`: packet assembly and decoder integration boundary.
- `tests/test_udp_client.py`: command formatting, socket binding, and response parsing.
- `tests/test_runner.py`: approved sequence, bounds, failure cleanup, and emergency behavior.
- `tests/test_cli.py`: CLI mode isolation and exit codes.

## Task 1: Scaffold the Python project

**Files:**

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/sentinel/__init__.py`
- Create: `src/sentinel/__main__.py`
- Create: `tests/test_package.py`

- [ ] **Step 1: Add packaging configuration**

Create `pyproject.toml` with this content:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "sentinel-tello"
version = "0.1.0"
description = "Supervised DJI Tello command and flight-envelope test harness"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "av>=12",
    "psutil>=5.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
]

[project.scripts]
sentinel = "sentinel.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-ra"
```

- [ ] **Step 2: Add repository ignores**

Create `.gitignore`:

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.venv/
venv/
build/
dist/
artifacts/
.coverage
htmlcov/
```

- [ ] **Step 3: Add the importable package shell**

Create `src/sentinel/__init__.py`:

```python
"""SENTINEL Tello command and flight-envelope test harness."""

__version__ = "0.1.0"
```

Create `src/sentinel/__main__.py`:

```python
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write and run the scaffold test**

Create `tests/test_package.py`:

```python
from sentinel import __version__


def test_package_exposes_version():
    assert __version__ == "0.1.0"
```

Run: `python -m pytest tests/test_package.py -q`  
Expected: `1 passed`.

- [ ] **Step 5: Commit the scaffold**

```powershell
git add pyproject.toml .gitignore src/sentinel/__init__.py src/sentinel/__main__.py tests/test_package.py
git commit -m "chore: scaffold SENTINEL Python package"
```

## Task 2: Implement the flight-envelope domain model using TDD

**Files:**

- Create: `tests/test_envelope.py`
- Create: `src/sentinel/models.py`
- Create: `src/sentinel/envelope.py`

- [ ] **Step 1: Write failing position and boundary tests**

Create `tests/test_envelope.py`:

```python
import pytest

from sentinel.envelope import EnvelopeViolation, FlightEnvelope
from sentinel.models import Position


def test_default_envelope_starts_at_launch_origin_and_lower_altitude():
    envelope = FlightEnvelope()

    assert envelope.position == Position(0, 0, 150)
    assert envelope.contains(envelope.position)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("forward", Position(0, 50, 150)),
        ("back", Position(0, -50, 150)),
        ("right", Position(50, 0, 150)),
        ("left", Position(-50, 0, 150)),
        ("up", Position(0, 0, 200)),
    ],
)
def test_project_move_returns_expected_position(direction, expected):
    envelope = FlightEnvelope()

    assert envelope.project_move(direction, 50) == expected


def test_project_move_allows_down_inside_the_cube():
    envelope = FlightEnvelope(position=Position(0, 0, 200))

    assert envelope.project_move("down", 50) == Position(0, 0, 150)


@pytest.mark.parametrize(
    ("position", "direction"),
    [
        (Position(50, 0, 150), "right"),
        (Position(-50, 0, 150), "left"),
        (Position(0, 50, 150), "forward"),
        (Position(0, -50, 150), "back"),
        (Position(0, 0, 250), "up"),
        (Position(0, 0, 150), "down"),
    ],
)
def test_project_move_rejects_crossing_any_cube_face(position, direction):
    envelope = FlightEnvelope(position=position)

    with pytest.raises(EnvelopeViolation):
        envelope.project_move(direction, 20)


def test_commit_move_updates_position_only_after_validation():
    envelope = FlightEnvelope()

    assert envelope.commit_move("forward", 50) == Position(0, 50, 150)
    assert envelope.position == Position(0, 50, 150)

    with pytest.raises(EnvelopeViolation):
        envelope.commit_move("forward", 20)

    assert envelope.position == Position(0, 50, 150)
```

Run: `python -m pytest tests/test_envelope.py -q`  
Expected: FAIL because the domain modules do not yet exist.

- [ ] **Step 2: Implement the minimal domain types**

Create `src/sentinel/models.py`:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Position:
    x: int
    y: int
    z: int


@dataclass(frozen=True)
class VideoFrame:
    width: int
    height: int
    captured_at: datetime


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    index: int
    local_ip: str
    is_up: bool
    is_default_route: bool
    gateway: str | None = None
    metric: int | None = None


@dataclass(frozen=True)
class CommandRecord:
    command: str
    response: str
    position: Position | None
    timestamp: datetime


@dataclass(frozen=True)
class RunReport:
    status: str
    mode: str
    commands: tuple[CommandRecord, ...]
    video_frame: VideoFrame | None
    adapter: AdapterInfo | None
    error: str | None = None
```

Create `src/sentinel/envelope.py`:

```python
from dataclasses import dataclass

from .models import Position


class EnvelopeViolation(ValueError):
    """Raised when a planned movement leaves the flight envelope."""


@dataclass
class FlightEnvelope:
    min_x: int = -50
    max_x: int = 50
    min_y: int = -50
    max_y: int = 50
    min_z: int = 150
    max_z: int = 250
    position: Position = Position(0, 0, 150)

    def contains(self, position: Position) -> bool:
        return (
            self.min_x <= position.x <= self.max_x
            and self.min_y <= position.y <= self.max_y
            and self.min_z <= position.z <= self.max_z
        )

    def project_move(self, direction: str, distance_cm: int) -> Position:
        if distance_cm < 20 or distance_cm > 500:
            raise ValueError("Tello movement distance must be between 20 and 500 cm")

        deltas = {
            "forward": (0, distance_cm, 0),
            "back": (0, -distance_cm, 0),
            "right": (distance_cm, 0, 0),
            "left": (-distance_cm, 0, 0),
            "up": (0, 0, distance_cm),
            "down": (0, 0, -distance_cm),
        }
        try:
            dx, dy, dz = deltas[direction]
        except KeyError as exc:
            raise ValueError(f"unsupported movement direction: {direction}") from exc

        candidate = Position(
            self.position.x + dx,
            self.position.y + dy,
            self.position.z + dz,
        )
        if not self.contains(candidate):
            raise EnvelopeViolation(
                f"{direction} {distance_cm} cm would leave envelope: {candidate}"
            )
        return candidate

    def commit_move(self, direction: str, distance_cm: int) -> Position:
        candidate = self.project_move(direction, distance_cm)
        self.position = candidate
        return candidate
```

- [ ] **Step 3: Run the envelope tests**

Run: `python -m pytest tests/test_envelope.py -q`  
Expected: all envelope tests pass.

- [ ] **Step 4: Commit the envelope**

```powershell
git add src/sentinel/models.py src/sentinel/envelope.py tests/test_envelope.py
git commit -m "feat: enforce the SENTINEL flight envelope"
```

## Task 3: Define the client interface and deterministic dry-run client

**Files:**

- Create: `tests/test_dry_run.py`
- Create: `src/sentinel/client.py`
- Create: `src/sentinel/dry_run.py`

- [ ] **Step 1: Write failing dry-run behavior tests**

Create `tests/test_dry_run.py`:

```python
import pytest

from sentinel.client import EmergencyAborted
from sentinel.dry_run import DryRunClient


def test_dry_run_has_no_network_and_returns_deterministic_telemetry():
    client = DryRunClient()

    client.connect()

    assert client.battery() == 100
    assert client.height_cm() == 0
    assert client.sdk_version() == "dry-run"
    assert client.network_opened is False


def test_dry_run_simulates_video_and_command_state():
    client = DryRunClient()
    client.connect()
    client.stream_on()
    frame = client.read_frame(timeout=0.1)
    client.stream_off()

    assert (frame.width, frame.height) == (640, 480)
    client.takeoff()
    client.move("up", 20)
    assert client.height_cm() == 120
    assert [record.command for record in client.records] == [
        "command",
        "streamon",
        "streamoff",
        "takeoff",
        "up 20",
    ]


def test_dry_run_emergency_prevents_later_flight_commands():
    client = DryRunClient()
    client.connect()
    client.takeoff()
    client.emergency()

    with pytest.raises(EmergencyAborted):
        client.move("forward", 50)

    assert client.emergency_triggered is True
```

Run: `python -m pytest tests/test_dry_run.py -q`  
Expected: FAIL because the client interface and implementation do not yet exist.

- [ ] **Step 2: Define the client protocol and exceptions**

Create `src/sentinel/client.py`:

```python
from typing import Protocol

from .models import CommandRecord, VideoFrame


class DroneError(RuntimeError):
    """Base exception for client and protocol failures."""


class EmergencyAborted(DroneError):
    """Raised after the emergency command has been sent."""


class DroneClient(Protocol):
    records: list[CommandRecord]
    def connect(self) -> None: pass
    def close(self) -> None: pass
    def send(self, command: str) -> str: pass
    def battery(self) -> int: pass
    def height_cm(self) -> int: pass
    def speed_cm_per_second(self) -> int: pass
    def sdk_version(self) -> str: pass
    def set_speed(self, cm_per_second: int) -> str: pass
    def takeoff(self) -> str: pass
    def land(self) -> str: pass
    def move(self, direction: str, distance_cm: int) -> str: pass
    def rotate(self, direction: str, degrees: int) -> str: pass
    def stop(self) -> str: pass
    def stream_on(self) -> str: pass
    def stream_off(self) -> str: pass
    def read_frame(self, timeout: float) -> VideoFrame: pass
    def emergency(self) -> str: pass
```

- [ ] **Step 3: Implement the minimal dry-run client**

Create `src/sentinel/dry_run.py`:

```python
from datetime import datetime, timezone

from .client import DroneClient, EmergencyAborted
from .models import CommandRecord, VideoFrame


class DryRunClient:
    def __init__(self) -> None:
        self.network_opened = False
        self.connected = False
        self.streaming = False
        self.airborne = False
        self.emergency_triggered = False
        self._battery = 100
        self._height = 0
        self._speed = 10
        self.records: list[CommandRecord] = []

    def _ensure_available(self) -> None:
        if self.emergency_triggered:
            raise EmergencyAborted("dry-run emergency stop has been triggered")

    def _record(self, command: str, response: str = "ok") -> str:
        self.records.append(
            CommandRecord(
                command=command,
                response=response,
                position=None,  # replaced by the runner's envelope in reports
                timestamp=datetime.now(timezone.utc),
            )
        )
        return response

    def connect(self) -> None:
        self.connected = True
        self._record("command")

    def close(self) -> None:
        self.connected = False

    def send(self, command: str) -> str:
        self._ensure_available()
        return self._record(command)

    def battery(self) -> int:
        self._ensure_available()
        return self._battery

    def height_cm(self) -> int:
        self._ensure_available()
        return self._height

    def speed_cm_per_second(self) -> int:
        self._ensure_available()
        return self._speed

    def sdk_version(self) -> str:
        self._ensure_available()
        return "dry-run"

    def set_speed(self, cm_per_second: int) -> str:
        self._ensure_available()
        self._speed = cm_per_second
        return self._record(f"speed {cm_per_second}")

    def takeoff(self) -> str:
        self._ensure_available()
        self.airborne = True
        self._height = 100
        return self._record("takeoff")

    def land(self) -> str:
        self._ensure_available()
        self.airborne = False
        self._height = 0
        return self._record("land")

    def move(self, direction: str, distance_cm: int) -> str:
        self._ensure_available()
        if direction == "up":
            self._height += distance_cm
        elif direction == "down":
            self._height -= distance_cm
        return self._record(f"{direction} {distance_cm}")

    def rotate(self, direction: str, degrees: int) -> str:
        self._ensure_available()
        return self._record(f"{direction} {degrees}")

    def stop(self) -> str:
        self._ensure_available()
        return self._record("stop")

    def stream_on(self) -> str:
        self._ensure_available()
        self.streaming = True
        return self._record("streamon")

    def stream_off(self) -> str:
        self._ensure_available()
        self.streaming = False
        return self._record("streamoff")

    def read_frame(self, timeout: float) -> VideoFrame:
        self._ensure_available()
        if not self.streaming:
            raise RuntimeError("dry-run stream is not enabled")
        return VideoFrame(640, 480, datetime.now(timezone.utc))

    def emergency(self) -> str:
        if not self.emergency_triggered:
            self.emergency_triggered = True
            self._record("emergency")
        return "ok"
```

- [ ] **Step 4: Run the dry-run tests and all existing tests**

Run: `python -m pytest -q`  
Expected: all scaffold, envelope, and dry-run tests pass.

- [ ] **Step 5: Commit the client boundary**

```powershell
git add src/sentinel/client.py src/sentinel/dry_run.py src/sentinel/models.py tests/test_dry_run.py
git commit -m "feat: add real-and-dry-run client boundary"
```

## Task 4: Add Windows-safe dual-adapter selection

**Files:**

- Create: `tests/test_network.py`
- Create: `src/sentinel/network.py`

- [ ] **Step 1: Write failing selector tests**

Create `tests/test_network.py`:

```python
import pytest

from sentinel.models import AdapterInfo
from sentinel.network import (
    AmbiguousAdapter,
    DefaultRouteChanged,
    NoTelloAdapter,
    NetworkAdapterSelector,
    RouteSnapshot,
)


def test_auto_selection_prefers_tello_subnet_on_non_default_adapter():
    adapters = [
        AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True),
        AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False),
    ]

    selected = NetworkAdapterSelector(adapters).select()

    assert selected.name == "USB Wi-Fi"
    assert selected.local_ip == "192.168.10.2"


def test_selection_rejects_missing_or_ambiguous_adapter():
    with pytest.raises(NoTelloAdapter):
        NetworkAdapterSelector(
            [AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True)]
        ).select()

    with pytest.raises(AmbiguousAdapter):
        NetworkAdapterSelector(
            [
                AdapterInfo("USB Wi-Fi A", 19, "192.168.10.2", True, False),
                AdapterInfo("USB Wi-Fi B", 20, "192.168.10.3", True, False),
            ]
        ).select()


def test_explicit_local_ip_override_selects_exact_adapter():
    selected = NetworkAdapterSelector(
        [
            AdapterInfo("USB Wi-Fi A", 19, "192.168.10.2", True, False),
            AdapterInfo("USB Wi-Fi B", 20, "192.168.10.3", True, False),
        ]
    ).select(local_ip="192.168.10.3")

    assert selected.name == "USB Wi-Fi B"


def test_route_snapshot_must_not_change():
    before = RouteSnapshot(source_ip="192.168.1.44", interface_index=7)
    after = RouteSnapshot(source_ip="192.168.10.2", interface_index=19)

    with pytest.raises(DefaultRouteChanged):
        NetworkAdapterSelector.assert_default_route_unchanged(before, after)
```

Run: `python -m pytest tests/test_network.py -q`  
Expected: FAIL because the selector module does not yet exist.

- [ ] **Step 2: Implement injected adapter selection and route checks**

Create `src/sentinel/network.py`:

```python
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
import socket
from typing import Iterable

from .models import AdapterInfo


TELLO_SUBNET = IPv4Network("192.168.10.0/24")


class NetworkSelectionError(RuntimeError):
    """Base error for unsafe or unavailable adapter selection."""


class NoTelloAdapter(NetworkSelectionError):
    pass


class AmbiguousAdapter(NetworkSelectionError):
    pass


class DefaultRouteChanged(NetworkSelectionError):
    pass


@dataclass(frozen=True)
class RouteSnapshot:
    source_ip: str | None
    interface_index: int | None


def route_source_ip(destination: str) -> str | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((destination, 8889))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def capture_default_route() -> RouteSnapshot:
    source_ip = route_source_ip("1.1.1.1")
    return RouteSnapshot(source_ip=source_ip, interface_index=None)


class NetworkAdapterSelector:
    def __init__(self, adapters: Iterable[AdapterInfo]) -> None:
        self.adapters = tuple(adapters)

    def select(
        self,
        *,
        local_ip: str | None = None,
        interface_name: str | None = None,
        interface_index: int | None = None,
    ) -> AdapterInfo:
        if local_ip is not None:
            candidates = [a for a in self.adapters if a.local_ip == local_ip]
        elif interface_name is not None:
            candidates = [a for a in self.adapters if a.name == interface_name]
        elif interface_index is not None:
            candidates = [a for a in self.adapters if a.index == interface_index]
        else:
            candidates = [
                a
                for a in self.adapters
                if a.is_up
                and not a.is_default_route
                and IPv4Address(a.local_ip) in TELLO_SUBNET
            ]

        if not candidates:
            raise NoTelloAdapter("no active dedicated adapter can reach the Tello")
        if len(candidates) != 1:
            raise AmbiguousAdapter(
                "multiple Tello adapters matched; pass --local-ip or --interface"
            )
        return candidates[0]

    @staticmethod
    def assert_default_route_unchanged(
        before: RouteSnapshot, after: RouteSnapshot
    ) -> None:
        if before != after:
            raise DefaultRouteChanged(
                f"internet route changed from {before} to {after}"
            )


def discover_adapters() -> tuple[AdapterInfo, ...]:
    import psutil

    stats = psutil.net_if_stats()
    result: list[AdapterInfo] = []
    for name, addresses in psutil.net_if_addrs().items():
        stat = stats.get(name)
        if stat is None or not stat.isup:
            continue
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            result.append(
                AdapterInfo(
                    name=name,
                    index=socket.if_nametoindex(name),
                    local_ip=address.address,
                    is_up=True,
                    is_default_route=False,
                )
            )
    return tuple(result)
```

The production selector must add `is_default_route=True` to the adapter whose local IP equals `capture_default_route().source_ip`; the runner must capture the default route before and after selection and before takeoff. It must not invoke `Set-NetIPInterface`, `netsh interface set`, or any route mutation.

- [ ] **Step 3: Run selector tests**

Run: `python -m pytest tests/test_network.py -q`  
Expected: all selector tests pass.

- [ ] **Step 4: Commit network selection**

```powershell
git add src/sentinel/network.py src/sentinel/models.py tests/test_network.py
git commit -m "feat: select a dedicated Tello network adapter safely"
```

## Task 5: Implement command transport and video decoding boundaries

**Files:**

- Create: `tests/test_udp_client.py`
- Create: `tests/test_video.py`
- Create: `src/sentinel/video.py`
- Create: `src/sentinel/udp_client.py`

- [ ] **Step 1: Write failing command and video tests**

Create `tests/test_udp_client.py`:

```python
from sentinel.udp_client import parse_response, format_move, format_rotate


def test_commands_use_tello_centimetre_and_degree_syntax():
    assert format_move("forward", 50) == "forward 50"
    assert format_move("up", 20) == "up 20"
    assert format_rotate("cw", 90) == "cw 90"


def test_responses_strip_whitespace_and_normalize_case():
    assert parse_response(b" OK\r\n") == "ok"
    assert parse_response(b"error\n") == "error"
```

Create `tests/test_video.py`:

```python
from sentinel.video import H264FrameAssembler


class FakeDecoder:
    def decode(self, payload: bytes):
        return [(640, 480)] if payload else []


def test_frame_assembler_decodes_when_tello_frame_ends():
    assembler = H264FrameAssembler(FakeDecoder())

    assert assembler.push(b"a" * 1460) == []
    assert assembler.push(b"b" * 12) == [(640, 480)]
```

Run: `python -m pytest tests/test_udp_client.py tests/test_video.py -q`  
Expected: FAIL because the transport and video modules do not yet exist.

- [ ] **Step 2: Implement the pure command helpers and H.264 assembler**

Create `src/sentinel/video.py`:

```python
from collections.abc import Protocol


class FrameDecoder(Protocol):
    def decode(self, payload: bytes) -> list[tuple[int, int]]: pass


class H264FrameAssembler:
    def __init__(self, decoder: FrameDecoder) -> None:
        self.decoder = decoder
        self._payload = bytearray()

    def push(self, packet: bytes) -> list[tuple[int, int]]:
        self._payload.extend(packet)
        if len(packet) == 1460:
            return []
        payload = bytes(self._payload)
        self._payload.clear()
        return self.decoder.decode(payload)
```

Create the first part of `src/sentinel/udp_client.py`:

```python
from typing import Final


MOVEMENT_DIRECTIONS: Final = {"forward", "back", "left", "right", "up", "down"}
ROTATION_DIRECTIONS: Final = {"cw", "ccw"}


def format_move(direction: str, distance_cm: int) -> str:
    if direction not in MOVEMENT_DIRECTIONS:
        raise ValueError(f"unsupported movement direction: {direction}")
    if not 20 <= distance_cm <= 500:
        raise ValueError("Tello movement distance must be between 20 and 500 cm")
    return f"{direction} {distance_cm}"


def format_rotate(direction: str, degrees: int) -> str:
    if direction not in ROTATION_DIRECTIONS:
        raise ValueError(f"unsupported rotation direction: {direction}")
    if not 1 <= degrees <= 360:
        raise ValueError("Tello rotation must be between 1 and 360 degrees")
    return f"{direction} {degrees}"


def parse_response(response: bytes) -> str:
    return response.decode("utf-8", errors="replace").strip().lower()
```

- [ ] **Step 3: Add the PyAV decoder and UDP video receiver**

Extend `src/sentinel/video.py`:

```python
from datetime import datetime, timezone
import socket
from threading import Event, Thread
from queue import Queue

from .models import VideoFrame


class PyAVDecoder:
    def __init__(self) -> None:
        import av

        self._codec = av.CodecContext.create("h264", "r")

    def decode(self, payload: bytes) -> list[tuple[int, int]]:
        import av

        return [
            (frame.width, frame.height)
            for frame in self._codec.decode(av.Packet(payload))
        ]


class UdpVideoReceiver:
    def __init__(self, local_ip: str, port: int = 11111) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((local_ip, port))
        self.socket.settimeout(0.25)
        self.frames: Queue[VideoFrame] = Queue()
        self.stop_event = Event()
        self.thread = Thread(target=self._receive, daemon=True)
        self.assembler = H264FrameAssembler(PyAVDecoder())

    def start(self) -> None:
        self.thread.start()

    def _receive(self) -> None:
        while not self.stop_event.is_set():
            try:
                packet, _ = self.socket.recvfrom(2048)
            except socket.timeout:
                continue
            for width, height in self.assembler.push(packet):
                self.frames.put(
                    VideoFrame(width, height, datetime.now(timezone.utc))
                )

    def read(self, timeout: float) -> VideoFrame:
        return self.frames.get(timeout=timeout)

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1)
        self.socket.close()
```

- [ ] **Step 4: Implement the real UDP client**

Implement `UdpTelloClient` in `src/sentinel/udp_client.py` with this complete behavior:

```python
from datetime import datetime, timezone
from queue import Empty
import socket
from threading import RLock

from .client import DroneError
from .models import CommandRecord, VideoFrame
from .video import UdpVideoReceiver


def _require_ok(response: str, command: str) -> str:
    if response != "ok":
        raise DroneError(f"Tello rejected {command!r}: {response}")
    return response


def _parse_int(response: str, command: str) -> int:
    try:
        return int(response)
    except ValueError as exc:
        raise DroneError(f"Tello returned a non-integer for {command!r}: {response}") from exc


class UdpTelloClient:
    def __init__(
        self,
        local_ip: str,
        tello_ip: str = "192.168.10.1",
        timeout: float = 7.0,
        socket_factory=socket.socket,
        video_factory=UdpVideoReceiver,
    ) -> None:
        self.local_ip = local_ip
        self.tello_address = (tello_ip, 8889)
        self.timeout = timeout
        self.socket_factory = socket_factory
        self.video_factory = video_factory
        self.socket = None
        self.video = None
        self.airborne = False
        self.emergency_triggered = False
        self.records: list[CommandRecord] = []
        self._lock = RLock()

    def connect(self) -> None:
        self.socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.local_ip, 0))
        self.socket.settimeout(self.timeout)
        _require_ok(self.send("command"), "command")

    def close(self) -> None:
        if self.video is not None:
            self.video.close()
            self.video = None
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def send(self, command: str) -> str:
        if self.emergency_triggered and command != "emergency":
            raise DroneError("commands are disabled after emergency stop")
        if self.socket is None:
            raise DroneError("Tello client is not connected")
        with self._lock:
            self.socket.sendto(command.encode("utf-8"), self.tello_address)
            payload, _ = self.socket.recvfrom(3000)
            response = parse_response(payload)
            self.records.append(
                CommandRecord(command, response, None, datetime.now(timezone.utc))
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
        if not 10 <= cm_per_second <= 100:
            raise ValueError("Tello speed must be between 10 and 100 cm/s")
        return _require_ok(self.send(f"speed {cm_per_second}"), "speed")

    def takeoff(self) -> str:
        response = _require_ok(self.send("takeoff"), "takeoff")
        self.airborne = True
        return response

    def land(self) -> str:
        response = _require_ok(self.send("land"), "land")
        self.airborne = False
        return response

    def move(self, direction: str, distance_cm: int) -> str:
        return _require_ok(self.send(format_move(direction, distance_cm)), direction)

    def rotate(self, direction: str, degrees: int) -> str:
        return _require_ok(self.send(format_rotate(direction, degrees)), direction)

    def stop(self) -> str:
        return _require_ok(self.send("stop"), "stop")

    def stream_on(self) -> str:
        if self.video is not None:
            raise DroneError("video stream is already active")
        self.video = self.video_factory(self.local_ip)
        self.video.start()
        try:
            return _require_ok(self.send("streamon"), "streamon")
        except Exception:
            self.video.close()
            self.video = None
            raise

    def stream_off(self) -> str:
        response = _require_ok(self.send("streamoff"), "streamoff")
        if self.video is not None:
            self.video.close()
            self.video = None
        return response

    def read_frame(self, timeout: float) -> VideoFrame:
        if self.video is None:
            raise DroneError("video stream is not active")
        try:
            return self.video.read(timeout)
        except Empty as exc:
            raise DroneError("no decodable video frame arrived before timeout") from exc

    def emergency(self) -> str:
        if self.socket is None:
            raise DroneError("Tello client is not connected")
        with self._lock:
            self.socket.sendto(b"emergency", self.tello_address)
            self.emergency_triggered = True
            return "ok"
```

Add a small state-listener thread in the same module that binds `local_ip:8890`, parses semicolon-separated state fields into a latest-state dictionary, and closes before the command socket. The listener is diagnostic only; `height_cm()` remains a strict `height?` command query. Bind every socket to `local_ip` before use, and use a lock around sends so the emergency hotkey cannot interleave bytes with a normal command.

- [ ] **Step 5: Run transport tests and all unit tests**

Run: `python -m pytest -q`  
Expected: all tests pass without a drone; no test opens a real socket unless a fake socket is injected.

- [ ] **Step 6: Commit transport and video support**

```powershell
git add src/sentinel/video.py src/sentinel/udp_client.py tests/test_video.py tests/test_udp_client.py
git commit -m "feat: add Tello UDP and video transport"
```

## Task 6: Build the approved flight runner using TDD

**Files:**

- Create: `tests/test_runner.py`
- Create: `src/sentinel/runner.py`

- [ ] **Step 1: Write the passing-spec tests first**

Create `tests/test_runner.py`:

```python
from sentinel.dry_run import DryRunClient
from sentinel.runner import FlightTestRunner


def test_dry_run_executes_only_the_approved_sequence():
    client = DryRunClient()
    report = FlightTestRunner(client, mode="dry-run").run(confirm=True)

    assert report.status == "PASS"
    assert [record.command for record in client.records] == [
        "command",
        "speed 20",
        "streamon",
        "streamoff",
        "takeoff",
        "up 20",
        "up 20",
        "up 20",
        "forward 50",
        "right 50",
        "back 50",
        "left 50",
        "up 50",
        "down 50",
        "cw 90",
        "ccw 90",
        "stop",
        "land",
    ]
    assert report.video_frame is not None
    assert (report.video_frame.width, report.video_frame.height) == (640, 480)


def test_runner_does_not_send_a_movement_that_crosses_the_envelope():
    client = DryRunClient()
    runner = FlightTestRunner(client, mode="dry-run")
    runner.envelope.position = runner.envelope.position.__class__(50, 0, 150)

    runner.move_checked("right", 20)

    assert "right 20" not in [record.command for record in client.records]


def test_emergency_abort_has_no_landing_or_following_movement():
    client = DryRunClient()
    client.connect()
    client.takeoff()
    client.emergency_triggered = True
    report = FlightTestRunner(client, mode="dry-run").abort_from_emergency()

    assert report.status == "EMERGENCY_ABORTED"
    assert "land" not in [record.command for record in client.records]
```

Run: `python -m pytest tests/test_runner.py -q`  
Expected: FAIL because the runner does not yet exist.

- [ ] **Step 2: Implement the runner with a separate altitude-entry phase**

Create `src/sentinel/runner.py`:

```python
from dataclasses import replace
from threading import Event
from typing import Callable, TypeVar

from .client import DroneClient, EmergencyAborted
from .envelope import EnvelopeViolation, FlightEnvelope
from .models import CommandRecord, Position, RunReport, VideoFrame


Result = TypeVar("Result")


class FlightTestRunner:
    def __init__(self, client: DroneClient, mode: str, minimum_battery: int = 40):
        self.client = client
        self.mode = mode
        self.minimum_battery = minimum_battery
        self.envelope = FlightEnvelope()
        self.video_frame: VideoFrame | None = None
        self.history: list[CommandRecord] = []
        self.emergency_event = Event()

    def _call(self, action: Callable[[], Result], position: Position | None = None) -> Result:
        self._raise_if_emergency()
        record_count = len(self.client.records)
        result = action()
        if len(self.client.records) > record_count:
            self.history.append(replace(self.client.records[-1], position=position))
        return result

    def _raise_if_emergency(self) -> None:
        if self.emergency_event.is_set():
            raise EmergencyAborted("operator emergency stop was triggered")

    def trigger_emergency(self) -> None:
        record_count = len(self.client.records)
        self.client.emergency()
        if len(self.client.records) > record_count:
            self.history.append(
                replace(self.client.records[-1], position=self.envelope.position)
            )
        self.emergency_event.set()

    def move_checked(self, direction: str, distance_cm: int) -> None:
        candidate = self.envelope.project_move(direction, distance_cm)
        try:
            response = self._call(
                lambda: self.client.move(direction, distance_cm), candidate
            )
        except Exception as exc:
            if self.emergency_event.is_set():
                raise EmergencyAborted("movement interrupted by emergency stop") from exc
            raise
        if response != "ok":
            raise RuntimeError(f"movement failed: {direction} {distance_cm}: {response}")
        self.envelope.position = candidate

    def _enter_altitude_band(self) -> None:
        height = self._call(self.client.height_cm)
        if height > self.envelope.max_z:
            raise EnvelopeViolation(f"takeoff height exceeds upper face: {height} cm")
        while height < self.envelope.min_z:
            if height + 20 > self.envelope.max_z:
                raise EnvelopeViolation("20 cm altitude step would cross upper face")
            response = self._call(lambda: self.client.move("up", 20))
            if response != "ok":
                raise RuntimeError(f"altitude-entry move failed: {response}")
            height = self._call(self.client.height_cm)
            if height > self.envelope.max_z:
                raise EnvelopeViolation(f"measured height exceeds upper face: {height} cm")
        self.envelope.position = Position(0, 0, height)

    def run(self, *, confirm: bool) -> RunReport:
        if not confirm:
            raise RuntimeError("real flight requires explicit confirmation")
        airborne = False
        self.history.clear()
        try:
            self._call(self.client.connect)
            if self._call(self.client.battery) < self.minimum_battery:
                raise RuntimeError("battery is below the 40% minimum")
            self._call(self.client.sdk_version)
            self._call(lambda: self.client.set_speed(20))
            if self._call(self.client.speed_cm_per_second) != 20:
                raise RuntimeError("speed readback did not match 20 cm/s")
            self._call(self.client.stream_on)
            self.video_frame = self._call(lambda: self.client.read_frame(timeout=10))
            self._call(self.client.stream_off)
            self._call(self.client.takeoff)
            airborne = True
            self._enter_altitude_band()
            self.move_checked("forward", 50)
            self.move_checked("right", 50)
            self.move_checked("back", 50)
            self.move_checked("left", 50)
            self.move_checked("up", 50)
            self.move_checked("down", 50)
            if self._call(lambda: self.client.rotate("cw", 90)) != "ok":
                raise RuntimeError("clockwise rotation failed")
            if self._call(lambda: self.client.rotate("ccw", 90)) != "ok":
                raise RuntimeError("counter-clockwise rotation failed")
            if self._call(self.client.stop) != "ok":
                raise RuntimeError("hover/stop failed")
            if self._call(self.client.land) != "ok":
                raise RuntimeError("landing failed")
            airborne = False
            return RunReport("PASS", self.mode, tuple(self.history), self.video_frame, None)
        except EmergencyAborted:
            return RunReport(
                "EMERGENCY_ABORTED", self.mode, tuple(self.history), self.video_frame, None
            )
        except Exception as exc:
            if airborne and not self.emergency_event.is_set():
                try:
                    self.client.land()
                except Exception:
                    pass
            status = "EMERGENCY_ABORTED" if self.emergency_event.is_set() else "FAIL"
            return RunReport(
                status, self.mode, tuple(self.history), self.video_frame, None, str(exc)
            )
        finally:
            self.client.close()

    def abort_from_emergency(self) -> RunReport:
        self.emergency_event.set()
        return RunReport(
            "EMERGENCY_ABORTED", self.mode, tuple(self.history), self.video_frame, None
        )
```

Altitude entry is intentionally separate from `FlightEnvelope.project_move`: the drone begins on the ground below the cube, so ascent to the cube floor is checked for its upper limit but is not treated as an in-cube movement. The runner must receive the selected `AdapterInfo`, snapshot the default route before and after adapter selection, reject a changed route before takeoff, check height after every vertical movement, and never invoke normal landing cleanup after `EmergencyAborted`.

- [ ] **Step 3: Run runner and full tests**

Run: `python -m pytest -q`  
Expected: all tests pass and the command order exactly matches the approved sequence.

- [ ] **Step 4: Commit the runner**

```powershell
git add src/sentinel/runner.py tests/test_runner.py
git commit -m "feat: run the bounded conservative Tello test"
```

## Task 7: Add reporting, emergency controls, and CLI

**Files:**

- Create: `tests/test_cli.py`
- Create: `src/sentinel/reporting.py`
- Create: `src/sentinel/emergency.py`
- Create: `src/sentinel/cli.py`

- [ ] **Step 1: Write failing CLI and emergency tests**

Create `tests/test_cli.py`:

```python
from sentinel.cli import main


def test_dry_run_cli_returns_zero(monkeypatch, tmp_path):
    output = tmp_path / "run.jsonl"
    assert main(["run", "--dry-run", "--log", str(output)]) == 0
    assert output.exists()


def test_real_cli_requires_explicit_confirmation(monkeypatch):
    assert main(["run", "--real"]) == 1


def test_emergency_sender_uses_the_emergency_command(monkeypatch):
    sent = []

    class FakeSender:
        def send(self):
            sent.append("emergency")

    monkeypatch.setattr("sentinel.cli.EmergencySender", FakeSender)
    monkeypatch.setattr("sentinel.cli.resolve_local_ip", lambda args: "192.168.10.2")
    assert main(["emergency"]) == 0
    assert sent == ["emergency"]
```

Run: `python -m pytest tests/test_cli.py -q`  
Expected: FAIL because reporting, emergency, and CLI modules do not yet exist.

- [ ] **Step 2: Implement JSONL reporting**

Create `src/sentinel/reporting.py`:

```python
import json
from dataclasses import asdict
from pathlib import Path

from .models import RunReport


def write_report(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(report), handle, default=str, sort_keys=True)
        handle.write("\n")
```

- [ ] **Step 3: Implement emergency sender and Windows hotkey boundary**

Create `src/sentinel/emergency.py`:

```python
import socket
from threading import Event, Thread
from typing import Callable


class EmergencySender:
    def __init__(self, local_ip: str | None = None, tello_ip: str = "192.168.10.1"):
        self.local_ip = local_ip
        self.address = (tello_ip, 8889)

    def send(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if self.local_ip:
                sock.bind((self.local_ip, 0))
            sock.sendto(b"emergency", self.address)
        finally:
            sock.close()


class EmergencyHotkey:
    def __init__(self, on_emergency: Callable[[], None]):
        self.on_emergency = on_emergency
        self.stop_event = Event()
        self.thread = Thread(target=self._listen, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _listen(self) -> None:
        if __import__("os").name != "nt":
            return
        import msvcrt
        while not self.stop_event.is_set():
            if msvcrt.kbhit() and msvcrt.getwch().lower() == "e":
                self.on_emergency()
                return
```

The real CLI starts `EmergencyHotkey` before takeoff and prints the fallback command. The callback sends `emergency`, sets the runner's abort event, and prevents all later commands. The fallback sender must bind to the selected Tello adapter when `--local-ip` is supplied; automatic mode selects the adapter first.

- [ ] **Step 4: Implement the CLI**

Create `src/sentinel/cli.py` with these parser and exit-code rules:

```python
import argparse
from pathlib import Path

from .dry_run import DryRunClient
from .emergency import EmergencySender
from .reporting import write_report
from .runner import FlightTestRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--real", action="store_true")
    run.add_argument("--log", type=Path, default=Path("artifacts/run.jsonl"))
    run.add_argument("--local-ip")
    run.add_argument("--interface-name")
    run.add_argument("--interface-index", type=int)
    run.add_argument("--min-battery", type=int, default=40)
    run.add_argument("--confirm", action="store_true")

    emergency = subparsers.add_parser("emergency")
    emergency.add_argument("--local-ip")
    emergency.add_argument("--interface-name")
    emergency.add_argument("--interface-index", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "emergency":
        local_ip = resolve_local_ip(args)
        EmergencySender(local_ip=local_ip).send()
        return 0
    if args.real and not args.confirm:
        print("Real mode requires --confirm after the physical preflight.")
        return 1
    client = DryRunClient() if args.dry_run else build_real_client(args)
    report = FlightTestRunner(client, "dry-run" if args.dry_run else "real", args.min_battery).run(confirm=args.confirm or args.dry_run)
    write_report(report, args.log)
    return {"PASS": 0, "FAIL": 1, "EMERGENCY_ABORTED": 2}[report.status]
```

`build_real_client(args)` and `resolve_local_ip(args)` must call `discover_adapters()`, mark the adapter matching `capture_default_route().source_ip` as default, select via the requested override or automatic policy, and use the selected local IP. Add the selected interface to the runner's report. The real run must construct `EmergencyHotkey(runner.trigger_emergency)`, start it before `runner.run()`, and stop it in a `finally` block. The `emergency` parser must accept `--interface-name`, `--interface-index`, and `--local-ip`; it must never send using a wildcard local address in automatic mode.

- [ ] **Step 5: Run CLI tests**

Run: `python -m pytest -q`  
Expected: all tests pass. Then run `python -m sentinel run --dry-run --log artifacts/verification.jsonl`; expected exit code `0` and one JSONL report with status `PASS`.

- [ ] **Step 6: Commit CLI and safety controls**

```powershell
git add src/sentinel/reporting.py src/sentinel/emergency.py src/sentinel/cli.py tests/test_cli.py
git commit -m "feat: add SENTINEL CLI and emergency controls"
```

## Task 8: Write user documentation and perform final verification

**Files:**

- Create: `README.md`
- Create: `docs/safety.md`

- [ ] **Step 1: Write the README**

Document these exact commands and facts:

```text
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
python -m sentinel run --dry-run
python -m sentinel run --real --confirm
python -m sentinel emergency
```

Explain that the primary Wi-Fi adapter stays on the internet and the USB adapter must already be connected to the Tello SSID. Explain automatic selection, `--local-ip` and interface overrides, ports `8889`, `8890`, and `11111`, Python 3.11+, the one-frame video check, the software envelope `x/y ±50 cm` and `z 150–250 cm`, and the fact that horizontal location is open-loop rather than measured.

Include upstream links to [dji-sdk/Tello-Python](https://github.com/dji-sdk/Tello-Python), the [official Tello SDK guide](https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf), and the official [`Tello3.py`](https://dl-cdn.ryzerobotics.com/downloads/tello/20180222/Tello3.py) reference.

- [ ] **Step 2: Write the safety checklist**

Create `docs/safety.md` with this checklist:

```text
Before --real:
- Charge the Tello and confirm at least 40% battery.
- Connect the USB Wi-Fi adapter to the Tello SSID.
- Confirm the main Wi-Fi adapter still owns the internet default route.
- Use a clear indoor area substantially larger than the 1 m cube.
- Install propeller guards where appropriate.
- Keep an independent Tello control option ready.
- Confirm that E is the emergency hotkey and know the fallback command.

During the test:
- Keep the operator beside the console.
- Press E for immediate motor stop only in an actual emergency.
- Use Ctrl+C for a recoverable abort that should attempt a landing.
- Do not treat the open-loop x/y plan as measured localization.
```

- [ ] **Step 3: Run the complete verification suite**

Run: `python -m pytest -q`  
Expected: all tests pass with zero failures.

Run: `python -m sentinel run --dry-run --log artifacts/final-dry-run.jsonl`  
Expected: process exits `0`; JSONL contains `"status": "PASS"`, the video frame dimensions, adapter mode `dry-run`, and the approved command list.

Run: `python -m sentinel --help` and `python -m sentinel run --help`  
Expected: both commands exit `0` and show the documented subcommands/options.

Run: `git diff --check` and `git status --short`  
Expected: no whitespace errors; only intended repository files are present, and generated `artifacts/` remains ignored.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md docs/safety.md
git commit -m "docs: document SENTINEL setup and flight safety"
```

- [ ] **Step 5: Record hardware limitation**

Do not claim real-drone verification until the USB adapter is available and a supervised run has actually produced fresh evidence. The first real trial must confirm that the adapter selector chooses the USB interface, the primary internet route remains unchanged, one video frame decodes, the approved commands receive acknowledgements, and emergency stop works from both the hotkey and fallback process.

## Task 9: Publish the private GitHub repository and manage collaborators

**Files:**

- External GitHub repository: `Cols77/SENTINEL`
- Local Git remote: `origin`

- [ ] **Step 1: Verify GitHub CLI authentication and repository privacy**

Run from `C:\coding\sentinel`:

```powershell
gh auth status
gh repo view Cols77/SENTINEL --json nameWithOwner,isPrivate,url,viewerPermission
git remote -v
```

Expected: the authenticated account is `Cols77`, the repository is private, `origin` points to `git@github.com:Cols77/SENTINEL.git`, and the viewer has administrator access.

- [ ] **Step 2: Create or publish the repository only when it does not exist**

If `gh repo view` reports that the repository is missing, run:

```powershell
gh repo create SENTINEL --private --source . --remote origin --push
```

Do not delete, overwrite, or reinitialize an existing remote. If local commits are ahead after implementation, run:

```powershell
git push origin master
```

The official `gh repo create` command supports `--private`, `--source`, `--remote`, and `--push`.

- [ ] **Step 3: Ask the owner for collaborator usernames before sending invitations**

Known GitHub username: `Dzivha`. Ask for any remaining GitHub usernames or email-bound GitHub addresses, and confirm that each supplied identity should receive repository access. Do not guess usernames from display names.

- [ ] **Step 4: Invite each confirmed collaborator with write access**

Current `gh` has no dedicated `gh repo add-collaborator` command, so use its authenticated REST wrapper. For each confirmed username, run:

```powershell
gh api --method PUT "repos/Cols77/SENTINEL/collaborators/USERNAME" -f permission=push
```

Replace `USERNAME` with the exact confirmed GitHub login. A successful response is HTTP `201` for a new invitation or `204` when access already exists. The GitHub REST collaborator API supports `pull`, `triage`, `push`, `maintain`, and `admin`; SENTINEL uses `push` unless the owner explicitly requests another level.

- [ ] **Step 5: Verify collaborator invitations without exposing tokens**

Run:

```powershell
gh api "repos/Cols77/SENTINEL/collaborators" --jq ".[] | [.login, .permissions.push, .permissions.admin] | @tsv"
```

Report the repository URL and each confirmed collaborator's login/status. Never print `GH_TOKEN`, personal access tokens, or credential-store contents.
