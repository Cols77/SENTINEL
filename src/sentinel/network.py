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


class RouteDiscoveryError(NetworkSelectionError):
    """Raised when the default route cannot be identified safely."""


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

    def __post_init__(self) -> None:
        if self.source_ip is not None:
            if not isinstance(self.source_ip, str):
                raise ValueError("source_ip must be a valid IPv4 address or None")
            try:
                IPv4Address(self.source_ip)
            except ValueError as exc:
                raise ValueError(
                    "source_ip must be a valid IPv4 address or None"
                ) from exc
        if (
            self.interface_index is not None
            and (
                isinstance(self.interface_index, bool)
                or not isinstance(self.interface_index, int)
                or self.interface_index <= 0
            )
        ):
            raise ValueError(
                "interface_index must be a positive integer or None"
            )


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


def resolve_interface_indexes(names: Iterable[str]) -> dict[str, int]:
    """Resolve interface names to OS indexes without changing networking.

    Windows exposes the complete name/index mapping through
    :func:`socket.if_nameindex` on supported Python versions.  If that
    mapping is unavailable, each requested name is resolved independently via
    :func:`socket.if_nametoindex`.  Unresolvable names are omitted rather than
    assigned a guessed index.
    """

    requested = tuple(dict.fromkeys(names))
    mapped: dict[str, int] = {}
    try:
        for index, name in socket.if_nameindex():
            if name not in requested:
                continue
            if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
                continue
            mapped[name] = index
    except (AttributeError, NotImplementedError, OSError, TypeError, ValueError):
        mapped.clear()

    resolved: dict[str, int] = {}
    for name in requested:
        index = mapped.get(name)
        if index is None:
            try:
                index = socket.if_nametoindex(name)
            except (
                AttributeError,
                KeyError,
                NotImplementedError,
                OSError,
                TypeError,
                ValueError,
            ):
                continue
            if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
                continue
        if index is None:
            continue
        resolved[name] = index
    return resolved


def _psutil_module():
    global psutil
    if psutil is None:
        try:
            import psutil as imported_psutil
        except ImportError as exc:
            raise RouteDiscoveryError(
                "cannot discover the interface for the default route"
            ) from exc
        psutil = imported_psutil
    return psutil


def _interface_index_for_source(
    source_ip: str, adapters: Iterable[AdapterInfo] | None
) -> int:
    if adapters is not None:
        matches = [adapter for adapter in adapters if adapter.local_ip == source_ip]
        if len(matches) != 1:
            raise RouteDiscoveryError(
                "could not resolve the default route source IP to one interface"
            )
        return matches[0].index

    try:
        addresses_by_name = _psutil_module().net_if_addrs()
    except (AttributeError, OSError) as exc:
        raise RouteDiscoveryError(
            "could not discover the interface for the default route source IP"
        ) from exc

    matching_names = [
        name
        for name, addresses in addresses_by_name.items()
        if any(
            address.family == socket.AF_INET and address.address == source_ip
            for address in addresses
        )
    ]
    if len(matching_names) != 1:
        raise RouteDiscoveryError(
            "could not resolve the default route source IP to one interface"
        )

    interface_index = resolve_interface_indexes(matching_names).get(matching_names[0])
    if interface_index is None:
        raise RouteDiscoveryError(
            "could not resolve the default route source IP to one interface"
        )
    return interface_index


def capture_default_route(
    *, adapters: Iterable[AdapterInfo] | None = None
) -> RouteSnapshot:
    """Capture a complete source-IP/interface snapshot for the internet route.

    The route probe is read-only.  When adapter metadata is supplied, it is
    used to map the selected source address to an interface index.  Otherwise
    the same mapping is discovered from psutil addresses and socket indexes.
    Missing or ambiguous route information raises instead of producing a
    snapshot that could make every interface appear non-default.
    """

    source_ip = route_source_ip("1.1.1.1")
    if source_ip is None:
        raise RouteDiscoveryError(
            "could not determine the source IP for the default route"
        )
    try:
        if IPv4Address(source_ip).is_loopback:
            raise ValueError
    except ValueError as exc:
        raise RouteDiscoveryError(
            "could not determine a usable source IP for the default route"
        ) from exc

    interface_index = _interface_index_for_source(source_ip, adapters)
    return RouteSnapshot(source_ip=source_ip, interface_index=interface_index)


def assert_default_route_unchanged(
    before: RouteSnapshot, after: RouteSnapshot
) -> None:
    """Raise if either source address or interface differs from the snapshot."""

    if before != after:
        raise DefaultRouteChanged(
            f"internet route changed from {before} to {after}"
        )


def _is_tello_address(local_ip: str) -> bool:
    try:
        address = IPv4Address(local_ip)
    except ValueError:
        return False
    return address in TELLO_SUBNET and address not in (
        TELLO_SUBNET.network_address,
        TELLO_SUBNET.broadcast_address,
    )


