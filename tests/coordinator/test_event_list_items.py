"""``state.get_recent_events`` returns typed events (WP0 ``event_list`` fix,
operator Coherence consent 2026-09-20, proposal
``2026-09-21_wp0-proposal-event-list-items.md``).

Before: the wire schema's ``event_list`` items were ``{"type": "object",
"additionalProperties": false}`` with no properties, so only ``{}`` validated,
the loop returned ``"events": []``, and every event shared one 512-character
``safe_summary``. Replaying the 2026-09-20 live run's database showed the
newest event -- the only one the model needed -- was the one cut off.
"""
from __future__ import annotations

import pytest

from sentinel.contracts import AgentToolResult
from sentinel.coordinator.agent_loop import AgentLoop

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, close, envelope, open_harness

ITEM = {"sequence_no": 3, "event_type": "PLAN_PROPOSED", "summary": "PLAN_PROPOSED: {}"}


def _tool_result(events: list) -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_tool_result",
        "tool_name": "state.get_recent_events", "status": "ACCEPTED", "error_code": None,
        "state_version": 1, "event_cursor": 3, "legal_tools": ["finish"],
        "safe_summary": "1 event(s)", "result": {"kind": "event_list", "events": events},
    }


def test_the_schema_accepts_a_populated_event():
    AgentToolResult.from_dict(_tool_result([ITEM]))


def test_the_schema_still_accepts_an_empty_list():
    AgentToolResult.from_dict(_tool_result([]))


@pytest.mark.parametrize("bad", [
    {**ITEM, "payload": {"raw": "event body"}},                      # no extra fields
    {"sequence_no": 3, "event_type": "PLAN_PROPOSED"},              # summary required
    {**ITEM, "sequence_no": -1},
    {**ITEM, "summary": "x" * 513},
    {**ITEM, "event_type": "x" * 65},
])
def test_the_schema_rejects_a_malformed_event(bad):
    with pytest.raises(Exception):
        AgentToolResult.from_dict(_tool_result([bad]))


def _tool_call(request, tool_name, arguments):
    return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
            "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
            "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                            "tool_name": tool_name, "arguments": arguments}]}


def test_the_loop_returns_each_event_as_its_own_typed_item(tmp_path):
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

        AgentLoop(CoordinatorBoundary(repo, gateway), policy, max_turns=3, repository=repo).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        events = seen[1]["previous_result"]["result"]["events"]
        assert [e["event_type"] for e in events] == ["RUN_CREATED", "USER_MESSAGE_RECEIVED"]
        assert [e["sequence_no"] for e in events] == sorted(e["sequence_no"] for e in events)
        assert all(set(e) == {"sequence_no", "event_type", "summary"} for e in events)
        assert "inspect one station" in events[1]["summary"]
    finally:
        close(repo)
