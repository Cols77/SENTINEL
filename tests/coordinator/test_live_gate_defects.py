"""Regressions for three defects the 2026-09-20 consented live runs exposed.

All three were invisible to the hermetic suite: the fakes never took the paths
the real provider and the real judges take.
"""
from __future__ import annotations

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.judgment import LlmJudge

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, close, envelope, open_harness


def _tool_call(request, tool_name, arguments):
    return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
            "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
            "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                            "tool_name": tool_name, "arguments": arguments}]}


def test_recent_events_tool_survives_a_populated_event_log(tmp_path):
    """The wire schema's event_list items admit no properties, so a populated
    events array fails validation and kills the run. Summaries travel in
    safe_summary until the amendment lands."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        seen = []

        def policy(request):
            seen.append(request)
            if len(seen) == 1:
                return _tool_call(request, "state.get_recent_events", {"after_sequence": 0, "limit": 16})
            return _tool_call(request, "finish", {"status": "NEEDS_REVIEW", "summary": "done looking"})

        boundary = CoordinatorBoundary(repo, gateway)
        result = AgentLoop(boundary, policy, max_turns=3, repository=repo).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["status"] == "NEEDS_REVIEW"
        assert len(seen) == 2, "the loop must survive the read tool and take another turn"
        previous = seen[1]["previous_result"]
        assert previous["tool_name"] == "state.get_recent_events"
        assert previous["status"] == "ACCEPTED"
        # the model still learns what happened, through the free-form summary
        assert "RUN_CREATED" in previous["safe_summary"]
        assert "USER_MESSAGE_RECEIVED" in previous["safe_summary"]
        assert len(previous["safe_summary"]) <= 512
    finally:
        close(repo)


def test_a_raising_policy_is_not_reported_as_an_invalid_model_result(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))

        def broken_policy(request):
            raise ModuleNotFoundError("No module named 'openai'")

        boundary = CoordinatorBoundary(repo, gateway)
        result = AgentLoop(boundary, broken_policy, max_turns=4, repository=repo).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["error_code"] == "AGENT_CALL_FAILED"
        assert "ModuleNotFoundError" in result["summary"]
    finally:
        close(repo)


def test_a_schema_invalid_model_result_still_reports_invalid_agent_result(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))

        def malformed_policy(request):
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": "task.create", "arguments": {}}]}

        boundary = CoordinatorBoundary(repo, gateway)
        result = AgentLoop(boundary, malformed_policy, max_turns=4, repository=repo).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["error_code"] == "INVALID_AGENT_RESULT"
    finally:
        close(repo)


def test_llm_judge_choice_accepts_criteria_like_the_jev_judge():
    """JevPolicy passes label descriptions as `criteria`; the LLM advisor
    raised TypeError on every call until this matched (live sweep: error_rate 1.0)."""
    captured = {}

    def transport(url, headers, payload):  # LlmJudge's transport seam takes the full call
        captured.update(payload)
        return {"choices": [{"message": {"content": '{"answers": {"pick": {"choice": "create_task", '
                                                    '"probabilities": {"create_task": 0.8, "escalate": 0.2}, '
                                                    '"confidence": 0.8}}}'}}]}

    judge = LlmJudge(api_key_env="TEST_KEY", base_url="https://example.invalid", model="m", transport=transport)
    import os
    os.environ["TEST_KEY"] = "x"
    try:
        answer = judge.choice({"facts": {}}, "Pick one.",
                              criteria={"create_task": "create a patrol task", "escalate": "ask the operator"},
                              name="pick")
    finally:
        os.environ.pop("TEST_KEY", None)
    assert answer["value"] == "create_task"
    body = str(captured)
    assert "create a patrol task" in body and "ask the operator" in body
