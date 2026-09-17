"""Required A15 one-station coordinator vertical-slice evidence."""
from __future__ import annotations

import json

from sentinel.coordinator.agent_loop import AgentLoop

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, close, envelope, open_harness


def _plan_reference(repo):
    plan = repo.connection.execute("SELECT * FROM plans").fetchone()
    return {
        "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }


def test_one_station_flow_runs_from_run_create_to_completed(tmp_path):
    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
        calls = []

        def initial_agent(request):
            calls.append(request)
            if len(calls) == 1:
                tool, args = "state.get_summary", {}
            else:
                tool, args = "inspection.propose_plan", {
                    "catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE",
                }
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": tool, "arguments": args}]}

        boundary = CoordinatorBoundary(repo, gateway)
        result = AgentLoop(boundary, initial_agent, max_turns=3).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["status"] == "ACCEPTED"
        assert result["error_code"] == "WAITING_FOR_APPROVAL"
        reference = _plan_reference(repo)
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
        }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        from .harness import FakeWorker
        worker = FakeWorker(gateway)
        worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
        worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"])
        worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])

        phase_calls = []
        def resumed_agent(request):
            phase_calls.append(request)
            task = repo.connection.execute("SELECT task_id FROM tasks").fetchone()
            if len(phase_calls) == 1:
                tool, args = "task.create", {
                    "perception_result_id": "55555555-5555-4555-8555-555555555555",
                    "section_id": "LEFT_ALLEY_MIDDLE",
                    "evidence_artifact_id": "33333333-3333-4333-8333-333333333333",
                }
            elif len(phase_calls) == 2:
                tool, args = "patroller.rank_for_task", {"task_id": task["task_id"]}
            else:
                tool, args = "assignment.propose", {
                    "task_id": task["task_id"], "patroller_id": "PATROLLER_1",
                    "reason": "deterministic rank-1",
                }
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": tool, "arguments": args}]}

        result = AgentLoop(boundary, resumed_agent, max_turns=4).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")
        assert result["error_code"] == "WAITING_FOR_APPROVAL"
        task_id = repo.connection.execute("SELECT task_id FROM tasks").fetchone()[0]
        gateway.submit(envelope("OPERATOR_DECIDE", {
            "decision": "CONFIRM", "target": {"kind": "TASK", "task_id": task_id},
        }, sequence=30, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))

        assert repo.connection.execute("SELECT status FROM runs WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == "COMPLETED"
        assert repo.connection.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[0] == "ASSIGNED"
        assert repo.connection.execute("SELECT COUNT(*) FROM artifacts WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == 1
        assert repo.connection.execute("SELECT COUNT(*) FROM perception_references WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == 1
        assert [event["event_type"] for event in repo.read_events(RUN_ID, 0, 30)] == [
            "RUN_CREATED", "USER_MESSAGE_RECEIVED", "PLAN_PROPOSED", "PLAN_CREATED",
            "AGENT_INTENT_PROPOSED", "PLAN_APPROVED", "PREFLIGHT_REPORTED",
            "DRONE_DISPATCH_REQUESTED", "DETECTION_RECORDED", "TASK_CREATED",
            "PATROLLER_ASSIGNMENT_PROPOSED", "PATROLLER_ASSIGNMENT_CONFIRMED",
        ]
        assert calls[0]["legal_tools"]
        assert phase_calls[-1]["previous_result"]["result"]["kind"] == "ranking"
    finally:
        close(repo)
