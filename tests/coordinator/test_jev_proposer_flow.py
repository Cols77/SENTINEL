"""Real AgentLoop/gateway lifecycle for a Jev-selected plan."""
from __future__ import annotations

from sentinel.coordinator import AgentLoop, JevProposer
from sentinel.coordinator.boundary import CoordinatorBoundary
from sentinel.coordinator.legal_tools import load_catalogue_plans
from sentinel.coordinator.policy import bind_context
from sentinel.coordinator.proposal_review import pending_proposal_review
from sentinel.coordinator.shadow import MemoryAuditSink

from .harness import AGENT_RUN_ID, envelope, open_harness


ROUTE = "PLAN_ROUTE_ONLY_2_ZONE"


class ScriptedJudge:
    def choice(self, state, instruction, *, criteria, name):
        return {
            "value": ROUTE,
            "confidence": 0.91,
            "probabilities": {ROUTE: 0.8, "PLAN_MARKER_VERIFIED_6": 0.2},
        }


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
