from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sentinel import network
from sentinel.models import AdapterInfo
from sentinel.network import (
    AmbiguousAdapter,
    DefaultRouteChanged,
    NetworkAdapterSelector,
    NetworkSelectionError,
    NoTelloAdapter,
    RouteDiscoveryError,
    RouteSnapshot,
)


DEFAULT_ROUTE = RouteSnapshot("192.168.1.44", 7)


def reachable(_adapter: AdapterInfo) -> bool:
    return True


def test_resolve_interface_indexes_uses_windows_friendly_name_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(network.os, "name", "nt")
    monkeypatch.setattr(
        network,
        "_windows_interface_indexes",
        lambda _names: {"Wi-Fi": 7},
    )
    monkeypatch.setattr(network.socket, "if_nameindex", lambda: [])

    assert network.resolve_interface_indexes(["Wi-Fi"]) == {"Wi-Fi": 7}


def selector_for_test(
    adapters, *, default_route: RouteSnapshot | None = DEFAULT_ROUTE, **kwargs
) -> NetworkAdapterSelector:
    kwargs.setdefault("reachability_probe", reachable)
    if default_route is not None:
        kwargs.setdefault("route_snapshot_provider", lambda: default_route)
    return NetworkAdapterSelector(adapters, default_route=default_route, **kwargs)


def test_auto_selection_prefers_reachable_tello_subnet_adapter() -> None:
    adapters = [
        AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True),
        AdapterInfo(
            "USB Wi-Fi",
            19,
            "192.168.10.2",
            True,
            False,
            netmask="255.255.255.0",
        ),
    ]

    selected = selector_for_test(adapters).select()

    assert selected == adapters[1]


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"local_ip": "192.168.10.2"}, "USB Wi-Fi A"),
        ({"interface_name": "USB Wi-Fi B"}, "USB Wi-Fi B"),
        ({"interface_index": 20}, "USB Wi-Fi B"),
    ],
)
def test_explicit_local_ip_name_and_index_overrides_select_one_adapter(
    override: dict[str, object], expected: str
) -> None:
    adapters = [
        AdapterInfo(
            "USB Wi-Fi A",
            19,
            "192.168.10.2",
            True,
            False,
            netmask="255.255.255.0",
        ),
        AdapterInfo(
            "USB Wi-Fi B",
            20,
            "192.168.10.3",
            True,
            False,
            netmask="255.255.255.0",
        ),
    ]

    selected = selector_for_test(adapters).select(**override)

    assert selected.name == expected


def test_conflicting_overrides_are_rejected() -> None:
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )

    with pytest.raises(NetworkSelectionError, match="exactly one"):
        selector_for_test([adapter]).select(local_ip=adapter.local_ip, interface_index=19)


def test_ambiguous_auto_selection_fails_with_candidate_names() -> None:
    adapters = [
        AdapterInfo(
            "USB Wi-Fi A",
            19,
            "192.168.10.2",
            True,
            False,
            netmask="255.255.255.0",
        ),
        AdapterInfo(
            "USB Wi-Fi B",
            20,
            "192.168.10.3",
            True,
            False,
            netmask="255.255.255.0",
        ),
    ]

    with pytest.raises(AmbiguousAdapter) as error:
        selector_for_test(adapters).select()

    assert "USB Wi-Fi A" in str(error.value)
    assert "USB Wi-Fi B" in str(error.value)


def test_no_reachable_candidate_fails_closed() -> None:
    calls: list[str] = []
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )

    def unreachable(candidate: AdapterInfo) -> bool:
        calls.append(candidate.local_ip)
        return False

    with pytest.raises(NoTelloAdapter):
        NetworkAdapterSelector(
            [adapter],
            default_route=DEFAULT_ROUTE,
            reachability_probe=unreachable,
            route_snapshot_provider=lambda: DEFAULT_ROUTE,
        ).select(local_ip=adapter.local_ip)

    assert calls == [adapter.local_ip]


def test_injected_adapter_discovery_is_read_only_and_used_once() -> None:
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )
    calls: list[object] = []

    def discover() -> tuple[AdapterInfo, ...]:
        calls.append(object())
        return (adapter,)

    selected = NetworkAdapterSelector(
        discover,
        default_route=DEFAULT_ROUTE,
        reachability_probe=reachable,
        route_snapshot_provider=lambda: DEFAULT_ROUTE,
    ).select()

    assert selected == adapter
    assert len(calls) == 1
    assert adapter == AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )


def test_reachability_hook_exceptions_are_treated_as_unreachable() -> None:
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )

    def broken_probe(_candidate: AdapterInfo) -> bool:
        raise OSError("probe failed")

    with pytest.raises(NoTelloAdapter):
        NetworkAdapterSelector(
            [adapter],
            default_route=DEFAULT_ROUTE,
            reachability_probe=broken_probe,
            route_snapshot_provider=lambda: DEFAULT_ROUTE,
        ).select()


def test_changed_default_route_after_selection_is_rejected() -> None:
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )
    snapshots = iter(
        [DEFAULT_ROUTE, RouteSnapshot("192.168.1.99", 8)]
    )

    with pytest.raises(DefaultRouteChanged, match="route changed"):
        NetworkAdapterSelector(
            [adapter],
            reachability_probe=reachable,
            route_snapshot_provider=lambda: next(snapshots),
        ).select()


def test_incomplete_default_route_snapshot_is_rejected_before_selection() -> None:
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )

    with pytest.raises(RouteDiscoveryError, match="complete"):
        NetworkAdapterSelector(
            [adapter],
            reachability_probe=reachable,
            route_snapshot_provider=lambda: RouteSnapshot(None, None),
        ).select(local_ip=adapter.local_ip)


def test_route_snapshot_is_immutable_and_compares_source_and_interface() -> None:
    before = RouteSnapshot("192.168.1.44", 7)

    with pytest.raises(FrozenInstanceError):
        before.source_ip = "192.168.1.99"

    with pytest.raises(DefaultRouteChanged):
        NetworkAdapterSelector.assert_default_route_unchanged(
            before, RouteSnapshot("192.168.1.44", 8)
        )


def test_discover_adapters_marks_default_route_from_injected_snapshot() -> None:
    adapters = (
        AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, False),
        AdapterInfo(
            "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
        ),
    )

    discovered = network.discover_adapters(
        adapter_provider=lambda: adapters,
        route_snapshot_provider=lambda: DEFAULT_ROUTE,
    )

    assert discovered[0].is_default_route is True
    assert discovered[0].index == 7
    assert discovered[1].is_default_route is False


class FakeProbe:
    def __init__(self) -> None:
        self.bound_to: tuple[str, int] | None = None
        self.connected_to: tuple[str, int] | None = None
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        pass

    def bind(self, address: tuple[str, int]) -> None:
        self.bound_to = address

    def connect(self, address: tuple[str, int]) -> None:
        self.connected_to = address

    def getsockname(self) -> tuple[str, int]:
        assert self.bound_to is not None
        return self.bound_to[0], 45678

    def send(self, payload: bytes) -> int:
        self.sent.append(payload)
        return len(payload)

    def recv(self, _size: int) -> bytes:
        return b"\r\nok\r\n"

    def close(self) -> None:
        self.closed = True


def test_default_reachability_probe_binds_candidate_and_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = FakeProbe()
    monkeypatch.setattr(network.socket, "socket", lambda *_args: probe)
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )

    assert network.probe_tello_endpoint(adapter) is True
    assert probe.bound_to == (adapter.local_ip, 8889)
    assert probe.connected_to == ("192.168.10.1", 8889)
    assert probe.sent == [b"command"]
    assert probe.closed is True
