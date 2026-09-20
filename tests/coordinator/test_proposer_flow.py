"""Integration evidence for architecture E's approval-gated proposer flow."""
from __future__ import annotations

import json

from sentinel.contracts import AgentModelResult, AgentRequest
from sentinel.coordinator import LlmProposer
from sentinel.coordinator.architecture import build_policy
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.driver import drive_run
from sentinel.coordinator.legal_tools import SELECT_PLAN, legal_set, load_catalogue_plans
from sentinel.coordinator.policy import PolicyContext
from sentinel.coordinator.shadow import MemoryAuditSink

from .harness import AGENT_RUN_ID, FakeWorker, envelope, open_harness
from .p3_helpers import P3Run


ROUTE_PLAN = "PLAN_ROUTE_ONLY_2_ZONE"
FULL_PLAN = "PLAN_MARKER_VERIFIED_6"
CATALOGUE = load_catalogue_plans()


class _LegalProposalAdapter:
    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        self.calls: list[tuple[AgentRequest, PolicyContext]] = []

    def propose(self, request: AgentRequest, context: PolicyContext) -> AgentModelResult:
        self.calls.append((request, context))
        return AgentModelResult.from_dict({
            "contract_version": "sentinel.v0",
            "wire_type": "agent_result",
            "agent_run_id": request.agent_run_id,
            "status": "TOOL_CALLS",
            "tool_calls": [{
                "contract_version": "sentinel.v0",
                "wire_type": "agent_tool_call",
                "tool_name": "inspection.propose_plan",
                "arguments": {
                    "catalogue_plan_id": self.plan_id,
                    "reason": "narrowest catalogue plan covering the requested route",
                },
            }],
        })


def _latest_plan_reference(repo, run_id: str) -> dict:
    row = repo.connection.execute(
        "SELECT plan_id, catalogue_id, catalogue_revision, plan_sha256, station_ids_json "
        "FROM plans WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return {
        "plan_id": row["plan_id"],
        "catalogue_id": row["catalogue_id"],
        "catalogue_revision": row["catalogue_revision"],
        "plan_sha256": row["plan_sha256"],
        "station_ids": json.loads(row["station_ids_json"]),
    }


def test_e_proposal_crosses_agent_loop_and_gateway_before_two_operator_approvals(tmp_path):
    repo, gateway = open_harness(tmp_path)
    run_id = "77777777-7777-4777-8777-777777777777"
    adapter = _LegalProposalAdapter(ROUTE_PLAN)
    sink = MemoryAuditSink()
    policy = build_policy(
        "E",
        advisors=[],
        sink=sink,
        proposer=LlmProposer(adapter, sink),
    )
    approval_states: list[DecisionState] = []

    def approve(state: DecisionState) -> None:
        approval_states.append(state)
        if not state.plan_approved:
            event_types = [event["event_type"] for event in repo.read_events(run_id, 0, 30)]
            assert event_types == [
                "RUN_CREATED",
                "USER_MESSAGE_RECEIVED",
                "PLAN_PROPOSED",
                "PLAN_CREATED",
                "AGENT_INTENT_PROPOSED",
            ]
            assert repo.connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE run_id = ?", (run_id,)
            ).fetchone()[0] == 0
            assert state.run_status == "WAITING_FOR_APPROVAL"
            assert state.proposed_catalogue_plan_ids == (ROUTE_PLAN,)

            reference = _latest_plan_reference(repo, run_id)
            gateway.submit(envelope(
                "OPERATOR_DECIDE",
                {"decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference}},
                sequence=10,
                run_id=run_id,
                actor_type="OPERATOR",
                expected=state.state_version,
            ))
            worker = FakeWorker(gateway)
            worker.preflight(run_id, reference["plan_id"], reference["plan_sha256"])
            worker.dispatch(
                run_id,
                reference["plan_id"],
                "LEFT_ALLEY_MIDDLE",
                repo.read_state(run_id).to_dict()["state_version"],
            )
            worker.capture_and_perceive(run_id, reference["plan_id"], reference["plan_sha256"])
            return

        assert state.task_status == "ASSIGNMENT_PROPOSED"
        gateway.submit(envelope(
            "OPERATOR_DECIDE",
            {"decision": "CONFIRM", "target": {"kind": "TASK", "task_id": state.task_id}},
            sequence=30,
            run_id=run_id,
            actor_type="OPERATOR",
            expected=state.state_version,
        ))

    try:
        result = drive_run(
            repo=repo,
            gateway=gateway,
            policy=policy,
            catalogue=CATALOGUE,
            run_id=run_id,
            agent_run_id=AGENT_RUN_ID,
            request_text="route-only survey of the alley stations",
            on_approval=approve,
            on_judgment=lambda pending: (_ for _ in ()).throw(
                AssertionError(f"architecture E unexpectedly asked for an operator choice: {pending}")
            ),
        )

        assert result["status"] == "COMPLETED"
        assert result["approvals"] == 2
        assert result["judgments"] == 0
        assert len(approval_states) == 2
        assert len(adapter.calls) == 1
        assert adapter.calls[0][1].legal.decision_point == SELECT_PLAN
        recommendation, route = sink.records[:2]
        assert recommendation["record"] == "proposal_recommendation"
        assert recommendation["label"] == ROUTE_PLAN
        assert route["record"] == "architecture_route"
        assert route["route"] == "proposer"
        proposal_event = next(
            event for event in repo.read_events(run_id, 0, 30)
            if event["event_type"] == "AGENT_INTENT_PROPOSED"
        )
        assert proposal_event["payload"] == {
            "intent": "REQUEST_OPERATOR_APPROVAL",
            "catalogue_plan_id": ROUTE_PLAN,
        }
    finally:
        repo.close()


def test_rejected_catalogue_plan_is_omitted_before_another_real_plan_is_confirmed(tmp_path):
    run = P3Run(tmp_path, "proposer-flow-rejection")
    try:
        run.propose_plan(ROUTE_PLAN)
        assert run.state().run_status == "WAITING_FOR_APPROVAL"

        run.decide_latest_plan("REJECT")
        state = run.state()
        legal = legal_set(state, CATALOGUE)

        assert state.run_status == "RUNNING"
        assert state.plan_id is None
        assert legal.decision_point == SELECT_PLAN
        assert legal.labels == (FULL_PLAN, "escalate")

        run.propose_plan(FULL_PLAN)
        run.decide_latest_plan("CONFIRM")

        confirmed = run.state()
        assert confirmed.plan_approved is True
        assert confirmed.proposed_catalogue_plan_ids == (ROUTE_PLAN, FULL_PLAN)
        assert len(confirmed.plan_station_ids) == 6
    finally:
        run.close()
