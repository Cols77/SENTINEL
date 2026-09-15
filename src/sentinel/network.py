"""Read-only network discovery and safe Tello adapter selection."""

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
import socket
from typing import Callable, Iterable

from .models import AdapterInfo


psutil = None


TELLO_SUBNET = IPv4Network("192.168.10.0/24")
TELLO_ENDPOINT = "192.168.10.1"


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
                source_address = IPv4Address(self.source_ip)
            except ValueError as exc:
                raise ValueError(
                    "source_ip must be a valid IPv4 address or None"
                ) from exc
            if (
                source_address.is_unspecified
                or source_address.is_loopback
                or source_address.is_multicast
            ):
                raise ValueError(
                    "source_ip must be a usable non-loopback IPv4 address or None"
                )
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


def route_source_ip(destination: str, *, local_ip: str | None = None) -> str | None:
    """Return the local source address selected for a UDP probe.

    UDP ``connect`` does not send a datagram, but asks the OS to resolve the
    route that would be used for the Tello command port.  The socket is always
    closed, including when socket creation, connection, or inspection fails.
    """

    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if local_ip is not None:
            probe.bind((local_ip, 0))
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
        source_address = IPv4Address(source_ip)
    except ValueError as exc:
        raise RouteDiscoveryError(
            "could not determine a usable source IP for the default route"
        ) from exc
    if (
        source_address.is_unspecified
        or source_address.is_loopback
        or source_address.is_multicast
    ):
        raise RouteDiscoveryError(
            "could not determine a usable source IP for the default route"
        )

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


def _probe_tello_endpoint(adapter: AdapterInfo) -> bool:
    """Check Tello reachability with only the harmless SDK ``command``.

    The UDP socket is bound to the candidate address, connected to the Tello
    command endpoint, and required to receive the normalized ``ok`` response.
    This sends only the SDK ``command`` string; it never sends movement
    commands or changes host networking.
    """

    probe: socket.socket | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(1.0)
        probe.bind((adapter.local_ip, 0))
        probe.connect((TELLO_ENDPOINT, 8889))
        if probe.getsockname()[0] != adapter.local_ip:
            return False
        probe.send(b"command")
        response = probe.recv(1024)
        return response.strip() == b"ok"
    except OSError:
        return False
    finally:
        if probe is not None:
            try:
                probe.close()
            except OSError:
                pass


def _is_safe_candidate(
    adapter: AdapterInfo,
    default_route: RouteSnapshot | None,
    reachability_probe: Callable[[AdapterInfo], bool],
) -> bool:
    if not adapter.is_up or adapter.is_default_route:
        return False
    if not _has_usable_tello_subnet(adapter):
        return False
    if default_route is not None and (
        adapter.local_ip == default_route.source_ip
        or adapter.index == default_route.interface_index
    ):
        return False
    try:
        return bool(reachability_probe(adapter))
    except Exception:
        # An injected probe failure is also an unsafe reachability result.
        return False


