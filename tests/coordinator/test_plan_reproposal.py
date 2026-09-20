"""Re-proposing a plan already proposed in the run must surface as a typed
CoordinatorError, not as a raw sqlite3.IntegrityError escaping the gateway.

Found by the P3 session on 2026-09-20: plans.plan_sha256 is UNIQUE (and
plan_id is derived from run_id + catalogue entry, so it collides too), so the
second proposal raised sqlite3.IntegrityError straight out of CommandGateway,
past AgentLoop's CoordinatorError handling, killing the run with a traceback.
"""
from __future__ import annotations

import sqlite3

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.errors import CoordinatorError

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, close, envelope, open_harness

PLAN = "PLAN_ROUTE_ONLY_2_ZONE"


def _propose(sequence: int) -> dict:
    return envelope("AGENT_INTENT_PROPOSE",
                    {"intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": PLAN},
                    sequence=sequence)


def test_reproposing_the_same_plan_raises_a_typed_error(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        gateway.submit(_propose(2))
        with pytest.raises(CoordinatorError) as caught:
            gateway.submit(_propose(3))
        assert caught.value.code == "PLAN_ALREADY_PROPOSED"
        assert not isinstance(caught.value, sqlite3.Error)
        # the run survives: state is still readable and the plan row is intact
        assert repo.connection.execute(
            "SELECT COUNT(*) FROM plans WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == 1
    finally:
        close(repo)


def test_agent_loop_reports_a_rejection_instead_of_crashing(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        gateway.submit(_propose(2))
        # approve it, so the run leaves WAITING_FOR_APPROVAL and the loop really
        # reaches the tool call instead of returning early
        from .test_one_station_flow import _plan_reference
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": _plan_reference(repo)},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        # a policy that proposes the same plan again must not take the run down
        def repropose(request):
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": "inspection.propose_plan",
                                    "arguments": {"catalogue_plan_id": PLAN}}]}

        boundary = CoordinatorBoundary(repo, gateway)
        result = AgentLoop(boundary, repropose, max_turns=3).run(RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["status"] in {"FAILED", "NEEDS_REVIEW", "ACCEPTED"}
        assert result.get("error_code") != "INVALID_AGENT_RESULT"
    finally:
        close(repo)


def test_other_integrity_violations_are_typed_too(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        # force an unrelated constraint violation through the same entry point
        with pytest.raises(CoordinatorError) as caught:
            gateway.repository.connection.execute("PRAGMA foreign_keys = ON")
            gateway._raise_typed_integrity_error(  # type: ignore[attr-defined]
                sqlite3.IntegrityError("UNIQUE constraint failed: task_rankings.task_id"))
        assert caught.value.code == "STATE_CONSTRAINT_VIOLATION"
    finally:
        close(repo)
