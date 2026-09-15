from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import sentinel.network as network
from sentinel.models import AdapterInfo
from sentinel.network import (
    AmbiguousAdapter,
    DefaultRouteChanged,
    NoTelloAdapter,
    NetworkSelectionError,
    NetworkAdapterSelector,
    RouteDiscoveryError,
    RouteSnapshot,
    capture_default_route,
    discover_adapters,
    resolve_interface_indexes,
    route_source_ip,
)


DEFAULT_ROUTE = RouteSnapshot("192.168.1.44", 7)


def always_reachable(_adapter):
    return True


def selector_for_test(adapters, **kwargs):
    kwargs.setdefault("reachability_probe", always_reachable)
    return NetworkAdapterSelector(adapters, **kwargs)


def test_auto_selection_prefers_tello_subnet_on_non_default_adapter():
    adapters = [
        AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True),
        AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False),
    ]

    selected = selector_for_test(adapters, default_route=DEFAULT_ROUTE).select()

    assert selected.name == "USB Wi-Fi"
    assert selected.local_ip == "192.168.10.2"


def test_selection_rejects_missing_or_ambiguous_adapter():
    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True)],
            default_route=DEFAULT_ROUTE,
        ).select()

    with pytest.raises(AmbiguousAdapter):
        selector_for_test(
            [
                AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True),
                AdapterInfo("USB Wi-Fi A", 19, "192.168.10.2", True, False),
                AdapterInfo("USB Wi-Fi B", 20, "192.168.10.3", True, False),
            ],
            default_route=DEFAULT_ROUTE,
        ).select()


@pytest.mark.parametrize(
    ("kwargs", "expected_name"),
    [
        ({"local_ip": "192.168.10.3"}, "USB Wi-Fi B"),
        ({"interface_name": "USB Wi-Fi A"}, "USB Wi-Fi A"),
        ({"interface_index": 20}, "USB Wi-Fi B"),
    ],
)
def test_explicit_override_selects_exact_adapter(kwargs, expected_name):
    selected = selector_for_test(
        [
            AdapterInfo("USB Wi-Fi A", 19, "192.168.10.2", True, False),
            AdapterInfo("USB Wi-Fi B", 20, "192.168.10.3", True, False),
        ],
        default_route=DEFAULT_ROUTE,
    ).select(**kwargs)

    assert selected.name == expected_name


@pytest.mark.parametrize("field", ["inactive", "default"])
def test_explicit_override_rejects_unsafe_adapter(field):
    adapter = AdapterInfo(
        "USB Wi-Fi",
        19,
        "192.168.10.2",
        is_up=field != "inactive",
        is_default_route=field == "default",
    )

    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [adapter], default_route=DEFAULT_ROUTE
        ).select(interface_index=19)


@pytest.mark.parametrize(
    "adapter",
    [
        AdapterInfo("Ethernet", 3, "10.0.0.2", True, False),
        AdapterInfo("Loopback", 1, "127.0.0.1", True, False),
    ],
)
def test_explicit_override_rejects_non_tello_or_loopback_adapter(adapter):
    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [adapter], default_route=DEFAULT_ROUTE
        ).select(interface_index=adapter.index)


def test_selector_rejects_conflicting_overrides():
    with pytest.raises(NetworkSelectionError, match="exactly one"):
        selector_for_test(
            [AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False)],
            default_route=DEFAULT_ROUTE,
        ).select(local_ip="192.168.10.2", interface_index=19)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"local_ip": "192.168.10.2"},
        {"interface_name": "USB Wi-Fi"},
        {"interface_index": 19},
    ],
)
def test_explicit_override_requires_a_complete_default_route_snapshot(
    monkeypatch, kwargs
):
    monkeypatch.setattr(
        network,
        "capture_default_route",
        lambda adapters=None: RouteSnapshot(None, None),
    )

    with pytest.raises(RouteDiscoveryError, match="complete"):
        selector_for_test(
            [AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False)]
        ).select(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"local_ip": "192.168.10.2"},
        {"interface_name": "USB Wi-Fi"},
        {"interface_index": 19},
    ],
)
def test_explicit_override_rejects_candidate_owned_by_current_default_route(kwargs):
    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False)],
            default_route=RouteSnapshot("192.168.10.2", 19),
        ).select(**kwargs)


@pytest.mark.parametrize(
    "source_ip",
    ["not-an-ip", "2001:db8::1", "", "0.0.0.0", "127.0.0.1", "224.0.0.1"],
)
def test_route_snapshot_rejects_malformed_source_ip(source_ip):
    with pytest.raises(ValueError, match="source_ip"):
        RouteSnapshot(source_ip, 7)