class NetworkAdapterSelector:
    """Select exactly one safe adapter without changing host networking."""

    def __init__(
        self,
        adapters: Iterable[AdapterInfo],
        *,
        default_route: RouteSnapshot | None = None,
        reachability_probe: Callable[[AdapterInfo], bool] | None = None,
        route_snapshot_provider: Callable[[], RouteSnapshot] | None = None,
    ) -> None:
        self.adapters = tuple(adapters)
        self.default_route = default_route
        if reachability_probe is not None and not callable(reachability_probe):
            raise TypeError("reachability_probe must be callable or None")
        if route_snapshot_provider is not None and not callable(
            route_snapshot_provider
        ):
            raise TypeError("route_snapshot_provider must be callable or None")
        self.reachability_probe = (
            reachability_probe
            if reachability_probe is not None
            else _probe_tello_endpoint
        )
        self.route_snapshot_provider = route_snapshot_provider

    def _selection_route(self) -> RouteSnapshot:
        if self.route_snapshot_provider is not None:
            route = self.route_snapshot_provider()
        elif self.default_route is None:
            # Do not cache discovered routes: the second snapshot in a
            # selection and the next select call must observe current state.
            route = capture_default_route(adapters=self.adapters)
        else:
            route = self.default_route
        if not isinstance(route, RouteSnapshot):
            raise RouteDiscoveryError(
                "default route snapshot must be a RouteSnapshot"
            )
        if (
            route.source_ip is None
            or route.interface_index is None
        ):
            raise RouteDiscoveryError(
                "default route snapshot is incomplete; source IP and interface are required"
            )
        return route

    @staticmethod
    def _candidate_diagnostics(
        candidates: Iterable[AdapterInfo],
        before_route: RouteSnapshot,
        current_route: RouteSnapshot | None = None,
    ) -> str:
        candidate_details = "; ".join(
            (
                f"name={candidate.name!r}, interface_index={candidate.index}, "
                f"local_ip={candidate.local_ip}, netmask={candidate.netmask}, "
                f"gateway={candidate.gateway}, metric={candidate.metric}, "
                f"is_up={candidate.is_up}, "
                f"is_default_route={candidate.is_default_route}"
            )
            for candidate in candidates
        ) or "none"
        route_details = f"route snapshot={before_route!r}"
        if current_route is not None:
            route_details += (
                f", current {current_route.__class__.__name__}={current_route!r}"
            )
        return f"candidate adapters: [{candidate_details}]; {route_details}"

    @classmethod
    def _selection_route_check(
        cls,
        before_route: RouteSnapshot,
        current_route: RouteSnapshot,
        candidates: Iterable[AdapterInfo],
    ) -> None:
        try:
            assert_default_route_unchanged(before_route, current_route)
        except DefaultRouteChanged as exc:
            diagnostics = cls._candidate_diagnostics(
                candidates, before_route, current_route
            )
            raise DefaultRouteChanged(f"{exc}; {diagnostics}") from exc

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
        before_route = self._selection_route()
        if local_ip is not None:
            raw_candidates = [
                adapter for adapter in self.adapters if adapter.local_ip == local_ip
            ]
        elif interface_name is not None:
            raw_candidates = [
                adapter for adapter in self.adapters if adapter.name == interface_name
            ]
        elif interface_index is not None:
            raw_candidates = [
                adapter
                for adapter in self.adapters
                if adapter.index == interface_index
            ]
        else:
            raw_candidates = list(self.adapters)

        if explicit and len(raw_candidates) > 1:
            after_route = self._selection_route()
            self._selection_route_check(before_route, after_route, raw_candidates)
            raise AmbiguousAdapter(
                "multiple adapters matched; pass --local-ip or an interface "
                "override; "
                f"{self._candidate_diagnostics(raw_candidates, before_route, after_route)}"
            )

        probe_results: dict[int, bool] = {}

        def is_safe(candidate: AdapterInfo) -> bool:
            candidate_key = id(candidate)
            if candidate_key not in probe_results:
                probe_results[candidate_key] = _is_safe_candidate(
                    candidate, before_route, self.reachability_probe
                )
            return probe_results[candidate_key]

        candidates = [
            candidate for candidate in raw_candidates if is_safe(candidate)
        ]

        after_route = self._selection_route()
        self._selection_route_check(before_route, after_route, raw_candidates)

        if not candidates:
            if explicit:
                raise NoTelloAdapter(
                    "selected adapter is inactive, unsafe, or owns the default "
                    "route; "
                    f"{self._candidate_diagnostics(raw_candidates, before_route, after_route)}"
                )
            raise NoTelloAdapter(
                "no safe adapter matched the requested selection; "
                f"{self._candidate_diagnostics(raw_candidates, before_route, after_route)}"
            )
        if len(candidates) != 1:
            raise AmbiguousAdapter(
                "multiple adapters matched; pass --local-ip or an interface "
                "override; "
                f"{self._candidate_diagnostics(raw_candidates, before_route, after_route)}"
            )

        return candidates[0]

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
            try:
                adapters.append(
                    AdapterInfo(
                        name=name,
                        index=interface_index,
                        local_ip=address.address,
                        is_up=True,
                        is_default_route=False,
                        netmask=getattr(address, "netmask", None),
                    )
                )
            except ValueError as exc:
                raise RouteDiscoveryError(
                    f"invalid address metadata for interface {name}"
                ) from exc
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
            netmask=adapter.netmask,
        )
        for adapter in adapters
    )
