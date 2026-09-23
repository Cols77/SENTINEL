"""Task C: scripts/room_takeoff_check.py, with fakes only (never run for real)."""
from __future__ import annotations

import io
import json

from scripts import room_takeoff_check
from sentinel.djitellopy_backend import DjitellopyClient
from sentinel.ui.room_rehearsal import FakeEmergencySocket, FakeTelloWorld, FakeVideo


def fake_client(**world):
    tello = FakeTelloWorld(**world)
    client = DjitellopyClient(local_ip="192.168.10.2", tello_factory=lambda **_: tello,
                              video_factory=FakeVideo(tello), socket_factory=lambda *a, **k: FakeEmergencySocket())
    return tello, client


def sdk_sequence(sent):
    """Commands sent to the aircraft, without read-only queries (``height?``...)."""
    return [command for command in sent if not command.endswith("?")]


def test_without_execute_it_refuses_and_builds_nothing(capsys):
    built = []
    code = room_takeoff_check.main([], build_client=lambda: built.append("client"))
    assert code == 2 and built == []
    assert "--execute" in capsys.readouterr().err


def test_it_connects_takes_off_hovers_about_three_seconds_and_lands():
    tello, client = fake_client()
    slept, armed, out = [], [], io.StringIO()
    code = room_takeoff_check.main(["--execute"], build_client=lambda: client, sleep=slept.append,
                                   arm_emergency=lambda stop: armed.append(stop), out=out)
    assert code == 0
    assert tello.sent == ["command", "takeoff", "land"]  # the exact SDK sequence
    assert slept == [3.0] and len(armed) == 1
    assert json.loads(out.getvalue()) == {"connected": True, "battery_percent": 90, "took_off": True,
                                          "landed": True}
    assert not tello.flying


def test_a_low_battery_refuses_before_takeoff():
    tello, client = fake_client(battery=20)
    code = room_takeoff_check.main(["--execute"], build_client=lambda: client, sleep=lambda s: None,
                                   arm_emergency=lambda stop: None)
    assert code == 2 and sdk_sequence(tello.sent) == ["command"]


def test_a_failure_during_the_hover_still_lands():
    tello, client = fake_client()

    def boom(_seconds):
        raise RuntimeError("hover interrupted")

    code = room_takeoff_check.main(["--execute"], build_client=lambda: client, sleep=boom,
                                   arm_emergency=lambda stop: None)
    assert code == 2 and sdk_sequence(tello.sent) == ["command", "takeoff", "land"]


def test_the_hover_is_bounded():
    tello, client = fake_client()
    code = room_takeoff_check.main(["--execute", "--hover-s", "30"], build_client=lambda: client,
                                   sleep=lambda s: None, arm_emergency=lambda stop: None)
    assert code == 2 and tello.sent == []