@pytest.mark.parametrize("interface_index", [True, 0, -1, 1.5, "7"])
def test_route_snapshot_rejects_invalid_interface_index(interface_index):
    with pytest.raises(ValueError, match="interface_index"):
        RouteSnapshot("192.168.1.44", interface_index)


@pytest.mark.parametrize("local_ip", ["192.168.10.0", "192.168.10.255"])
def test_selection_rejects_tello_network_and_broadcast_addresses(local_ip):
    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [AdapterInfo("USB Wi-Fi", 19, local_ip, True, False)],
            default_route=DEFAULT_ROUTE,
        ).select(local_ip=local_ip)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"index": True},
        {"index": 0},
        {"index": -1},
        {"local_ip": "not-an-ip"},
        {"local_ip": "2001:db8::1"},
        {"is_up": 1},
        {"is_default_route": 0},
        {"is_up": "yes"},
        {"is_default_route": None},
    ],
)
def test_adapter_info_rejects_invalid_metadata(kwargs):
    values = {
        "name": "USB Wi-Fi",
        "index": 19,
        "local_ip": "192.168.10.2",
        "is_up": True,
        "is_default_route": False,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        AdapterInfo(**values)


@pytest.mark.parametrize(
    "netmask",
    ["not-a-mask", "2001:db8::1", "255.0.255.0", True, 24],
)
def test_adapter_info_rejects_invalid_netmask(netmask):
    with pytest.raises(ValueError, match="netmask"):
        AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False, netmask=netmask)


def test_adapter_info_keeps_loopback_representable_for_selector_safety():
    adapter = AdapterInfo(
        "Loopback",
        1,
        "127.0.0.1",
        True,
        False,
        netmask="255.0.0.0",
    )

    assert adapter.local_ip == "127.0.0.1"


@pytest.mark.parametrize(
    ("local_ip", "netmask"),
    [
        ("192.168.10.2", "255.255.255.255"),
        ("192.168.10.2", "255.255.255.254"),
    ],
)
def test_selector_rejects_unusable_tello_subnet(local_ip, netmask):
    adapter = AdapterInfo("USB Wi-Fi", 19, local_ip, True, False, netmask=netmask)

    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [adapter], default_route=DEFAULT_ROUTE
        ).select(local_ip=local_ip)


def test_selector_uses_injected_reachability_probe_without_hardware():
    calls = []

    def unreachable(adapter):
        calls.append(adapter.local_ip)
        return False

    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )

    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [adapter],
            default_route=DEFAULT_ROUTE,
            reachability_probe=unreachable,
        ).select(local_ip=adapter.local_ip)

    assert calls == [adapter.local_ip]


def test_selector_default_probe_checks_tello_endpoint_from_candidate_ip(monkeypatch):
    calls = []

    def fake_route_source_ip(destination, *, local_ip=None):
        calls.append((destination, local_ip))
        return local_ip

    monkeypatch.setattr(network, "route_source_ip", fake_route_source_ip)
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )

    selected = NetworkAdapterSelector(
        [adapter], default_route=DEFAULT_ROUTE
    ).select(local_ip=adapter.local_ip)

    assert selected == adapter
    assert calls == [("192.168.10.1", "192.168.10.2")]


def test_selector_refreshes_route_snapshot_provider_on_each_select():
    routes = iter(
        [
            DEFAULT_ROUTE,
            RouteSnapshot(source_ip="192.168.10.2", interface_index=19),
        ]
    )
    adapter = AdapterInfo(
        "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
    )
    selector = selector_for_test(
        [adapter],
        default_route=DEFAULT_ROUTE,
        route_snapshot_provider=lambda: next(routes),
    )

    assert selector.select(local_ip=adapter.local_ip) == adapter
    with pytest.raises(NoTelloAdapter):
        selector.select(local_ip=adapter.local_ip)


def test_automatic_selection_rejects_incomplete_default_route_snapshot():
    adapters = [
        AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True),
        AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False),
    ]

    with pytest.raises(RouteDiscoveryError, match="default route"):
        selector_for_test(
            adapters,
            default_route=RouteSnapshot(source_ip=None, interface_index=None),
        ).select()


