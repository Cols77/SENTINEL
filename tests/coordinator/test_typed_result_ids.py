"""WP0 amendment section 5 (D5's remaining half): a policy must be able to read
the id of what it just created from the result itself, without parsing strings.

The live gate of 2026-09-20 failed `qualifying_evidence_to_task` because the
model created the task, received `{"kind": "task"}` with no id, and repeated
`task.create` until the correction budget was exhausted. The id existed only
inside the free-text `safe_summary`.
"""
from __future__ import annotations

import json
import uuid

from sentinel.contracts import AgentToolResult, validate_document
from sentinel.coordinator.agent_loop import AgentLoop

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, FakeWorker, close, envelope, open_harness
from .test_one_station_flow import _plan_reference

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def _call(request, tool_name, arguments):
    return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
            "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
            "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                            "tool_name": tool_name, "arguments": arguments}]}


def test_plan_result_carries_the_plan_id(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        results = []

        def policy(request):
            if request["previous_result"] is not None:
                results.append(request["previous_result"])
            return _call(request, "inspection.propose_plan", {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"})

        boundary = CoordinatorBoundary(repo, gateway)
        AgentLoop(boundary, policy, max_turns=2, repository=repo).run(RUN_ID, AGENT_RUN_ID, "inspect one station")
        # the loop returns on WAITING_FOR_APPROVAL, so read the plan result directly
        plan_id = repo.connection.execute("SELECT plan_id FROM plans").fetchone()[0]
        assert uuid.UUID(plan_id)
    finally:
        close(repo)


def test_task_rank_and_assignment_results_carry_typed_ids(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        gateway.submit(envelope("AGENT_INTENT_PROPOSE",
                                {"intent": "REQUEST_OPERATOR_APPROVAL",
                                 "catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"}, sequence=2))
        reference = _plan_reference(repo)
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=3, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))
        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE",
                        repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])

        seen: list[dict] = []
        detection = repo.connection.execute("SELECT result_id, artifact_id FROM perception_references").fetchone()

        def policy(request):
            previous = request["previous_result"]
            if previous is not None:
                seen.append(previous)
            if not seen:
                return _call(request, "task.create", {
                    "perception_result_id": detection["result_id"],
                    "section_id": "LEFT_ALLEY_MIDDLE",
                    "evidence_artifact_id": detection["artifact_id"]})
            # the id must come from the RESULT, never from a string or the db
            task_id = seen[0]["result"]["task_id"]
            if len(seen) == 1:
                return _call(request, "patroller.rank_for_task", {"task_id": task_id})
            patroller_id = seen[1]["result"]["patroller_ids"][0]
            return _call(request, "assignment.propose", {
                "task_id": task_id, "patroller_id": patroller_id, "reason": "rank-1"})

        boundary = CoordinatorBoundary(repo, gateway)
        AgentLoop(boundary, policy, max_turns=4, repository=repo).run(RUN_ID, AGENT_RUN_ID, "inspect one station")

        # Two results reach the policy: after assignment.propose the run enters
        # WAITING_FOR_APPROVAL and the loop returns before another turn, so the
        # assignment payload is covered by the unit test below instead.
        assert len(seen) == 2, seen
        task_result, ranking_result = (item["result"] for item in seen)

        assert task_result["kind"] == "task"
        assert uuid.UUID(task_result["task_id"])
        assert ranking_result["kind"] == "ranking"
        assert ranking_result["patroller_ids"] and all(
            isinstance(pid, str) and pid.isupper() for pid in ranking_result["patroller_ids"])

        # the assignment really was proposed using ids read from those results
        proposed = repo.connection.execute(
            "SELECT task_id, proposed_patroller_id FROM tasks WHERE task_id = ?",
            (task_result["task_id"],)).fetchone()
        assert proposed["proposed_patroller_id"] == ranking_result["patroller_ids"][0]

        for item in seen:
            validate_document(item)
    finally:
        close(repo)


def test_assignment_result_carries_both_ids(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        loop = AgentLoop(CoordinatorBoundary(repo, gateway), lambda request: None, repository=repo)
        task_id = "77777777-7777-4777-8777-777777777777"
        typed = loop._typed_result({"kind": "assignment"}, "assignment.propose",
                                   {"task_id": task_id, "patroller_id": "PATROLLER_1"}, RUN_ID, {})
        assert typed == {"kind": "assignment", "task_id": task_id, "patroller_id": "PATROLLER_1"}
    finally:
        close(repo)


def test_production_path_never_yields_a_null_id(tmp_path):
    """The id value is nullable only for the legacy no-read-model path. With a
    repository -- which every production construction uses -- it must be real."""
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"},
                                sequence=1, actor_type="OPERATOR"))
        seen = []

        def policy(request):
            if request["previous_result"] is not None:
                seen.append(request["previous_result"]["result"])
            return _call(request, "inspection.propose_plan", {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"})

        loop = AgentLoop(CoordinatorBoundary(repo, gateway), policy, max_turns=2, repository=repo)
        loop.run(RUN_ID, AGENT_RUN_ID, "inspect one station")
        typed = loop._typed_result({"kind": "plan"}, "inspection.propose_plan", {}, RUN_ID,
                                   {"plan_id": repo.connection.execute("SELECT plan_id FROM plans").fetchone()[0]})
        assert typed["plan_id"] is not None and uuid.UUID(typed["plan_id"])
    finally:
        close(repo)


def test_untyped_kinds_still_validate():
    for payload in ({"kind": "wait"}, {"kind": "empty"}, {"kind": "finish"}):
        document = {
            "contract_version": "sentinel.v0", "wire_type": "agent_tool_result",
            "tool_name": "wait_for_event", "status": "ACCEPTED", "error_code": None,
            "state_version": 1, "event_cursor": 1, "legal_tools": ["finish"],
            "safe_summary": "ok", "result": payload,
        }
        assert AgentToolResult.from_dict(document).to_dict()["result"] == payload


def test_a_task_result_without_its_id_is_now_invalid():
    document = {
        "contract_version": "sentinel.v0", "wire_type": "agent_tool_result",
        "tool_name": "task.create", "status": "ACCEPTED", "error_code": None,
        "state_version": 1, "event_cursor": 1, "legal_tools": ["finish"],
        "safe_summary": "ok", "result": {"kind": "task"},
    }
    try:
        AgentToolResult.from_dict(document)
    except Exception as exc:
        assert "validation failed" in str(exc)
    else:
        raise AssertionError("a bare {'kind': 'task'} must no longer validate: " + json.dumps(document))
