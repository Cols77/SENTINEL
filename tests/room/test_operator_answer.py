"""The mission-end operator answer: prompt assembly, graceful skip, failure safety."""
from __future__ import annotations

import json
from types import SimpleNamespace

from sentinel.room import operator_answer as oa


def _event(payload):
    return SimpleNamespace(event_type="ROOM_TOOL_OUTCOME", payload=payload)


def _frame(*classes):
    return {"estimates": [{"class_name": name} for name in classes], "people_confirmed": 0}


EVENTS = (
    SimpleNamespace(event_type="SAFETY_ASSESSED", payload={}),
    _event({"candidate_id": "OBSERVE:g1:c1:center-rear", "code": "OBSERVE_SUCCEEDED",
            "count_evidences": [_frame("mug"), _frame("mug", "phone")]}),
    _event({"candidate_id": "OBSERVE:g1:c1:left-rear", "code": "ZONE_REFUSED"}),
    _event({"candidate_id": "ANSWER:COUNTS", "code": "ANSWERED_COUNT", "terminal_state": "COMPLETED",
            "answer": "Objective g1:c1 (room-wide count): mugs 1 confirmed",
            "count_projection": [{"scope": "ROOM", "counts": [
                {"class_name": "mug", "confirmed": 1, "uncertain": 1, "unplaced": 0}],
                "coverage": {"complete": False, "uncovered": ["left:mug"]}}]}),
)
TERMINAL = {"state": "COMPLETED", "landing_state": "LANDED", "took_off": True, "reason": "CONTROLLER_TERMINAL"}


def test_findings_and_prompt_carry_request_counts_and_missed_zones():
    findings = oa.collect_findings(EVENTS, TERMINAL)
    assert findings["zones_observed"] == ["center-rear"]
    assert findings["zones_not_observed"] == [{"zone": "left-rear", "reason": "ZONE_REFUSED"}]
    assert findings["zones"][0]["detections"] == {"mug": 2, "phone": 1}
    assert findings["counts"] == [{"class_name": "mug", "confirmed": 1, "uncertain": 1, "unplaced": 0,
                                   "scope": "ROOM"}]
    assert findings["coverage"] == {"complete": False, "uncovered": ["left:mug"]}
    assert findings["landing_state"] == "LANDED"

    messages = oa.build_messages("Count the mugs in the room", findings)
    assert messages[0]["role"] == "system"
    user = json.loads(messages[1]["content"])
    assert user["operator_request"] == "Count the mugs in the room"
    assert user["findings"]["counts"][0]["class_name"] == "mug"


def test_unauthorised_run_writes_deterministic_answer_without_calling(tmp_path):
    def transport(*_):
        raise AssertionError("no call may be made without authorisation")

    document = oa.finalize_operator_answer(tmp_path, request_text="Count the mugs in the room", events=EVENTS,
                                           terminal=TERMINAL, authorised=False, transport=transport)
    assert document["source"] == "fallback"
    assert "1 mug confirmed and 1 uncertain" in document["operator_answer"]
    assert "left-rear (ZONE_REFUSED)" in document["operator_answer"]
    assert oa.read_operator_answer(tmp_path)["operator_answer"] == document["operator_answer"]


def test_authorised_call_uses_the_model_text(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    seen = {}

    def transport(url, headers, payload):
        seen["payload"] = payload
        return {"model": oa.DEFAULT_MODEL, "choices": [{"message": {"content": "I found one mug."}}]}

    document = oa.finalize_operator_answer(tmp_path, request_text="Count the mugs", events=EVENTS,
                                           terminal=TERMINAL, authorised=True, transport=transport)
    assert document["source"] == "llm" and document["operator_answer"] == "I found one mug."
    assert seen["payload"]["model"] == oa.DEFAULT_MODEL and "tools" not in seen["payload"]


def test_failing_call_falls_back_and_leaves_run_artifacts_intact(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    terminal_bytes = json.dumps(TERMINAL).encode()
    summary_bytes = b'{"run_id": "r1", "terminal_state": "COMPLETED"}'
    (tmp_path / "terminal.json").write_bytes(terminal_bytes)
    (tmp_path / "summary.json").write_bytes(summary_bytes)

    def transport(*_):
        raise ConnectionError("network down")

    document = oa.finalize_operator_answer(tmp_path, request_text="Count the mugs", events=EVENTS,
                                           terminal=TERMINAL, authorised=True, transport=transport)
    assert document["source"] == "fallback" and "network down" in document["error"]
    assert (tmp_path / "terminal.json").read_bytes() == terminal_bytes
    assert (tmp_path / "summary.json").read_bytes() == summary_bytes
    assert json.loads((tmp_path / oa.ANSWER_FILENAME).read_text(encoding="utf-8"))["source"] == "fallback"


def test_unwritable_run_dir_never_raises(tmp_path):
    not_a_dir = tmp_path / "a-file"
    not_a_dir.write_text("x")
    assert oa.finalize_operator_answer(not_a_dir, request_text="x", events=(), authorised=False) is None


def test_mission_control_projection_shows_the_answer():
    from sentinel.ui.room_agent_projection import RoomAgentProjection

    projection = RoomAgentProjection()
    projection.set_operator_answer(None)  # no file: nothing shown
    assert projection.public()["operator_answer"] is None
    projection.set_operator_answer({"operator_answer": "I found one mug.", "source": "llm", "model": "m"})
    state = projection.public()
    assert state["operator_answer"] == {"text": "I found one mug.", "source": "llm", "model": "m"}
    assert state["reanchor_log"][-1]["kind"] == "OPERATOR_ANSWER"
