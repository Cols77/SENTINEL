"""A model that uses a tool's documented optional ``reason`` must not be
rejected for it.

The wire contract defines each mutating tool twice: the arguments the model may
pass (``Agent*Arguments``) and the payload the command carries
(``Command*Payload``). For ``inspection.propose_plan``, ``task.create`` and
``patroller.rank_for_task`` the first admits an optional ``reason`` and the
second does not. ``command_for`` copied arguments into the payload verbatim, so
the gateway answered ``INVALID_CONTRACT`` to a perfectly good call.

This is a replay of what a paid run actually did
(``artifacts/wp1/live-runs/2026-09-20-operator-grant-3-qualifying_evidence_to_task.json``):
turns 1 and 2 were correct ``task.create`` calls carrying a ``reason`` and were
rejected; turn 5, identical but without it, was accepted. The run then ran out
of turns.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sentinel.contracts import load_schema
from sentinel.coordinator.agent_loop import _PAYLOAD_KEYS, command_for
from sentinel.coordinator.policy import one_call

from tests.agent import test_live_gate_real_loop as live_gate

RECORD = (Path(__file__).resolve().parents[2] / "artifacts" / "wp1" / "live-runs"
          / "2026-09-20-operator-grant-3-qualifying_evidence_to_task.json")


def _recorded_first_call() -> dict:
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    call = record["model_results"][0]["tool_calls"][0]
    assert call["tool_name"] == "task.create" and "reason" in call["arguments"]
    return call["arguments"]


def test_the_recorded_live_call_now_completes_the_scenario(tmp_path):
    """The model's own turn-1 arguments, replayed, then the two calls it never
    got the turns to make -- each carrying a reason."""
    first = _recorded_first_call()

    def policy(request):
        previous = request.get("previous_result") or {}
        result = previous.get("result") or {}
        if result.get("kind") == "task":
            return one_call(request, "patroller.rank_for_task",
                            {"task_id": result["task_id"], "reason": "rank the available patrollers"})
        if result.get("kind") == "ranking":
            task_id = policy.task_id
            return one_call(request, "assignment.propose",
                            {"task_id": task_id, "patroller_id": result["patroller_ids"][0],
                             "reason": "top-ranked patroller"})
        return one_call(request, "task.create", first)

    def remembering(request):
        result = (request.get("previous_result") or {}).get("result") or {}
        if result.get("kind") == "task":
            policy.task_id = result["task_id"]
        return policy(request)

    outcome = live_gate.run_real_loop_scenario("qualifying_evidence_to_task", remembering, tmp_path=tmp_path)
    assert outcome["calls"] == ["task.create", "patroller.rank_for_task", "assignment.propose"]
    assert outcome["result"].get("error_code") == "WAITING_FOR_APPROVAL"
    rejected = [r["previous_result"] for r in outcome["requests"][1:] if r["previous_result"]["status"] != "ACCEPTED"]
    assert rejected == []


@pytest.mark.parametrize("tool_name, arguments_def, payload_def", [
    ("inspection.propose_plan", "AgentPlanProposalArguments", "CommandAgentIntentProposePayload"),
    ("task.create", "AgentTaskCreateArguments", "CommandTaskCreatePayload"),
    ("patroller.rank_for_task", "AgentTaskRankArguments", "CommandTaskRankPayload"),
    ("assignment.propose", "AgentAssignmentProposalArguments", "CommandAssignmentProposePayload"),
])
def test_payload_keys_are_exactly_what_both_contracts_share(tool_name, arguments_def, payload_def):
    """Derived from the schema, so a future amendment that adds an argument or
    a payload field cannot silently fall out of step with ``_PAYLOAD_KEYS``."""
    defs = load_schema("wire")["$defs"]
    arguments = set(defs[arguments_def]["properties"])
    payload = set(defs[payload_def]["properties"]) - {"intent"}   # `intent` is added by command_for itself
    assert set(_PAYLOAD_KEYS[tool_name]) == arguments & payload
    assert set(defs[payload_def].get("required", [])) - {"intent"} <= set(_PAYLOAD_KEYS[tool_name])


def test_an_optional_reason_never_reaches_a_payload_that_forbids_it():
    command = command_for("task.create", _recorded_first_call(), "11111111-1111-4111-8111-111111111111",
                          "AGENT", "22222222-2222-4222-8222-222222222222", "k")
    assert "reason" not in command["payload"]
    assert set(command["payload"]) == {"perception_result_id", "section_id", "evidence_artifact_id"}


def test_a_required_reason_still_reaches_the_assignment_payload():
    command = command_for("assignment.propose",
                          {"task_id": "77777777-7777-4777-8777-777777777777", "patroller_id": "PATROLLER_A",
                           "reason": "closest"},
                          "11111111-1111-4111-8111-111111111111", "AGENT",
                          "22222222-2222-4222-8222-222222222222", "k")
    assert command["payload"]["reason"] == "closest"
