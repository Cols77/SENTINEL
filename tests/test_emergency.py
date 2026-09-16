import sys
from types import SimpleNamespace

import sentinel.emergency as emergency_module
from sentinel.emergency import EmergencyHotkey


def _run_windows_hotkey(monkeypatch, callback):
    monkeypatch.setattr(emergency_module.os, "name", "nt")
    fake_msvcrt = SimpleNamespace(kbhit=lambda: True, getwch=lambda: "e")
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    hotkey = EmergencyHotkey(callback)
    hotkey.start()
    hotkey.thread.join(timeout=0.5)
    assert not hotkey.thread.is_alive()
    return hotkey


def test_windows_hotkey_triggers_once_and_latches(monkeypatch):
    calls = []

    hotkey = _run_windows_hotkey(monkeypatch, lambda: calls.append("stop"))

    hotkey.stop()
    assert calls == ["stop"]
    assert hotkey.triggered.is_set()
    assert hotkey.error is None


def test_windows_hotkey_retains_callback_failure(monkeypatch):
    failure = RuntimeError("send failed")

    hotkey = _run_windows_hotkey(monkeypatch, lambda: (_ for _ in ()).throw(failure))

    assert hotkey.triggered.is_set()
    assert hotkey.error is failure


def test_non_windows_hotkey_is_a_noop(monkeypatch):
    monkeypatch.setattr(emergency_module.os, "name", "posix")
    calls = []
    hotkey = EmergencyHotkey(lambda: calls.append("stop"))

    hotkey.start()
    hotkey.thread.join(timeout=0.5)
    hotkey.stop()

    assert calls == []
    assert not hotkey.triggered.is_set()
    assert hotkey.error is None