def _is_safe_candidate(
    adapter: AdapterInfo, default_route: RouteSnapshot | None
) -> bool:
    if not adapter.is_up or adapter.is_default_route:
        return False
    if not _is_tello_address(adapter.local_ip):
        return False
    if default_route is not None and (
        adapter.local_ip == default_route.source_ip
        or adapter.index == default_route.interface_index
    ):
        return False
    return True


class NetworkAdapterSelector:
    """Select exactly one safe adapter without changing host networking."""

    def __init__(
        self,
        adapters: Iterable[AdapterInfo],
        *,
        default_route: RouteSnapshot | None = None,
    ) -> None:
        self.adapters = tuple(adapters)
        self.default_route = default_route

    def _selection_route(self) -> RouteSnapshot:
        if self.default_route is None:
            self.default_route = capture_default_route(adapters=self.adapters)
        if not isinstance(self.default_route, RouteSnapshot):
            raise RouteDiscoveryError(
                "default route snapshot must be a RouteSnapshot"
            )
        if (
            self.default_route.source_ip is None
            or self.default_route.interface_index is None
        ):
            raise RouteDiscoveryError(
                "default route snapshot is incomplete; source IP and interface are required"
            )
        return self.default_route

    def select(
        self,
        *,
        local_ip: str | None = None,
        interface_name: str | None = None,
        interface_index: int | None = None,
    ) -> AdapterInfo:
        overrides = tuple(
            value is not None
            for value in (local_ip, interface_name, interface_index)
        )
        if sum(overrides) > 1:
            raise NetworkSelectionError(
                "exactly one adapter override may be supplied"
            )

        explicit = any(overrides)
        default_route = self._selection_route()
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
                if _is_safe_candidate(adapter, default_route)
            ]

        if not candidates:
            raise NoTelloAdapter("no adapter matched the requested selection")
        if len(candidates) != 1:
            raise AmbiguousAdapter(
                "multiple adapters matched; pass --local-ip or an interface override"
            )

        selected = candidates[0]
        if not _is_safe_candidate(selected, default_route):
            if explicit:
                raise NoTelloAdapter(
                    "selected adapter is inactive, unsafe, or owns the default route"
                )
            raise NoTelloAdapter("no safe adapter matched the requested selection")
        return selected

    @staticmethod
    def assert_default_route_unchanged(
        before: RouteSnapshot, after: RouteSnapshot
    ) -> None:
        assert_default_route_unchanged(before, after)

    @staticmethod
    def _is_tello_address(local_ip: str) -> bool:
        """Preserve the original subnet predicate for existing callers."""

        return _is_tello_address(local_ip)


def _active_ipv4_adapters(psutil_module) -> tuple[AdapterInfo, ...]:
    stats = psutil_module.net_if_stats()
    addresses_by_name = psutil_module.net_if_addrs()
    eligible_names = [
        name
        for name, addresses in addresses_by_name.items()
        if (stats.get(name) is not None and stats[name].isup)
        and any(
            address.family == socket.AF_INET
            and _is_non_loopback_address(address.address)
            for address in addresses
        )
    ]
    indexes = resolve_interface_indexes(eligible_names)
    adapters: list[AdapterInfo] = []
    for name in eligible_names:
        interface_index = indexes.get(name)
        if interface_index is None:
            continue
        for address in addresses_by_name[name]:
            if address.family != socket.AF_INET:
                continue
            try:
                parsed_address = IPv4Address(address.address)
            except ValueError:
                continue
            if parsed_address.is_loopback:
                continue
            adapters.append(
                AdapterInfo(
                    name=name,
                    index=interface_index,
                    local_ip=address.address,
                    is_up=True,
                    is_default_route=False,
                )
            )
    return tuple(adapters)


def _is_non_loopback_address(local_ip: str) -> bool:
    try:
        return not IPv4Address(local_ip).is_loopback
    except ValueError:
        return False


def discover_adapters() -> tuple[AdapterInfo, ...]:
    """Discover active, non-loopback IPv4 adapters using read-only APIs."""

    adapters = _active_ipv4_adapters(_psutil_module())
    default_route = capture_default_route(adapters=adapters)
    if (
        default_route.source_ip is None
        or default_route.interface_index is None
    ):
        raise RouteDiscoveryError(
            "default route snapshot is incomplete; source IP and interface are required"
        )

    matching = [
        adapter
        for adapter in adapters
        if adapter.local_ip == default_route.source_ip
        and adapter.index == default_route.interface_index
    ]
    if len(matching) != 1:
        raise RouteDiscoveryError(
            "could not associate the default route with one discovered interface"
        )

    return tuple(
        AdapterInfo(
            name=adapter.name,
            index=adapter.index,
            local_ip=adapter.local_ip,
            is_up=adapter.is_up,
            is_default_route=(
                adapter.local_ip == default_route.source_ip
                and adapter.index == default_route.interface_index
            ),
            gateway=adapter.gateway,
            metric=adapter.metric,
        )
        for adapter in adapters
    )
