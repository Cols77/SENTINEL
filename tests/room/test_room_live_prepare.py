"""Task M: ``scripts/room_live_prepare.py`` -- one offline command prepares the Live pack.

The prepared pack is driven through Mission Control's room-agent Live path
(``sentinel.ui.room_agent`` -> the real ``scripts.room_session.main``) up to
the point where the drone would be contacted: every gate (manifest digest,
TRIAL grant admission, consent, site assurances, profiles, tag map) must pass
and the first thing that would touch hardware -- the connectivity preflight's
drone probe -- is a recorder. Nothing reaches hardware, the network or a
provider.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts import room_live_prepare as prepare
from sentinel.room import artifacts, capabilities, live_stack
from sentinel.ui.app import build_app
from sentinel.ui.bootstrap import SessionConfig, build_service
from sentinel.ui.room_agent import attach_room_agent

REQUEST = "count the phones and computers in the room"
ROOT = Path(__file__).resolve().parents[2]


def _prepare(run_root, *extra, capsys=None):
    code = prepare.main(["--run-root", str(run_root), *extra])
    out = err = ""
    if capsys is not None:
        captured = capsys.readouterr()
        out, err = captured.out, captured.err
    return code, out, err


def _pack(run_root):
    return json.loads((Path(run_root) / prepare.CURRENT_PACK_NAME).read_text(encoding="utf-8"))


class _Recorder:
    def __init__(self):
        self.calls = []


@pytest.fixture(autouse=True)
def _free_command_port(monkeypatch):
    # The real UDP 8889 may be held by an operator's Live server on this machine.
    from sentinel.ui import room_agent
    monkeypatch.setattr(room_agent.RoomAgentService, "_command_port_status",
                        lambda self, local_ip: dict(status="FREE"))
    # The Prepare panel's battery probe would otherwise add its own TelloLink to the recorder.
    monkeypatch.setattr(room_agent.RoomAgentService, "_live_battery_percent",
                        lambda self, local_ip: None)


@pytest.fixture
def hardware(monkeypatch):
    """Every real constructor/probe the live path could reach, replaced by recorders."""
    rec = _Recorder()

    class FakeLink:
        def __init__(self, *, local_ip=None, clock=None, **_):
            rec.calls.append(("TelloLink", local_ip))

        def probe(self):
            rec.calls.append(("drone_probe",))
            raise RuntimeError("test: the drone would be contacted here")

        def close(self):
            rec.calls.append(("close",))

    def provider_probe(*_a, **_k):
        rec.calls.append(("provider_probe",))
        raise RuntimeError("test: the provider would be contacted here")

    monkeypatch.setattr(live_stack, "TelloLink", FakeLink)
    monkeypatch.setattr(live_stack, "openrouter_provider_probe", provider_probe)
    monkeypatch.setattr(live_stack, "default_jev", lambda *a, **k: rec.calls.append(("default_jev",)))
    monkeypatch.setattr(live_stack, "default_detector", lambda *a, **k: rec.calls.append(("default_detector",)))
    return rec


def test_the_prepared_pack_passes_every_live_gate_up_to_the_drone_probe(tmp_path, capsys, hardware):
    run_root = tmp_path / "runs"
    code, out, err = _prepare(run_root, "--local-ip", "192.168.10.2", "--consent-confirmed", capsys=capsys)
    assert code == 0, err
    pack_path = run_root / prepare.CURRENT_PACK_NAME
    pack = _pack(run_root)
    run_id = pack["run_id"]
    assert (run_root / f"{run_id}.pack.json").exists()
    assert json.loads((run_root / f"{run_id}.pack.json").read_text(encoding="utf-8")) == pack
    assert "everyone" in out and "consent" in out.lower()
    assert f"--room-live-config {pack_path.as_posix()}" in out.replace("\\", "/")
    # The launch line names the venue the pack was prepared for, not a fixed
    # room: Mission Control validates that venue's cube site and tag map at
    # startup, so a lecture-room pack must not tell the operator to start the
    # server on the CanningSt profile.
    assert (f"python -m sentinel.ui --mode live --site-profile {pack['location']} "
            "--data-dir artifacts/room-live") in out
    assert "--i-authorise-paid-calls" in out and '$env:PYTHONPATH="src;."' in out
    assert "expire" in out.lower()
    assert pack["local_ip"] == "192.168.10.2" and len(pack["assurances"]) == 2
    assert hardware.calls == []  # preparing contacts nothing

    service = build_service(SessionConfig(data_dir=tmp_path / "data", room_live_config=pack_path,
                                          authorise_paid_calls=True))
    room = attach_room_agent(service, pace=False)
    with TestClient(build_app(service, repository_root=ROOT)) as client:
        catalogue = client.get("/api/v1/room-agent").json()["catalogue"]
        assert {m["id"]: m["available"] for m in catalogue["modes"]}["LIVE"] is True
        proposed = client.post("/api/v1/room-agent/proposals",
                               json=dict(mode="LIVE", scenario="a", request_text=REQUEST)).json()
        assert proposed["status"] == "ACKNOWLEDGED", proposed
        client.post("/api/v1/room-agent/decisions",
                    json=dict(proposal_id=proposed["run"]["proposal"]["id"], decision="APPROVE"))
        assert room.wait(30)
        run = client.get("/api/v1/room-agent").json()["run"]
    # Every gate passed: the only refusal is the (recorded) drone contact itself.
    assert run["status"] == "REFUSED", run
    assert "connectivity preflight not ready" in run["error"], run["error"]
    assert ("TelloLink", "192.168.10.2") in hardware.calls
    assert ("drone_probe",) in hardware.calls
    assert ("default_jev",) not in hardware.calls and ("default_detector",) not in hardware.calls
    # The run directory the live path opened is the pack's run, recorded with the typed request.
    from sentinel.room.store import RoomStore
    store = RoomStore.open(run_root / run_id / "session.sqlite3")
    try:
        assert [e.event_type for e in store.events(run_id)] == ["CONNECTIVITY_CHECKED"]
    finally:
        store.close()

    # A run opened only by a (refused or motors-off) connectivity check stays reusable: re-preparing
    # keeps the manifest it was opened with, and the next approval again reaches the drone probe.
    code, out, err = _prepare(run_root, capsys=capsys)
    assert code == 0, err
    assert _pack(run_root)["run_id"] == run_id and "reusing the manifest" in out
    hardware.calls.clear()
    service = build_service(SessionConfig(data_dir=tmp_path / "data2", room_live_config=pack_path,
                                          authorise_paid_calls=True))
    room = attach_room_agent(service, pace=False)
    with TestClient(build_app(service, repository_root=ROOT)) as client:
        proposed = client.post("/api/v1/room-agent/proposals",
                               json=dict(mode="LIVE", scenario="a", request_text="find the phone")).json()
        client.post("/api/v1/room-agent/decisions",
                    json=dict(proposal_id=proposed["run"]["proposal"]["id"], decision="APPROVE"))
        assert room.wait(30)
        run = client.get("/api/v1/room-agent").json()["run"]
    assert "connectivity preflight not ready" in run["error"], run["error"]
    assert ("drone_probe",) in hardware.calls


def test_the_wide_scenario_pack_binds_its_site_and_drift_and_passes_every_gate(tmp_path, capsys, hardware):
    run_root = tmp_path / "runs"
    code, out, err = _prepare(run_root, "--scenario", "wide", "--consent-confirmed", "--new-run",
                              "--local-ip", "192.168.10.2", capsys=capsys)
    assert code == 0, err
    pack = _pack(run_root)
    assert pack["scenario"] == "wide" and "(scenario wide)" in out
    assert Path(pack["site"]).name == "canningst-wide-site.json"
    assert Path(pack["drift_profile"]).name == "canningst-wide-drift.json"

    service = build_service(SessionConfig(data_dir=tmp_path / "data", room_live_config=run_root / prepare.CURRENT_PACK_NAME,
                                          authorise_paid_calls=True))
    room = attach_room_agent(service, pace=False)
    with TestClient(build_app(service, repository_root=ROOT)) as client:
        proposed = client.post("/api/v1/room-agent/proposals",
                               json=dict(mode="LIVE", scenario="a", request_text=REQUEST)).json()
        assert "Site: CanningSt, wide scenario (canningst-wide-site.json)" in proposed["run"]["proposal"]["lines"]
        client.post("/api/v1/room-agent/decisions",
                    json=dict(proposal_id=proposed["run"]["proposal"]["id"], decision="APPROVE"))
        assert room.wait(30)
        run = client.get("/api/v1/room-agent").json()["run"]
    assert "connectivity preflight not ready" in run["error"], run["error"]
    assert ("drone_probe",) in hardware.calls

    # The default (Mission Control's no-argument prepare) is the lecture-room
    # venue the demo is in (2026-09-26), so an untouched drop-down cannot
    # prepare another room's geometry against this room's tags. Being another
    # venue, it needs its own filming consent: the CanningSt record this test
    # already made does not carry it.
    assert _prepare(run_root, "--new-run", capsys=capsys)[0] == 2
    assert _prepare(run_root, "--new-run", "--consent-confirmed", capsys=capsys)[0] == 0
    default = _pack(run_root)
    assert default["scenario"] == "lecture" and default["location"] == "LectureRoom"
    assert Path(default["site"]).name == "lecture-room-site.json"
    assert Path(default["tag_map"]).name == "lecture-room-map.json"
    assert Path(default["drift_profile"]).name == "canningst-demo-drift.json"


def test_rerunning_reuses_the_registry_the_run_and_its_grant(tmp_path, capsys):
    run_root = tmp_path / "runs"
    assert _prepare(run_root, "--consent-confirmed", "--local-ip", "192.168.10.3", capsys=capsys)[0] == 0
    first = _pack(run_root)
    registry_bytes = (run_root / "registry.json").read_bytes()
    code, out, err = _prepare(run_root, capsys=capsys)  # consent already recorded today
    assert code == 0, err
    second = _pack(run_root)
    assert second["run_id"] == first["run_id"]
    assert second["grant_sha256"] == first["grant_sha256"]
    assert second["local_ip"] == "192.168.10.3"  # kept from the previous preparation
    assert second["assurances"] == first["assurances"]  # rewritten in place, fresh window
    assert (run_root / "registry.json").read_bytes() == registry_bytes  # no second grant
    assert "reusing" in out.lower()
    registry = capabilities.load_registry(run_root / "registry.json")
    assert len(registry.grants) == 1
    manifest = artifacts.parse_manifest(Path(second["manifest"]).read_bytes())
    assert capabilities.admit_live(registry, manifest=manifest,
                                   now=prepare.utc_now()).grant_sha256 == second["grant_sha256"]


def test_a_flown_run_is_never_reused(tmp_path, capsys):
    run_root = tmp_path / "runs"
    assert _prepare(run_root, "--consent-confirmed", capsys=capsys)[0] == 0
    first = _pack(run_root)
    flown = run_root / first["run_id"]
    flown.mkdir(parents=True, exist_ok=True)
    (flown / "terminal.json").write_text('{"state": "COMPLETED"}', encoding="utf-8")
    code, _, err = _prepare(run_root, "--run-id", "l3-second", capsys=capsys)
    assert code == 0, err
    second = _pack(run_root)
    assert second["run_id"] == "l3-second" != first["run_id"]
    registry = capabilities.load_registry(run_root / "registry.json")
    assert len(registry.grants) == 2
    manifest = artifacts.parse_manifest(Path(second["manifest"]).read_bytes())
    assert capabilities.admit_live(registry, manifest=manifest,
                                   now=prepare.utc_now()).grant_sha256 == second["grant_sha256"]
    # Without --run-id, a flown current run is replaced by a fresh one too.
    (run_root / "l3-second").mkdir()
    (run_root / "l3-second" / "terminal.json").write_text("{}", encoding="utf-8")
    assert _prepare(run_root, capsys=capsys)[0] == 0
    assert _pack(run_root)["run_id"] not in (first["run_id"], "l3-second")


def test_without_a_consent_record_it_refuses_and_writes_nothing(tmp_path, capsys):
    run_root = tmp_path / "runs"
    code, _, err = _prepare(run_root, capsys=capsys)
    assert code == 2
    assert "consent" in err and "--consent-confirmed" in err
    assert not (run_root / "registry.json").exists()
    assert not (run_root / prepare.CURRENT_PACK_NAME).exists()


def test_mission_control_refuses_a_pack_whose_run_already_flew(tmp_path, capsys, hardware):
    run_root = tmp_path / "runs"
    assert _prepare(run_root, "--consent-confirmed", capsys=capsys)[0] == 0
    pack = _pack(run_root)
    (run_root / pack["run_id"]).mkdir(parents=True, exist_ok=True)
    (run_root / pack["run_id"] / "terminal.json").write_text("{}", encoding="utf-8")
    service = build_service(SessionConfig(data_dir=tmp_path / "data",
                                          room_live_config=run_root / prepare.CURRENT_PACK_NAME,
                                          authorise_paid_calls=True))
    room = attach_room_agent(service, pace=False)
    with TestClient(build_app(service, repository_root=ROOT)) as client:
        refused = client.post("/api/v1/room-agent/proposals",
                              json=dict(mode="LIVE", scenario="a", request_text="find the phone"))
        assert refused.status_code == 503
        assert refused.json()["reason_code"] == "ROOM_PACK_ALREADY_USED"
        assert client.get("/api/v1/room-agent").json()["run"] is None
    assert room.run is None
    assert hardware.calls == []
