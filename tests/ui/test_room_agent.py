"""Task C: Mission Control's room-agent architecture, through the service API.

Rehearsal runs the production room composition over the fake CanningSt
aircraft (``sentinel.ui.room_rehearsal``); nothing reaches hardware, the
network or a provider. Live is exercised only up to (and just past) the
approval boundary, with ``room_session.main`` and every real constructor
replaced by recorders.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from sentinel.room import live_stack
from sentinel.ui import room_agent, room_rehearsal
from sentinel.ui.app import build_app
from sentinel.ui.bootstrap import SessionConfig, build_service
from sentinel.ui.room_agent import attach_room_agent

REQUEST = "count the phones and computers in the room"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fast(monkeypatch):
    # No real waiting: SETTLE/capture windows collapse on the fake stack.
    monkeypatch.setattr(live_stack._TelloStack, "_within", staticmethod(lambda upper_bound_s: 0.0))


def _client(tmp_path, **config):
    service = build_service(SessionConfig(data_dir=tmp_path / "data", **config))
    room = attach_room_agent(service, pace=False)
    return service, room, TestClient(build_app(service, repository_root=ROOT))


def _run(client, room, *, scenario="a", mode="REHEARSAL", timeout=120):
    proposed = client.post("/api/v1/room-agent/proposals",
                           json=dict(mode=mode, scenario=scenario, request_text=REQUEST))
    assert proposed.status_code == 200, proposed.json()
    run = proposed.json()["run"]
    assert run["status"] == "AWAITING_APPROVAL" and run["proposal"]["request_text"] == REQUEST
    decided = client.post("/api/v1/room-agent/decisions",
                          json=dict(proposal_id=run["proposal"]["id"], decision="APPROVE"))
    assert decided.status_code == 200, decided.json()
    assert room.wait(timeout)
    return client.get("/api/v1/room-agent").json()["run"]


def test_the_catalogue_offers_the_room_agent_next_to_the_survey(tmp_path):
    service, room, client = _client(tmp_path)
    with client:
        body = client.get("/api/v1/room-agent").json()
    ids = [row["id"] for row in body["catalogue"]["architectures"]]
    assert ids == ["survey", "room_agent"]
    assert body["catalogue"]["architectures"][1]["label"] == "Room agent (Jev adaptive)"
    modes = {row["id"]: row["available"] for row in body["catalogue"]["modes"]}
    assert modes == {"REHEARSAL": True, "JEV_REHEARSAL": True, "LIVE": False}
    assert [row["id"] for row in body["catalogue"]["scenarios"]] == ["a", "b"]
    assert body["run"] is None


def test_a_rehearsal_run_reaches_an_answer_and_returns_home(tmp_path, fast):
    service, room, client = _client(tmp_path)
    with client:
        run = _run(client, room)
    assert run["status"] == "COMPLETED", run["error"]
    assert run["answer"]["code"] == "ANSWERED_COUNT"
    assert run["answer"]["summary"] == "phones: 1 confirmed, 1 uncertain; computers: 2 confirmed", run["answer"]
    assert run["returned_home"] is True
    assert run["landing"] == {"state": "LANDED", "reason": "REQUESTED"}
    assert [r["status"] for r in run["regions"]] == ["OBSERVED"] * 3
    assert run["captures"] >= 3 and len(run["detections"]) >= 4
    assert any(d["confidence"] == "low" for d in run["detections"])
    assert run["decisions"] and all(d["reason"] for d in run["decisions"])
    assert run["decisions"][-1]["action"] == "ANSWER:COUNTS"
    assert run["yaw_rotations"] == 0
    # The runner is released once the run is terminal.
    assert service.registry.get(service.config.run_id) is None


def test_scenario_b_recovers_by_a_yaw_rotation_and_an_accepted_reanchor(tmp_path, fast):
    service, room, client = _client(tmp_path)
    with client:
        run = _run(client, room, scenario="b")
    assert run["status"] == "COMPLETED", run["error"]
    kinds = [row["kind"] for row in run["reanchor_log"]]
    assert "RECOVERY" in kinds
    rotation = kinds.index("YAW_ROTATION")
    accepted = kinds.index("REANCHOR_ACCEPTED")
    assert rotation < accepted
    rotation_row = run["reanchor_log"][rotation]
    assert rotation_row["direction"] == "ccw" and rotation_row["degrees"] == 15
    assert abs(rotation_row["heading_deg"] - 90) < 1
    states = [row["state"] for row in run["reanchor_log"] if row["kind"] == "LOCALIZATION"]
    assert "COASTING" in states and "RECOVERY_REQUIRED" in states
    bursts = [row for row in run["reanchor_log"] if row["kind"] == "TAG_BURST"]
    assert any(row["tags"] == [0, 2] and abs(row["heading_deg"] - 90) < 1 for row in bursts)
    assert run["yaw_rotations"] >= 1 and run["reanchors_accepted"] >= 1
    assert run["answer"]["summary"].startswith("phones:")
    assert run["returned_home"] is True and run["landing"]["state"] == "LANDED"
    assert room.run.world.world.sent.count("ccw 15") == 1


def _wait_airborne(room, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = room.run
        if run is not None and run.safety.stack is not None and run.public()["took_off"]:
            return run
        time.sleep(0.05)
    raise AssertionError("never airborne")


def _slow_jev(monkeypatch):
    original = room_rehearsal.ScriptedJev.choice

    def choice(self, state, instruction, *, criteria, name):
        if name == "room_action":
            time.sleep(0.3)
        return original(self, state, instruction, criteria=criteria, name=name)

    monkeypatch.setattr(room_rehearsal.ScriptedJev, "choice", choice)


def test_a_controlled_land_during_a_room_run_goes_through_the_executor_and_is_landed(
        tmp_path, fast, monkeypatch):
    _slow_jev(monkeypatch)
    service, room, client = _client(tmp_path)
    with client:
        proposed = client.post("/api/v1/room-agent/proposals",
                               json=dict(mode="REHEARSAL", scenario="a", request_text=REQUEST)).json()
        client.post("/api/v1/room-agent/decisions",
                    json=dict(proposal_id=proposed["run"]["proposal"]["id"], decision="APPROVE"))
        run = _wait_airborne(room)
        snapshot = client.get(f"/api/v1/runs/{service.config.run_id}/snapshot").json()
        land_action = next(a for a in snapshot["legal_actions"] if a["action"] == "LAND")
        assert land_action["enabled"] is True
        identity = dict(command_id="land-1", correlation_id="land-1", idempotency_key="land-1")
        landed = client.post(f"/api/v1/runs/{service.config.run_id}/land", json=identity)
        assert landed.json()["status"] == "ACKNOWLEDGED", landed.json()
        assert room.wait(60)
        public = client.get("/api/v1/room-agent").json()["run"]
    assert public["landing"]["state"] == "LANDED"
    assert public["land_status"] == "ACKNOWLEDGED"
    assert run.safety.land_outcome.state == "LANDED"
    sent = run.world.world.sent
    assert sent.count("land") == 1 and not run.world.world.flying
    assert not service.registry.emergency.is_set()


def test_emergency_during_a_room_run_sends_the_motor_stop_and_latches_the_session(
        tmp_path, fast, monkeypatch):
    _slow_jev(monkeypatch)
    service, room, client = _client(tmp_path)
    with client:
        proposed = client.post("/api/v1/room-agent/proposals",
                               json=dict(mode="REHEARSAL", scenario="a", request_text=REQUEST)).json()
        client.post("/api/v1/room-agent/decisions",
                    json=dict(proposal_id=proposed["run"]["proposal"]["id"], decision="APPROVE"))
        run = _wait_airborne(room)
        identity = dict(command_id="e-1", correlation_id="e-1", idempotency_key="e-1")
        result = client.post("/api/v1/safety/emergency",
                             json=dict(session_id=service.session_id, identity=identity,
                                       active_run_id=service.config.run_id))
        assert result.json()["status"] == "ACKNOWLEDGED", result.json()
        assert room.wait(60)
        public = client.get("/api/v1/room-agent").json()["run"]
        again = client.post("/api/v1/room-agent/proposals",
                            json=dict(mode="REHEARSAL", scenario="a", request_text=REQUEST))
    assert run.safety.emergency_sent and public["emergency_sent"]
    assert run.world.client.emergency_triggered
    assert service.registry.emergency.is_set()
    assert again.json()["reason_code"] == "SESSION_EMERGENCY_LATCHED"


def test_rejecting_a_proposal_constructs_nothing(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(room_rehearsal, "build_world", lambda *a, **k: built.append("world"))
    service, room, client = _client(tmp_path)
    with client:
        run = client.post("/api/v1/room-agent/proposals",
                          json=dict(mode="REHEARSAL", scenario="a", request_text=REQUEST)).json()["run"]
        rejected = client.post("/api/v1/room-agent/decisions",
                               json=dict(proposal_id=run["proposal"]["id"], decision="REJECT")).json()
    assert rejected["run"]["status"] == "REJECTED" and built == []


def _live_pack(tmp_path):
    manifest = tmp_path / "run.manifest.json"
    manifest.write_text(json.dumps({"run_id": "l3-test"}), encoding="utf-8")
    pack = dict(manifest=str(manifest), authorization_digest="d" * 64, grant_registry=str(tmp_path / "reg.json"),
                consent_db=str(tmp_path / "consent.sqlite3"), location="CanningSt", date="2026-09-24",
                assurances=[str(tmp_path / "nav.json"), str(tmp_path / "energy.json")],
                clock_domain_id="operator-l3-test", run_root=str(tmp_path / "runs"), local_ip="192.168.10.2")
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(pack), encoding="utf-8")
    return path


def test_in_live_nothing_is_constructed_before_approval(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(live_stack, "TelloLink", lambda *a, **k: calls.append("TelloLink"))
    monkeypatch.setattr(live_stack, "default_jev", lambda *a, **k: calls.append("default_jev"))
    monkeypatch.setattr(live_stack, "default_detector", lambda *a, **k: calls.append("default_detector"))
    monkeypatch.setattr(live_stack, "openrouter_provider_probe", lambda *a, **k: calls.append("provider_probe"))
    from scripts import room_session
    released = threading.Event()

    def fake_main(argv, **kwargs):
        calls.append(("room_session.main", tuple(argv), kwargs["request_text"]))
        released.set()
        print("refused: connectivity preflight not ready (test)", file=__import__("sys").stderr)
        return 2

    monkeypatch.setattr(room_session, "main", fake_main)
    service, room, client = _client(tmp_path, room_live_config=_live_pack(tmp_path), authorise_paid_calls=True)
    with client:
        catalogue = client.get("/api/v1/room-agent").json()["catalogue"]
        assert {m["id"]: m["available"] for m in catalogue["modes"]}["LIVE"] is True
        assert catalogue["local_ip"] == "192.168.10.2"
        proposed = client.post("/api/v1/room-agent/proposals",
                               json=dict(mode="LIVE", scenario="a", request_text=REQUEST)).json()
        assert proposed["status"] == "ACKNOWLEDGED"
        assert proposed["run"]["proposal"]["flies"] is True
        time.sleep(0.3)
        assert calls == []  # nothing real before APPROVE
        client.post("/api/v1/room-agent/decisions",
                    json=dict(proposal_id=proposed["run"]["proposal"]["id"], decision="APPROVE"))
        assert released.wait(10) and room.wait(10)
        run = client.get("/api/v1/room-agent").json()["run"]
    (name, argv, request_text), = calls
    assert name == "room_session.main" and request_text == REQUEST
    assert argv[:2] == ("live", "--execute") and "--i-authorise-paid-calls" in argv
    assert argv[argv.index("--capability") + 1] == "count_return"
    assert argv[argv.index("--local-ip") + 1] == "192.168.10.2"
    assert argv.count("--assurance") == 2
    assert argv[argv.index("--site") + 1].endswith("canningst-site.json")
    assert argv[argv.index("--profile-file") + 1].endswith("canningst-flight-profile.json")
    assert run["status"] == "REFUSED" and "connectivity preflight" in run["error"]


def test_live_and_jev_rehearsal_need_the_paid_calls_authorisation(tmp_path):
    service, room, client = _client(tmp_path, room_live_config=_live_pack(tmp_path))
    with client:
        for mode in ("LIVE", "JEV_REHEARSAL"):
            refused = client.post("/api/v1/room-agent/proposals",
                                  json=dict(mode=mode, scenario="a", request_text=REQUEST))
            assert refused.status_code == 503
            assert refused.json()["reason_code"] == "PAID_CALLS_NOT_AUTHORISED"


def test_jev_rehearsal_builds_the_real_jev_only_after_approval(tmp_path, fast, monkeypatch):
    built = []

    def fake_jev():
        built.append("jev")
        judge = room_rehearsal.ScriptedJev(REQUEST)
        judge.identity = "jev:test-double"
        return judge

    monkeypatch.setattr(live_stack, "default_jev", fake_jev)
    service, room, client = _client(tmp_path, authorise_paid_calls=True)
    with client:
        proposed = client.post("/api/v1/room-agent/proposals",
                               json=dict(mode="JEV_REHEARSAL", scenario="a", request_text=REQUEST)).json()
        assert built == []
        client.post("/api/v1/room-agent/decisions",
                    json=dict(proposal_id=proposed["run"]["proposal"]["id"], decision="APPROVE"))
        assert room.wait(120)
        run = client.get("/api/v1/room-agent").json()["run"]
    assert built == ["jev"] and run["status"] == "COMPLETED" and run["returned_home"]