@pytest.mark.parametrize(
    "after",
    [
        RouteSnapshot(source_ip="192.168.1.44", interface_index=19),
        RouteSnapshot(source_ip="192.168.1.45", interface_index=7),
    ],
)
def test_route_snapshot_is_frozen_and_route_change_is_rejected(after):
    before = RouteSnapshot(source_ip="192.168.1.44", interface_index=7)

    with pytest.raises(FrozenInstanceError):
        before.source_ip = "192.168.1.45"

    with pytest.raises(DefaultRouteChanged):
        NetworkAdapterSelector.assert_default_route_unchanged(before, after)


class FakeProbe:
    def __init__(self, *, local_ip="192.168.10.2", error=None):
        self.local_ip = local_ip
        self.error = error
        self.closed = False
        self.connected_to = None
        self.bound_to = None

    def bind(self, address):
        self.bound_to = address

    def connect(self, address):
        self.connected_to = address
        if self.error is not None:
            raise self.error

    def getsockname(self):
        if self.error is not None:
            raise self.error
        return (self.local_ip, 45678)

    def close(self):
        self.closed = True


def test_route_source_ip_closes_udp_probe_after_success(monkeypatch):
    probe = FakeProbe()
    monkeypatch.setattr(network.socket, "socket", lambda *args: probe)

    assert route_source_ip("192.168.10.1") == "192.168.10.2"
    assert probe.connected_to == ("192.168.10.1", 8889)
    assert probe.closed is True


def test_route_source_ip_can_bind_to_candidate_local_ip(monkeypatch):
    probe = FakeProbe()
    monkeypatch.setattr(network.socket, "socket", lambda *args: probe)

    assert (
        route_source_ip("192.168.10.1", local_ip="192.168.10.2")
        == "192.168.10.2"
    )
    assert probe.bound_to == ("192.168.10.2", 0)
    assert probe.closed is True


def test_route_source_ip_returns_none_and_closes_after_os_error(monkeypatch):
    probe = FakeProbe(error=OSError("unreachable"))
    monkeypatch.setattr(network.socket, "socket", lambda *args: probe)

    assert route_source_ip("1.1.1.1") is None
    assert probe.closed is True


def test_capture_default_route_probes_public_dns(monkeypatch):
    destinations = []

    def fake_route_source_ip(destination):
        destinations.append(destination)
        return "192.168.1.44"

    monkeypatch.setattr(network, "route_source_ip", fake_route_source_ip)

    adapters = [AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True)]

    assert capture_default_route(adapters=adapters) == RouteSnapshot(
        "192.168.1.44", 7
    )
    assert destinations == ["1.1.1.1"]


def test_capture_default_route_fails_closed_when_probe_has_no_source(monkeypatch):
    monkeypatch.setattr(network, "route_source_ip", lambda _: None)

    with pytest.raises(RouteDiscoveryError, match="source IP"):
        capture_default_route(
            adapters=[AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True)]
        )


def test_resolve_interface_indexes_prefers_if_nameindex(monkeypatch):
    fallback_calls = []

    monkeypatch.setattr(
        network.socket,
        "if_nameindex",
        lambda: [(7, "Wi-Fi"), (19, "USB Wi-Fi")],
    )
    monkeypatch.setattr(
        network.socket,
        "if_nametoindex",
        lambda name: fallback_calls.append(name) or 23,
    )

    assert resolve_interface_indexes(["USB Wi-Fi", "Wi-Fi", "Missing"]) == {
        "USB Wi-Fi": 19,
        "Wi-Fi": 7,
        "Missing": 23,
    }
    assert fallback_calls == ["Missing"]


def test_resolve_interface_indexes_falls_back_for_windows_name_form_mismatch(
    monkeypatch,
):
    def fallback(name):
        if name == "USB_Wi-Fi":
            return 19
        raise OSError(name)

    monkeypatch.setattr(
        network.socket,
        "if_nameindex",
        lambda: [(19, "USB Wi-Fi")],
    )
    monkeypatch.setattr(
        network.socket,
        "if_nametoindex",
        fallback,
    )

    assert resolve_interface_indexes(["USB_Wi-Fi"]) == {"USB_Wi-Fi": 19}


def test_resolve_interface_indexes_uses_safe_fallback_when_mapping_unavailable(
    monkeypatch,
):
    def no_mapping():
        raise OSError("if_nameindex unavailable")

    monkeypatch.setattr(network.socket, "if_nameindex", no_mapping)
    monkeypatch.setattr(
        network.socket,
        "if_nametoindex",
        lambda name: {"USB Wi-Fi": 19}[name],
    )

    assert resolve_interface_indexes(["USB Wi-Fi", "Missing"]) == {"USB Wi-Fi": 19}


