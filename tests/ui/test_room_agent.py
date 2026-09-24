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
from sentinel.room.store import RoomStore
from sentinel.ui import room_agent, room_rehearsal
from sentinel.ui.app import build_app
from sentinel.ui.bootstrap import SessionConfig, build_service
from sentinel.ui.room_agent import attach_room_agent

REQUEST = "count the phones and computers in the room"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _free_command_port(monkeypatch):
    # The real UDP 8889 may be held by an operator's Live server on this
    # machine; the Live gates under test must not depend on it.
    monkeypatch.setattr(room_agent.RoomAgentService, "_command_port_status",
                        lambda self, local_ip: dict(status="FREE"))


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
    # Jev modes need paid calls authorised, and Live needs a flight pack.
    assert modes == {"REHEARSAL": True, "JEV_REHEARSAL": False, "LIVE": False}
    assert [row["id"] for row in body["catalogue"]["scenarios"]] == ["a", "b"]
    assert body["run"] is None


def test_recorded_review_is_a_separate_live_only_proposal_choice(tmp_path):
    service, room, client = _client(tmp_path, room_live_config=_live_pack(tmp_path),
                                    authorise_paid_calls=True)
    with client:
        catalogue = client.get("/api/v1/room-agent").json()["catalogue"]
        assert [row["id"] for row in catalogue["perception_reviews"]] == ["STANDARD", "RECORDED_REVIEW"]
        rejected = client.post("/api/v1/room-agent/proposals", json=dict(
            mode="REHEARSAL", scenario="a", request_text=REQUEST,
            perception_review="RECORDED_REVIEW"))
        assert rejected.status_code == 422
        proposed = client.post("/api/v1/room-agent/proposals", json=dict(
            mode="LIVE", scenario="a", request_text=REQUEST,
            perception_review="RECORDED_REVIEW")).json()
    assert proposed["run"]["proposal"]["perception_review"] == "RECORDED_REVIEW"
    assert "record" in " ".join(proposed["run"]["proposal"]["lines"]).lower()
    argv, _, _ = room.live_argv(room.run)
    assert "--record-video" in argv


def test_current_completed_run_exposes_recording_and_rejects_unstaged_analysis(tmp_path):
    service, room, client = _client(tmp_path)
    run_dir = tmp_path / "completed"
    run_dir.mkdir()
    (run_dir / "flight.h264").write_bytes(b"recorded-stream")
    run = room_agent.RoomAgentRun(mode="LIVE", scenario="a", request_text=REQUEST,
                                  local_ip=None, perception_review="RECORDED_REVIEW")
    run.id = "l3-completed"
    run.frame_root = run_dir
    run.status = "COMPLETED"
    run.done.set()
    room.run = run
    with client:
        state = client.get("/api/v1/room-agent").json()["run"]
        assert state["review"]["recording"]["status"] == "READY"
        recording = client.get("/api/v1/room-agent/runs/l3-completed/recording")
        assert recording.content == b"recorded-stream"
        refused = client.post("/api/v1/room-agent/runs/l3-completed/analysis")
        assert refused.status_code == 503
        assert refused.json()["reason_code"] == "MODEL_NOT_STAGED"
        assert client.get("/api/v1/room-agent/runs/other/recording").status_code == 404


def test_a_rehearsal_run_reaches_an_answer_and_returns_home(tmp_path, fast):
    service, room, client = _client(tmp_path)
    with client:
        run = _run(client, room)
    assert run["status"] == "COMPLETED", run["error"]
    assert run["answer"]["code"] == "ANSWERED_COUNT"
    # Three overlapping frames from one declared pose are one corroborating
    # view, so each unique object remains uncertain until another pose sees it.
    assert run["answer"]["summary"] == (
        "phones: 0 confirmed, 2 uncertain; computers: 0 confirmed, 2 uncertain"), run["answer"]
    assert run["returned_home"] is True
    assert run["landing"] == {"state": "LANDED", "reason": "REQUESTED"}
    assert any(region["status"] == "OBSERVED" for region in run["regions"])
    session_path = tmp_path / "data" / "room-agent" / run["id"] / "session.sqlite3"
    assert session_path.exists()
    store = RoomStore.open(session_path)
    try:
        observed = [event.payload for event in store.events(run["id"])
                    if event.event_type == "ROOM_TOOL_OUTCOME"
                    and event.payload.get("code") == "OBSERVE_SUCCEEDED"]
    finally:
        store.close()
    assert observed
    assert all(len(payload["count_evidences"]) == 3 for payload in observed)
    assert run["captures"] == room.run.world.detector.calls == 3 * len(observed)
    assert len(run["detections"]) >= 4
    assert any(d["confidence"] == "low" for d in run["detections"])
    assert run["decisions"] and all(d["reason"] for d in run["decisions"])
    assert run["decisions"][-1]["action"] == "ANSWER:COUNTS"
    assert run["yaw_rotations"] == 0
    assert sum(command in ("cw 30", "ccw 30") for command in room.run.world.world.sent) >= 3
    # The runner is released once the run is terminal.
    assert service.registry.get(service.config.run_id) is None


@pytest.mark.parametrize("battery,succeeds", [(70, False), (71, True)])
def test_rehearsal_sweep_battery_admission(tmp_path, fast, monkeypatch, battery, succeeds):
    original_build = room_rehearsal.build_world

    def build_with_battery(*args, **kwargs):
        built = original_build(*args, **kwargs)
        built.world.battery = battery
        return built

    monkeypatch.setattr(room_rehearsal, "build_world", build_with_battery)
    service, room, client = _client(tmp_path)
    with client:
        run = _run(client, room)
    assert run["captures"] == (3 if succeeds else 0)
    assert run["status"] == ("COMPLETED" if succeeds else "SAFE_LANDED")
    assert run["returned_home"] and run["landing"]["state"] == "LANDED"


def test_scenario_b_a_tag_loss_during_the_sweep_coasts_and_the_count_still_completes(tmp_path, fast):
    """Permissive positioning: the scenario's tag loss no longer forces a
    recovery (and a possible landing); the tracker coasts through it and the
    mission answers, returns and lands."""
    service, room, client = _client(tmp_path)
    with client:
        run = _run(client, room, scenario="b")
    assert run["status"] == "COMPLETED", run["error"]
    states = [row["state"] for row in run["reanchor_log"] if row["kind"] == "LOCALIZATION"]
    assert "COASTING" in states and "LOST" not in states[1:]
    assert run["answer"]["summary"].startswith("phones:")
    assert run["returned_home"] is True and run["landing"]["state"] == "LANDED"


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
                clock_domain_id="operator-l3-test", run_root=str(tmp_path / "runs"), local_ip="192.168.10.2",
                assurances_expire_at="2099-01-01T00:00:00+00:00", run_id="l3-test")
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
        assert proposed["status"] == "ACKNOWLEDGED", proposed
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
