"""D1-D7: the loop sees real payloads, a computed legal set, and gateway
rejections as feedback -- all within the current sentinel.v0 wire schema."""
from __future__ import annotations

from sentinel.contracts import validate_document
from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.legal_tools import EVIDENCE_GATE, load_catalogue_plans

from .harness import AGENT_RUN_ID, RUN_ID, CoordinatorBoundary, FakeWorker, close, envelope, open_harness


def _plan_reference(repo):
    plan = repo.connection.execute("SELECT * FROM plans").fetchone()
    return {
        "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": __import__("json").loads(plan["station_ids_json"]),
    }


def _drive_to_evidence_gate(repo, gateway):
    """RUN_CREATE -> propose+approve the route-only plan -> preflight/dispatch/perceive.
    Leaves the run sitting at EVIDENCE_GATE, with one DETECTED perception on file."""
    gateway.submit(envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR"))
    gateway.submit(envelope("AGENT_INTENT_PROPOSE", {
        "intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE",
    }, sequence=2, actor_type="AGENT"))
    reference = _plan_reference(repo)
    gateway.submit(envelope("OPERATOR_DECIDE", {
        "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
    }, sequence=10, actor_type="OPERATOR", expected=repo.read_state(RUN_ID).to_dict()["state_version"]))
    worker = FakeWorker(gateway)
    worker.preflight(RUN_ID, reference["plan_id"], reference["plan_sha256"])
    worker.dispatch(RUN_ID, reference["plan_id"], "LEFT_ALLEY_MIDDLE", repo.read_state(RUN_ID).to_dict()["state_version"])
    worker.capture_and_perceive(RUN_ID, reference["plan_id"], reference["plan_sha256"])
    return reference


def test_event_context_evidence_context_and_legal_set_reach_the_request(tmp_path):
    """(a) event_context carries the real DETECTION_RECORDED payload (D1).
    (b) evidence_context surfaces the DETECTED perception with its score band (D2).
    (c) legal_tools is the computed legal set, not the fixed 8-tool list (D3).
    (g) every request the loop builds still validates against the wire schema."""
    repo, gateway = open_harness(tmp_path)
    try:
        _drive_to_evidence_gate(repo, gateway)
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()

        requests = []

        def escalate_at_evidence_gate(request):
            requests.append(request)
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": "finish",
                                    "arguments": {"status": "NEEDS_REVIEW", "summary": "hand back for review"}}]}

        AgentLoop(boundary, escalate_at_evidence_gate, max_turns=1, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")

        assert len(requests) == 1
        request = requests[0]
        validate_document(request)  # (g)

        detection_entries = [e for e in request["event_context"] if e["summary"].startswith("DETECTION_RECORDED:")]
        assert detection_entries, request["event_context"]
        assert "LEFT_ALLEY_MIDDLE" in detection_entries[0]["summary"]  # (a)

        assert len(request["evidence_context"]) == 1  # (b)
        evidence = request["evidence_context"][0]
        assert evidence["status"] == "DETECTED"
        assert "score_band=very_high" in evidence["summary"]

        assert request["legal_tools"] == ["finish", "state.get_recent_events", "state.get_summary", "task.create"]  # (c)
    finally:
        close(repo)


def test_gateway_rejection_feeds_back_without_raising(tmp_path):
    """(d) a gateway-rejected mutation comes back as REJECTED with a non-null
    error_code in the *next* request's previous_result, and run() does not raise."""
    repo, gateway = open_harness(tmp_path)
    try:
        _drive_to_evidence_gate(repo, gateway)
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()

        requests = []

        def wrong_then_correct_then_finish(request):
            requests.append(request)
            if len(requests) == 1:
                tool, args = "task.create", {
                    "perception_result_id": "55555555-5555-4555-8555-555555555555",
                    "section_id": "NOT_A_REAL_STATION",
                    "evidence_artifact_id": "33333333-3333-4333-8333-333333333333",
                }
            elif len(requests) == 2:
                tool, args = "task.create", {
                    "perception_result_id": "55555555-5555-4555-8555-555555555555",
                    "section_id": "LEFT_ALLEY_MIDDLE",
                    "evidence_artifact_id": "33333333-3333-4333-8333-333333333333",
                }
            else:
                tool, args = "finish", {"status": "NEEDS_REVIEW", "summary": "task created, hand back"}
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": tool, "arguments": args}]}

        result = AgentLoop(boundary, wrong_then_correct_then_finish, max_turns=3, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")

        assert len(requests) == 3
        feedback = requests[1]["previous_result"]
        assert feedback["status"] == "REJECTED"
        assert feedback["error_code"]
        assert result["status"] == "NEEDS_REVIEW"  # run() returned instead of raising

        # (f), adapted: the schema locks a mutation's `result` payload to just
        # {"kind": ...} (typed ids are a Task 6 amendment), so D5 surfaces the
        # newly created id through `safe_summary` instead of `event_context`,
        # since the TASK_CREATED event's own payload never carried a task_id.
        task_id = repo.connection.execute("SELECT task_id FROM tasks").fetchone()[0]
        assert task_id in requests[2]["previous_result"]["safe_summary"]
        assert requests[2]["previous_result"]["status"] == "ACCEPTED"
    finally:
        close(repo)


def test_three_consecutive_gateway_rejections_exhaust_the_corrective_budget(tmp_path):
    """(e) three consecutive gateway rejections are terminal with GATEWAY_REJECTED,
    sharing the same 2-retry corrective budget as a schema-invalid result."""
    repo, gateway = open_harness(tmp_path)
    try:
        _drive_to_evidence_gate(repo, gateway)
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()

        def always_wrong(request):
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result",
                    "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS",
                    "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call",
                                    "tool_name": "task.create",
                                    "arguments": {
                                        "perception_result_id": "55555555-5555-4555-8555-555555555555",
                                        "section_id": "NOT_A_REAL_STATION",
                                        "evidence_artifact_id": "33333333-3333-4333-8333-333333333333",
                                    }}]}

        result = AgentLoop(boundary, always_wrong, max_turns=4, repository=repo, catalogue=catalogue).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station")

        assert result["status"] == "FAILED"
        assert result["error_code"] == "GATEWAY_REJECTED"
    finally:
        close(repo)