def test_discover_adapters_uses_read_only_psutil_data_and_marks_default_route(
    monkeypatch,
):
    fake_psutil = SimpleNamespace(
        net_if_stats=lambda: {
            "Wi-Fi": SimpleNamespace(isup=True),
            "USB Wi-Fi": SimpleNamespace(isup=True),
            "Loopback": SimpleNamespace(isup=True),
            "Offline": SimpleNamespace(isup=False),
        },
        net_if_addrs=lambda: {
            "Wi-Fi": [
                SimpleNamespace(
                    family=network.socket.AF_INET,
                    address="192.168.1.44",
                    netmask="255.255.255.0",
                )
            ],
            "USB Wi-Fi": [
                SimpleNamespace(
                    family=network.socket.AF_INET,
                    address="192.168.10.2",
                    netmask="255.255.255.0",
                ),
                SimpleNamespace(family=network.socket.AF_INET6, address="::1"),
            ],
            "Loopback": [
                SimpleNamespace(family=network.socket.AF_INET, address="127.0.0.1")
            ],
            "Offline": [
                SimpleNamespace(family=network.socket.AF_INET, address="192.168.10.3")
            ],
        },
    )
    monkeypatch.setattr(network, "psutil", fake_psutil)
    monkeypatch.setattr(
        network,
        "capture_default_route",
        lambda adapters=None: RouteSnapshot("192.168.1.44", 7),
    )
    monkeypatch.setattr(
        network,
        "resolve_interface_indexes",
        lambda names: {"Wi-Fi": 7, "USB Wi-Fi": 19, "Loopback": 1},
    )

    discovered = discover_adapters()

    assert discovered == (
        AdapterInfo(
            "Wi-Fi", 7, "192.168.1.44", True, True, netmask="255.255.255.0"
        ),
        AdapterInfo(
            "USB Wi-Fi", 19, "192.168.10.2", True, False, netmask="255.255.255.0"
        ),
    )


def test_discover_adapters_fails_closed_when_default_route_source_is_unknown(
    monkeypatch,
):
    fake_psutil = SimpleNamespace(
        net_if_stats=lambda: {"USB Wi-Fi": SimpleNamespace(isup=True)},
        net_if_addrs=lambda: {
            "USB Wi-Fi": [
                SimpleNamespace(family=network.socket.AF_INET, address="192.168.10.2")
            ]
        },
    )
    monkeypatch.setattr(network, "psutil", fake_psutil)
    monkeypatch.setattr(
        network,
        "resolve_interface_indexes",
        lambda names: {"USB Wi-Fi": 19},
    )
    monkeypatch.setattr(
        network,
        "capture_default_route",
        lambda adapters=None: RouteSnapshot(None, None),
    )

    with pytest.raises(RouteDiscoveryError, match="source IP"):
        discover_adapters()


def test_capture_default_route_rejects_source_without_matching_adapter(monkeypatch):
    monkeypatch.setattr(network, "route_source_ip", lambda _: "192.168.1.44")

    with pytest.raises(RouteDiscoveryError, match="interface"):
        capture_default_route(
            adapters=[AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False)]
        )


def test_capture_default_route_discovers_interface_index_when_adapters_are_omitted(
    monkeypatch,
):
    monkeypatch.setattr(network, "route_source_ip", lambda _: "192.168.1.44")
    monkeypatch.setattr(
        network,
        "psutil",
        SimpleNamespace(
            net_if_addrs=lambda: {
                "Wi-Fi": [
                    SimpleNamespace(
                        family=network.socket.AF_INET, address="192.168.1.44"
                    )
                ]
            }
        ),
    )
    monkeypatch.setattr(network, "resolve_interface_indexes", lambda names: {"Wi-Fi": 7})

    assert capture_default_route() == RouteSnapshot("192.168.1.44", 7)


def test_automatic_selection_captures_route_when_snapshot_is_not_supplied(monkeypatch):
    adapters = [AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False)]
    captured = []

    def fake_capture(*, adapters=None):
        captured.append(tuple(adapters))
        return RouteSnapshot("192.168.1.44", 7)

    monkeypatch.setattr(network, "capture_default_route", fake_capture)

    assert selector_for_test(adapters).select() == adapters[0]
    assert captured == [tuple(adapters)]


def test_automatic_selection_rejects_candidate_owned_by_default_route():
    adapter = AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False)

    with pytest.raises(NoTelloAdapter):
        selector_for_test(
            [adapter],
            default_route=RouteSnapshot("192.168.10.2", 19),
        ).select()
