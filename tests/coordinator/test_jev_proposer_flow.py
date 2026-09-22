"""Real AgentLoop/gateway lifecycle for a Jev-selected plan."""
from __future__ import annotations

from sentinel.coordinator import AgentLoop, JevProposer
from sentinel.coordinator.architecture import ArchitectureRouter
from sentinel.coordinator.boundary import CoordinatorBoundary
from sentinel.coordinator.legal_tools import ESCALATE_LABEL, load_catalogue_plans
from sentinel.coordinator.policy import PolicyContext, bind_context
from sentinel.coordinator.proposal_review import pending_proposal_review
from sentinel.coordinator.shadow import MemoryAuditSink

from .harness import AGENT_RUN_ID, envelope, open_harness


ROUTE = "PLAN_ROUTE_ONLY_2_ZONE"


class ScriptedJudge:
    def choice(self, state, instruction, *, criteria, name):
        return {
            "value": ROUTE,
            "confidence": 0.91,
            "probabilities": {
                ROUTE: 0.8,
                "PLAN_MARKER_VERIFIED_6": 0.15,
                ESCALATE_LABEL: 0.05,
            },
        }


class EscalationJudge:
    def choice(self, state, instruction, *, criteria, name):
        return {
            "value": ESCALATE_LABEL,
            "confidence": 0.9,
            "probabilities": {
                label: 0.9 if label == ESCALATE_LABEL else 0.05
                for label in criteria
            },
        }


class CountingFallback:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, request: dict, context: PolicyContext) -> dict:
        self.calls += 1
        raise AssertionError("architecture F must not handle explicit proposer escalation")


def test_real_loop_persists_jev_proposal_then_stops_for_operator_approval(tmp_path) -> None:
    repo, gateway = open_harness(tmp_path)
    catalogue = load_catalogue_plans()
    sink = MemoryAuditSink()
    run_id = "33333333-3333-4333-8333-333333333333"
    try:
        gateway.submit(envelope("RUN_CREATE", {"request_text": "route-only alley survey"},
                                sequence=1, run_id=run_id, actor_type="OPERATOR"))
        policy = bind_context(JevProposer(ScriptedJudge(), sink), repo, catalogue)

        result = AgentLoop(
            CoordinatorBoundary(repo, gateway), policy, repository=repo, catalogue=catalogue,
        ).run(run_id, AGENT_RUN_ID, "route-only alley survey")

        assert result["error_code"] == "WAITING_FOR_APPROVAL"
        state = repo.read_state(run_id).to_dict()
        assert state["run_status"] == "WAITING_FOR_APPROVAL"
        persisted = repo.connection.execute(
            "SELECT pending_approval_kind FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert persisted["pending_approval_kind"] == "PLAN"
        assert repo.connection.execute(
            "SELECT COUNT(*) FROM plan_approvals WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 0
        assert repo.connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == 0

        view = pending_proposal_review(repo, run_id, catalogue, sink.records)
        assert view["proposed_plan"]["label"] == ROUTE
        assert view["proposed_plan"]["recommendation"] == {
            "reason": f"SELECT_PLAN: {catalogue[1].description}",
            "facts": {
                "operator_request": "route-only alley survey",
                "candidate_description": catalogue[1].description,
            },
        }
    finally:
        repo.close()


def test_real_e_loop_returns_confident_jev_escalation_without_persisting_a_proposal(tmp_path) -> None:
    repo, gateway = open_harness(tmp_path)
    catalogue = load_catalogue_plans()
    sink = MemoryAuditSink()
    fallback = CountingFallback()
    run_id = "44444444-4444-4444-8444-444444444444"
    try:
        gateway.submit(envelope(
            "RUN_CREATE", {"request_text": "unsupported request"}, sequence=1,
            run_id=run_id, actor_type="OPERATOR",
        ))
        events_before = repo.read_events(run_id, 0, 30)
        router = ArchitectureRouter(fallback, JevProposer(EscalationJudge(), sink), sink)

        result = AgentLoop(
            CoordinatorBoundary(repo, gateway),
            bind_context(router, repo, catalogue),
            repository=repo,
            catalogue=catalogue,
        ).run(run_id, AGENT_RUN_ID, "unsupported request")

        assert result == {
            "status": "NEEDS_REVIEW",
            "summary": "plan proposal needs operator review",
        }
        assert repo.read_events(run_id, 0, 30) == events_before
        assert repo.read_state(run_id).to_dict()["run_status"] == "RUNNING"
        for table in ("plans", "plan_approvals", "tasks"):
            count = repo.connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            assert count == 0
        attempts = [record for record in sink.records if record["record"] == "proposal_attempt"]
        assert len(attempts) == 1
        assert attempts[0]["gate_outcome"] == "accepted"
        assert attempts[0]["raw_choice"] == ESCALATE_LABEL
        assert attempts[0]["policy_label"] == ESCALATE_LABEL
        assert not [record for record in sink.records if record["record"] == "proposal_recommendation"]
        routes = [record for record in sink.records if record["record"] == "architecture_route"]
        assert len(routes) == 1
        assert routes[0]["route"] == "proposer_escalation"
        assert routes[0]["label"] == ESCALATE_LABEL
        assert fallback.calls == 0
    finally:
        repo.close()
