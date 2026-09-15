from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import sentinel.network as network
from sentinel.models import AdapterInfo
from sentinel.network import (
    AmbiguousAdapter,
    DefaultRouteChanged,
    NoTelloAdapter,
    NetworkAdapterSelector,
    RouteSnapshot,
    capture_default_route,
    discover_adapters,
    route_source_ip,
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


@pytest.mark.parametrize(
    ("kwargs", "expected_name"),
    [
        ({"local_ip": "192.168.10.3"}, "USB Wi-Fi B"),
        ({"interface_name": "USB Wi-Fi A"}, "USB Wi-Fi A"),
        ({"interface_index": 20}, "USB Wi-Fi B"),
    ],
)
def test_explicit_override_selects_exact_adapter(kwargs, expected_name):
    selected = NetworkAdapterSelector(
        [
            AdapterInfo("USB Wi-Fi A", 19, "192.168.10.2", True, False),
            AdapterInfo("USB Wi-Fi B", 20, "192.168.10.3", True, False),
        ]
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
        NetworkAdapterSelector([adapter]).select(interface_index=19)


def test_route_snapshot_is_frozen_and_route_change_is_rejected():
    before = RouteSnapshot(source_ip="192.168.1.44", interface_index=7)
    after = RouteSnapshot(source_ip="192.168.10.2", interface_index=19)

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

    assert capture_default_route() == RouteSnapshot("192.168.1.44", None)
    assert destinations == ["1.1.1.1"]


def test_discover_adapters_uses_read_only_psutil_data_and_marks_default_route(
    monkeypatch,
):
    fake_psutil = SimpleNamespace(
        net_if_stats=lambda: {
            "Wi-Fi": SimpleNamespace(isup=True),
            "USB Wi-Fi": SimpleNamespace(isup=True),
            "Offline": SimpleNamespace(isup=False),
        },
        net_if_addrs=lambda: {
            "Wi-Fi": [SimpleNamespace(family=network.socket.AF_INET, address="192.168.1.44")],
            "USB Wi-Fi": [
                SimpleNamespace(family=network.socket.AF_INET, address="192.168.10.2"),
                SimpleNamespace(family=network.socket.AF_INET6, address="::1"),
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
        lambda: RouteSnapshot("192.168.1.44", None),
    )
    monkeypatch.setattr(
        network.socket,
        "if_nametoindex",
        lambda name: {"Wi-Fi": 7, "USB Wi-Fi": 19}[name],
    )

    discovered = discover_adapters()

    assert discovered == (
        AdapterInfo("Wi-Fi", 7, "192.168.1.44", True, True),
        AdapterInfo("USB Wi-Fi", 19, "192.168.10.2", True, False),
    )
