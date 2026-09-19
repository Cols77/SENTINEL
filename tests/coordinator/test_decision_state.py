"""Tests for the DecisionState read model (Task 1)."""
from __future__ import annotations

import json
import re

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.decision_state import read_decision_state, score_band

from .harness import (
    AGENT_RUN_ID,
    RESULT_ID,
    RUN_ID,
    CoordinatorBoundary,
    FakeWorker,
    close,
    envelope,
    open_harness,
)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")


def _plan_reference(repo):
    plan = repo.connection.execute("SELECT * FROM plans").fetchone()
    return {
        "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }


def test_decision_state_after_run_create_has_request_text_and_no_plan(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope(
            "RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR",
        ))

        state = read_decision_state(repo, RUN_ID)

        assert state.request_text == "inspect one station"
        assert state.facts()["plan_exists"] is False
        packet_json = json.dumps(state.to_packet())
        assert _UUID_RE.search(packet_json) is None
    finally:
        close(repo)


def test_decision_state_after_detection_has_categorical_facts_and_ids(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope(
            "RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR",
        ))
        calls = []

        def initial_agent(request):
            calls.append(request)
            if len(calls) == 1:
                tool, args = "state.get_summary", {}
            else:
                tool, args = "inspection.propose_plan", {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"}
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": tool, "arguments": args}]}

        boundary = CoordinatorBoundary(repo, gateway)
        AgentLoop(boundary, initial_agent, max_turns=3).run(RUN_ID, AGENT_RUN_ID, "inspect one station")
        reference = _plan_reference(repo)
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE",
                         repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])

        state = read_decision_state(repo, RUN_ID)
        facts = state.facts()

        assert facts["detection_status"] == "DETECTED"
        assert facts["detection_score_band"] == "very_high"
        assert facts["detection_inside_plan"] is True
        assert facts["detection_duplicates_open_task"] is False
        assert state.ids()["perception_result_id"] == RESULT_ID
    finally:
        close(repo)


@pytest.mark.parametrize("score,expected", [
    (0.99, "very_high"),
    (0.8, "high"),
    (0.6, "moderate"),
    (0.5, "marginal"),
    (0.3, "below_threshold"),
    (None, None),
])
def test_score_band(score, expected):
    assert score_band(score) == expected
