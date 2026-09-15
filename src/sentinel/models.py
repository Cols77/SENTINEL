"""Shared immutable domain models used by SENTINEL."""

from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network


def _validate_timestamp(timestamp: object, field_name: str) -> None:
    if (
        not isinstance(timestamp, datetime)
        or timestamp.tzinfo is None
        or timestamp.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be a timezone-aware datetime")


@dataclass(frozen=True)
class Position:
    """A drone position expressed as integer centimetres."""

    x: int
    y: int
    z: int

    def __post_init__(self) -> None:
        coordinates = (self.x, self.y, self.z)
        if any(
            isinstance(coordinate, bool) or not isinstance(coordinate, int)
            for coordinate in coordinates
        ):
            raise ValueError("Position coordinates must be integers in centimetres")


@dataclass(frozen=True)
class VideoFrame:
    """A decoded video frame produced by a client."""

    width: int
    height: int
    captured_at: datetime

    def __post_init__(self) -> None:
        if (
            isinstance(self.width, bool)
            or not isinstance(self.width, int)
            or self.width <= 0
            or isinstance(self.height, bool)
            or not isinstance(self.height, int)
            or self.height <= 0
        ):
            raise ValueError("VideoFrame dimensions must be positive integers")
        _validate_timestamp(self.captured_at, "VideoFrame timestamp")


@dataclass(frozen=True)
class AdapterInfo:
    """Immutable metadata for one local IPv4 network adapter.

    ``gateway`` and ``metric`` are optional observations. ``None`` means the
    read-only discovery source did not provide that value; it is never a
    signal to infer or mutate the host's routing configuration.
    ``netmask`` is the optional IPv4 subnet mask reported for the address.
    """

    name: str
    index: int
    local_ip: str
    is_up: bool
    is_default_route: bool
    gateway: str | None = None
    metric: int | None = None
    netmask: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.index, bool)
            or not isinstance(self.index, int)
            or self.index <= 0
        ):
            raise ValueError("AdapterInfo index must be a positive integer")
        if not isinstance(self.local_ip, str):
            raise ValueError("AdapterInfo local_ip must be a valid IPv4 address")
        try:
            IPv4Address(self.local_ip)
        except ValueError as exc:
            raise ValueError(
                "AdapterInfo local_ip must be a valid IPv4 address"
            ) from exc
        if type(self.is_up) is not bool:
            raise ValueError("AdapterInfo is_up must be a bool")
        if type(self.is_default_route) is not bool:
            raise ValueError("AdapterInfo is_default_route must be a bool")
        if self.netmask is not None:
            if not isinstance(self.netmask, str):
                raise ValueError(
                    "AdapterInfo netmask must be a valid IPv4 netmask"
                )
            try:
                mask = IPv4Address(self.netmask)
                IPv4Network(f"0.0.0.0/{mask}")
            except ValueError as exc:
                raise ValueError(
                    "AdapterInfo netmask must be a valid IPv4 netmask"
                ) from exc


@dataclass(frozen=True)
class CommandRecord:
    """An immutable client command and its observed response."""

    command: str
    response: str
    position: Position | None
    timestamp: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("CommandRecord command must be a non-empty string")
        if not isinstance(self.response, str) or not self.response.strip():
            raise ValueError("CommandRecord response must be a non-empty string")
        if self.position is not None and not isinstance(self.position, Position):
            raise ValueError("CommandRecord position must be a Position or None")
        _validate_timestamp(self.timestamp, "CommandRecord timestamp")
