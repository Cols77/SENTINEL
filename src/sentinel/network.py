"""Read-only network discovery and safe Tello adapter selection."""

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
import socket
from typing import Iterable

from .models import AdapterInfo


psutil = None


TELLO_SUBNET = IPv4Network("192.168.10.0/24")


class NetworkSelectionError(RuntimeError):
    """Base error for unsafe or unavailable adapter selection."""


class NoTelloAdapter(NetworkSelectionError):
    """Raised when no safe adapter matches the requested selection."""


class AmbiguousAdapter(NetworkSelectionError):
    """Raised when more than one adapter matches a selection."""


class DefaultRouteChanged(NetworkSelectionError):
    """Raised when the host's default route differs from its snapshot."""


@dataclass(frozen=True)
class RouteSnapshot:
    """The source address and interface used by the default-route probe."""

    source_ip: str | None
    interface_index: int | None


def route_source_ip(destination: str) -> str | None:
    """Return the local source address selected for a UDP probe.

    UDP ``connect`` does not send a datagram, but asks the OS to resolve the
    route that would be used for the Tello command port.  The socket is always
    closed, including when socket creation, connection, or inspection fails.
    """

    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((destination, 8889))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        if probe is not None:
            try:
                probe.close()
            except OSError:
                pass


def capture_default_route() -> RouteSnapshot:
    """Capture the source address chosen for the internet default route."""

    return RouteSnapshot(source_ip=route_source_ip("1.1.1.1"), interface_index=None)


def assert_default_route_unchanged(
    before: RouteSnapshot, after: RouteSnapshot
) -> None:
    """Raise if two default-route snapshots are not identical."""

    if before != after:
        raise DefaultRouteChanged(
            f"internet route changed from {before} to {after}"
        )


class NetworkAdapterSelector:
    """Select exactly one safe adapter without changing host networking."""

    def __init__(self, adapters: Iterable[AdapterInfo]) -> None:
        self.adapters = tuple(adapters)

    def select(
        self,
        *,
        local_ip: str | None = None,
        interface_name: str | None = None,
        interface_index: int | None = None,
    ) -> AdapterInfo:
        explicit = (
            local_ip is not None
            or interface_name is not None
            or interface_index is not None
        )

        if local_ip is not None:
            candidates = [
                adapter for adapter in self.adapters if adapter.local_ip == local_ip
            ]
        elif interface_name is not None:
            candidates = [
                adapter for adapter in self.adapters if adapter.name == interface_name
            ]
        elif interface_index is not None:
            candidates = [
                adapter
                for adapter in self.adapters
                if adapter.index == interface_index
            ]
        else:
            candidates = [
                adapter
                for adapter in self.adapters
                if (
                    adapter.is_up
                    and not adapter.is_default_route
                    and self._is_tello_address(adapter.local_ip)
                )
            ]

        if not candidates:
            raise NoTelloAdapter("no adapter matched the requested selection")
        if len(candidates) != 1:
            raise AmbiguousAdapter(
                "multiple adapters matched; pass --local-ip or an interface override"
            )

        selected = candidates[0]
        if explicit and (not selected.is_up or selected.is_default_route):
            raise NoTelloAdapter("selected adapter is inactive or owns the default route")
        return selected

    @staticmethod
    def assert_default_route_unchanged(
        before: RouteSnapshot, after: RouteSnapshot
    ) -> None:
        assert_default_route_unchanged(before, after)

    @staticmethod
    def _is_tello_address(local_ip: str) -> bool:
        try:
            return IPv4Address(local_ip) in TELLO_SUBNET
        except ValueError:
            return False


def discover_adapters() -> tuple[AdapterInfo, ...]:
    """Discover active IPv4 adapters using psutil without changing routes."""

    psutil_module = psutil
    if psutil_module is None:
        import psutil as psutil_module

    stats = psutil_module.net_if_stats()
    default_route = capture_default_route()
    result: list[AdapterInfo] = []

    for name, addresses in psutil_module.net_if_addrs().items():
        stat = stats.get(name)
        if stat is None or not stat.isup:
            continue
        try:
            interface_index = socket.if_nametoindex(name)
        except OSError:
            continue

        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            result.append(
                AdapterInfo(
                    name=name,
                    index=interface_index,
                    local_ip=address.address,
                    is_up=True,
                    is_default_route=address.address == default_route.source_ip,
                )
            )

    return tuple(result)
