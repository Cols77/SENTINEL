"""Read-only local-network discovery and safe Tello adapter selection."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, replace
import errno
from ipaddress import IPv4Address, IPv4Network
import os
import socket
from typing import Any, Callable, Iterable, Protocol

from ctypes import wintypes


TELLO_SUBNET = IPv4Network("192.168.10.0/24")
TELLO_ENDPOINT = "192.168.10.1"
TELLO_COMMAND_PORT = 8889


try:
    from .models import AdapterInfo  # type: ignore[import-not-found]
except ImportError as error:
    # Task 1 supplies the shared model.  Keeping this small fallback makes
    # Task 2 independently testable from the clean setup baseline.
    if "AdapterInfo" not in str(error):
        raise

    @dataclass(frozen=True)
    class AdapterInfo:
        """Metadata for one local IPv4 adapter."""

        name: str
        index: int
        local_ip: str
        is_up: bool
        is_default_route: bool
        gateway: str | None = None
        metric: int | None = None
        netmask: str | None = None


class NetworkSelectionError(RuntimeError):
    """Base error for an unavailable or unsafe adapter selection."""


class RouteDiscoveryError(NetworkSelectionError):
    """Raised when the internet default route cannot be identified safely."""


class NoTelloAdapter(NetworkSelectionError):
    """Raised when no safe adapter matches the requested selection."""


class TelloCommandPortInUse(NetworkSelectionError):
    """Raised when another process already owns the local Tello SDK port."""


class AmbiguousAdapter(NetworkSelectionError):
    """Raised when more than one safe adapter matches the request."""


class DefaultRouteChanged(NetworkSelectionError):
    """Raised when the internet default route differs from its snapshot."""


@dataclass(frozen=True)
class RouteSnapshot:
    """The source address and interface selected by the default route."""

    source_ip: str | None
    interface_index: int | None

    def __post_init__(self) -> None:
        if self.source_ip is not None:
            if not isinstance(self.source_ip, str):
                raise ValueError("source_ip must be a valid IPv4 address or None")
            try:
                address = IPv4Address(self.source_ip)
            except ValueError as error:
                raise ValueError(
                    "source_ip must be a valid IPv4 address or None"
                ) from error
            if address.is_unspecified or address.is_loopback or address.is_multicast:
                raise ValueError(
                    "source_ip must be a usable non-loopback IPv4 address or None"
                )
        if self.interface_index is not None and (
            isinstance(self.interface_index, bool)
            or not isinstance(self.interface_index, int)
            or self.interface_index <= 0
        ):
            raise ValueError("interface_index must be a positive integer or None")


class AdapterProvider(Protocol):
    def __call__(self) -> Iterable[AdapterInfo]: ...


class RouteSnapshotProvider(Protocol):
    def __call__(self) -> RouteSnapshot: ...


class ReachabilityProbe(Protocol):
    def __call__(self, adapter: AdapterInfo) -> bool: ...


_psutil: Any | None = None


def _psutil_module() -> Any:
    global _psutil
    if _psutil is None:
        try:
            import psutil as imported_psutil
        except ImportError as error:
            raise RouteDiscoveryError(
                "cannot discover local network adapters without psutil"
            ) from error
        _psutil = imported_psutil
    return _psutil


def _is_non_loopback_ipv4(value: str) -> bool:
    try:
        address = IPv4Address(value)
    except ValueError:
        return False
    return address.version == 4 and not address.is_loopback


def _is_tello_address(local_ip: str) -> bool:
    try:
        address = IPv4Address(local_ip)
    except ValueError:
        return False
    return address in TELLO_SUBNET and address not in {
        TELLO_SUBNET.network_address,
        TELLO_SUBNET.broadcast_address,
    }


def _has_usable_tello_subnet(adapter: AdapterInfo) -> bool:
    if not _is_tello_address(adapter.local_ip):
        return False
    if adapter.netmask is None:
        return True
    try:
        network = IPv4Network(
            f"{adapter.local_ip}/{adapter.netmask}", strict=False
        )
    except ValueError:
        return False
    return network.prefixlen < 32 and IPv4Address(TELLO_ENDPOINT) in network


def route_source_ip(destination: str, *, local_ip: str | None = None) -> str | None:
    """Return the source address the OS would use for a UDP route.

    UDP ``connect`` resolves a route without transmitting a datagram.  The
    temporary socket is closed on every path and can optionally be bound to a
    candidate address for a route-specific probe.
    """

    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if local_ip is not None:
            probe.bind((local_ip, 0))
        probe.connect((destination, TELLO_COMMAND_PORT))
        source_ip = probe.getsockname()[0]
        if not _is_non_loopback_ipv4(source_ip):
            return None
        return source_ip
    except (OSError, ValueError):
        return None
    finally:
        if probe is not None:
            try:
                probe.close()
            except OSError:
                pass


def resolve_interface_indexes(names: Iterable[str]) -> dict[str, int]:
    """Resolve interface names to OS indexes using read-only socket APIs."""

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

    windows_mapped = _windows_interface_indexes(requested)
    resolved: dict[str, int] = {}
    for name in requested:
        index = mapped.get(name) or windows_mapped.get(name)
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
        resolved[name] = index
    return resolved


def _windows_interface_indexes(names: Iterable[str]) -> dict[str, int]:
    """Map Windows adapter friendly names to native interface indexes."""

    requested = set(names)
    if os.name != "nt" or not requested:
        return {}

    class IPAdapterAddresses(ctypes.Structure):
        pass

    IPAdapterAddresses._fields_ = [
        ("length", wintypes.ULONG),
        ("if_index", wintypes.DWORD),
        ("next", ctypes.POINTER(IPAdapterAddresses)),
        ("adapter_name", ctypes.c_char_p),
        ("first_unicast", ctypes.c_void_p),
        ("first_anycast", ctypes.c_void_p),
        ("first_multicast", ctypes.c_void_p),
        ("first_dns", ctypes.c_void_p),
        ("dns_suffix", wintypes.LPWSTR),
        ("description", wintypes.LPWSTR),
        ("friendly_name", wintypes.LPWSTR),
    ]

    try:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll")
        get_adapters = iphlpapi.GetAdaptersAddresses
        get_adapters.argtypes = [
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.c_void_p,
            ctypes.POINTER(IPAdapterAddresses),
            ctypes.POINTER(wintypes.ULONG),
        ]
        get_adapters.restype = wintypes.ULONG
        size = wintypes.ULONG(0)
        result = get_adapters(socket.AF_INET, 0, None, None, ctypes.byref(size))
        if result != 111 or size.value <= 0:  # ERROR_BUFFER_OVERFLOW
            return {}
        buffer = ctypes.create_string_buffer(size.value)
        head = ctypes.cast(buffer, ctypes.POINTER(IPAdapterAddresses))
        result = get_adapters(
            socket.AF_INET, 0, None, head, ctypes.byref(size)
        )
        if result != 0:  # NO_ERROR
            return {}
    except (AttributeError, OSError, TypeError, ValueError):
        return {}

    resolved: dict[str, int] = {}
    current = head
    while bool(current):
        entry = current.contents
        friendly_name = entry.friendly_name
        description = entry.description
        adapter_name = (
            entry.adapter_name.decode(errors="replace")
            if entry.adapter_name
            else None
        )
        for candidate in (friendly_name, description, adapter_name):
            if candidate in requested and entry.if_index:
                resolved[candidate] = int(entry.if_index)
        current = entry.next
    return resolved


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
    except (AttributeError, OSError) as error:
        raise RouteDiscoveryError(
            "could not discover the interface for the default route source IP"
        ) from error

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
    index = resolve_interface_indexes(matching_names).get(matching_names[0])
    if index is None:
        raise RouteDiscoveryError(
            "could not resolve the default route source IP to one interface"
        )
    return index


def capture_default_route(
    *, adapters: Iterable[AdapterInfo] | None = None
) -> RouteSnapshot:
    """Capture the current internet default-route source and interface."""

    adapter_snapshot = None if adapters is None else tuple(adapters)
    source_ip = route_source_ip("1.1.1.1")
    if source_ip is None:
        raise RouteDiscoveryError(
            "could not determine the source IP for the default route"
        )
    try:
        interface_index = _interface_index_for_source(source_ip, adapter_snapshot)
    except RouteDiscoveryError:
        raise
    except (TypeError, ValueError) as error:
        raise RouteDiscoveryError(
            "could not determine the interface for the default route"
        ) from error
    return RouteSnapshot(source_ip, interface_index)


def assert_default_route_unchanged(
    before: RouteSnapshot, after: RouteSnapshot
) -> None:
    """Raise unless the route source and interface still match exactly."""

    if before != after:
        raise DefaultRouteChanged(
            f"internet route changed from {before} to {after}"
        )


def probe_tello_endpoint(
    adapter: AdapterInfo, *, timeout: float = 1.0
) -> bool:
    """Check a candidate with the harmless SDK ``command`` request.

    The socket is explicitly bound to the candidate local address.  This is
    a reachability hook, not an interface or route configuration operation.
    """

    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(timeout)
        try:
            probe.bind((adapter.local_ip, TELLO_COMMAND_PORT))
        except OSError as error:
            if (error.errno in (errno.EADDRINUSE, 10048)
                    or getattr(error, "winerror", None) == 10048):
                raise TelloCommandPortInUse(
                    f"local UDP port {TELLO_COMMAND_PORT} on {adapter.local_ip} is already in use; "
                    "close the other Tello session before starting this one"
                ) from error
            raise
        probe.connect((TELLO_ENDPOINT, TELLO_COMMAND_PORT))
        if probe.getsockname()[0] != adapter.local_ip:
            return False
        probe.send(b"command")
        return probe.recv(1024).strip().lower() == b"ok"
    except (OSError, ValueError):
        return False
    finally:
        if probe is not None:
            try:
                probe.close()
            except OSError:
                pass


def _active_ipv4_adapters(psutil_module: Any) -> tuple[AdapterInfo, ...]:
    stats = psutil_module.net_if_stats()
    addresses_by_name = psutil_module.net_if_addrs()
    active_names = [
        name
        for name, addresses in addresses_by_name.items()
        if stats.get(name) is not None
        and bool(stats[name].isup)
        and any(
            address.family == socket.AF_INET
            and _is_non_loopback_ipv4(address.address)
            for address in addresses
        )
    ]
    indexes = resolve_interface_indexes(active_names)
    discovered: list[AdapterInfo] = []
    for name in active_names:
        index = indexes.get(name)
        if index is None:
            continue
        for address in addresses_by_name[name]:
            if address.family != socket.AF_INET or not _is_non_loopback_ipv4(
                address.address
            ):
                continue
            try:
                discovered.append(
                    AdapterInfo(
                        name=name,
                        index=index,
                        local_ip=address.address,
                        is_up=True,
                        is_default_route=False,
                        netmask=getattr(address, "netmask", None),
                    )
                )
            except (TypeError, ValueError) as error:
                raise RouteDiscoveryError(
                    f"invalid address metadata for interface {name}"
                ) from error
    return tuple(discovered)


def _copy_with_default_route(
    adapter: AdapterInfo, *, is_default_route: bool
) -> AdapterInfo:
    try:
        return replace(adapter, is_default_route=is_default_route)
    except (TypeError, ValueError):
        return AdapterInfo(
            name=adapter.name,
            index=adapter.index,
            local_ip=adapter.local_ip,
            is_up=adapter.is_up,
            is_default_route=is_default_route,
            gateway=getattr(adapter, "gateway", None),
            metric=getattr(adapter, "metric", None),
            netmask=getattr(adapter, "netmask", None),
        )


def discover_adapters(
    *,
    adapter_provider: AdapterProvider | None = None,
    route_snapshot_provider: RouteSnapshotProvider | None = None,
) -> tuple[AdapterInfo, ...]:
    """Discover active adapters, with injectable read-only data sources."""

    if adapter_provider is None:
        adapters = _active_ipv4_adapters(_psutil_module())
    else:
        if not callable(adapter_provider):
            raise TypeError("adapter_provider must be callable")
        adapters = tuple(adapter_provider())

    if route_snapshot_provider is None:
        route = capture_default_route(adapters=adapters)
    else:
        if not callable(route_snapshot_provider):
            raise TypeError("route_snapshot_provider must be callable")
        route = route_snapshot_provider()
    if not isinstance(route, RouteSnapshot):
        raise RouteDiscoveryError("default route snapshot has an invalid type")
    if route.source_ip is None or route.interface_index is None:
        raise RouteDiscoveryError(
            "default route snapshot is incomplete; source IP and interface are required"
        )

    matches = [
        adapter
        for adapter in adapters
        if adapter.local_ip == route.source_ip
        and adapter.index == route.interface_index
    ]
    if len(matches) != 1:
        raise RouteDiscoveryError(
            "could not associate the default route with one discovered interface"
        )
    return tuple(
        _copy_with_default_route(
            adapter,
            is_default_route=(
                adapter.local_ip == route.source_ip
                and adapter.index == route.interface_index
            ),
        )
        for adapter in adapters
    )


def _candidate_diagnostics(
    candidates: Iterable[AdapterInfo],
    before_route: RouteSnapshot,
    after_route: RouteSnapshot | None = None,
) -> str:
    details = "; ".join(
        (
            f"name={candidate.name!r}, index={candidate.index}, "
            f"local_ip={candidate.local_ip}, netmask={candidate.netmask}, "
            f"gateway={getattr(candidate, 'gateway', None)}, "
            f"metric={getattr(candidate, 'metric', None)}, "
            f"is_up={candidate.is_up}, "
            f"is_default_route={candidate.is_default_route}"
        )
        for candidate in candidates
    ) or "none"
    route_details = f"route snapshot={before_route!r}"
    if after_route is not None:
        route_details += f", current RouteSnapshot={after_route!r}"
    return f"candidate adapters: [{details}]; {route_details}"


def _is_safe_candidate(
    adapter: AdapterInfo,
    default_route: RouteSnapshot,
    reachability_probe: ReachabilityProbe,
) -> bool:
    if not adapter.is_up or adapter.is_default_route:
        return False
    if not _has_usable_tello_subnet(adapter):
        return False
    if (
        adapter.local_ip == default_route.source_ip
        or adapter.index == default_route.interface_index
    ):
        return False
    return _probe_with_retry(adapter, reachability_probe)


#: Timeout of the one retry a Tello-subnet candidate gets after its first
#: probe missed (L0 finding 2026-09-23: the 1 s probe can miss first contact).
TELLO_RETRY_TIMEOUT_S = 2.5


def _probe_with_retry(adapter: AdapterInfo, reachability_probe: ReachabilityProbe) -> bool:
    """Probe once; a candidate inside 192.168.10.0/24 gets exactly one more
    probe (the default probe with a longer timeout) before it is refused.
    Callers have already applied every safety rule (default route, subnet)."""
    try:
        if reachability_probe(adapter):
            return True
    except TelloCommandPortInUse:
        raise
    except Exception:
        pass
    if not _is_tello_address(adapter.local_ip):
        return False
    try:
        if reachability_probe is _DEFAULT_PROBE_MARKER:
            return bool(probe_tello_endpoint(adapter, timeout=TELLO_RETRY_TIMEOUT_S))
        return bool(reachability_probe(adapter))
    except TelloCommandPortInUse:
        raise
    except Exception:
        return False


def _default_reachability_probe(adapter: AdapterInfo) -> bool:
    """The default probe, resolved at call time (so tests may substitute it)."""
    return probe_tello_endpoint(adapter)


_DEFAULT_PROBE_MARKER = _default_reachability_probe


class NetworkAdapterSelector:
    """Select exactly one reachable Tello adapter without host mutation."""

    def __init__(
        self,
        adapters: Iterable[AdapterInfo] | AdapterProvider | None = None,
        *,
        adapter_provider: AdapterProvider | None = None,
        default_route: RouteSnapshot | None = None,
        reachability_probe: ReachabilityProbe | None = None,
        route_snapshot_provider: RouteSnapshotProvider | None = None,
    ) -> None:
        if adapters is not None and adapter_provider is not None:
            raise NetworkSelectionError(
                "provide adapters or adapter_provider, not both"
            )
        source = adapter_provider if adapter_provider is not None else adapters
        if source is None:
            source = lambda: discover_adapters()
        if not callable(source):
            fixed_adapters = tuple(source)
            self._adapter_provider: AdapterProvider = lambda: fixed_adapters
            self.adapters = fixed_adapters
        else:
            self._adapter_provider = source
            self.adapters = None

        if reachability_probe is not None and not callable(reachability_probe):
            raise TypeError("reachability_probe must be callable or None")
        if route_snapshot_provider is not None and not callable(
            route_snapshot_provider
        ):
            raise TypeError("route_snapshot_provider must be callable or None")
        self.default_route = default_route
        self.reachability_probe = (
            reachability_probe
            if reachability_probe is not None
            else _default_reachability_probe
        )
        self.route_snapshot_provider = route_snapshot_provider

    def _snapshot_adapters(self) -> tuple[AdapterInfo, ...]:
        try:
            adapters = tuple(self._adapter_provider())
        except NetworkSelectionError:
            raise
        except Exception as error:
            raise RouteDiscoveryError(
                "could not discover local network adapters"
            ) from error
        return adapters

    def _selection_route(
        self,
        adapters: tuple[AdapterInfo, ...],
        *,
        use_supplied_default: bool = False,
    ) -> RouteSnapshot:
        try:
            if self.route_snapshot_provider is not None:
                route = self.route_snapshot_provider()
            elif self.default_route is not None and use_supplied_default:
                route = self.default_route
            else:
                route = capture_default_route(adapters=adapters)
        except NetworkSelectionError:
            raise
        except Exception as error:
            raise RouteDiscoveryError(
                "could not capture the internet default route"
            ) from error
        if not isinstance(route, RouteSnapshot):
            raise RouteDiscoveryError("default route snapshot has an invalid type")
        if route.source_ip is None or route.interface_index is None:
            raise RouteDiscoveryError(
                "default route snapshot is incomplete; source IP and interface are required"
            )
        return route

    def select(
        self,
        *,
        local_ip: str | None = None,
        interface_name: str | None = None,
        interface_index: int | None = None,
    ) -> AdapterInfo:
        overrides = (local_ip, interface_name, interface_index)
        if sum(value is not None for value in overrides) > 1:
            raise NetworkSelectionError(
                "exactly one adapter override may be supplied"
            )

        adapters = self._snapshot_adapters()
        before_route = self._selection_route(
            adapters, use_supplied_default=True
        )
        explicit = any(value is not None for value in overrides)
        if local_ip is not None:
            raw_candidates = [
                adapter for adapter in adapters if adapter.local_ip == local_ip
            ]
        elif interface_name is not None:
            raw_candidates = [
                adapter for adapter in adapters if adapter.name == interface_name
            ]
        elif interface_index is not None:
            raw_candidates = [
                adapter for adapter in adapters if adapter.index == interface_index
            ]
        else:
            raw_candidates = list(adapters)

        if explicit and len(raw_candidates) > 1:
            after_route = self._selection_route(adapters)
            try:
                assert_default_route_unchanged(before_route, after_route)
            except DefaultRouteChanged as error:
                raise DefaultRouteChanged(
                    f"{error}; "
                    f"{_candidate_diagnostics(raw_candidates, before_route, after_route)}"
                ) from error
            raise AmbiguousAdapter(
                "multiple adapters matched the override; "
                f"{_candidate_diagnostics(raw_candidates, before_route, after_route)}"
            )

        safe_candidates = [
            adapter
            for adapter in raw_candidates
            if _is_safe_candidate(
                adapter, before_route, self.reachability_probe
            )
        ]

        after_route = self._selection_route(adapters)
        try:
            assert_default_route_unchanged(before_route, after_route)
        except DefaultRouteChanged as error:
            raise DefaultRouteChanged(
                f"{error}; "
                f"{_candidate_diagnostics(raw_candidates, before_route, after_route)}"
            ) from error

        if not safe_candidates:
            if explicit:
                raise NoTelloAdapter(
                    "selected adapter is inactive, unreachable, outside the "
                    "Tello subnet, or owns the default route; "
                    f"{_candidate_diagnostics(raw_candidates, before_route, after_route)}"
                )
            raise NoTelloAdapter(
                "no safe Tello adapter matched the requested selection; "
                f"{_candidate_diagnostics(raw_candidates, before_route, after_route)}"
            )
        if len(safe_candidates) > 1:
            raise AmbiguousAdapter(
                "multiple safe Tello adapters matched; use --local-ip, "
                "--interface-name, or --interface-index; "
                f"{_candidate_diagnostics(raw_candidates, before_route, after_route)}"
            )
        return safe_candidates[0]

    @staticmethod
    def assert_default_route_unchanged(
        before: RouteSnapshot, after: RouteSnapshot
    ) -> None:
        assert_default_route_unchanged(before, after)

    @staticmethod
    def _is_tello_address(local_ip: str) -> bool:
        return _is_tello_address(local_ip)
