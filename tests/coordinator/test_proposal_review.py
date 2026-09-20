"""Read-only review view for architecture E's pending plan proposal."""
from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pytest

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.legal_tools import legal_set, load_catalogue_plans
from sentinel.coordinator.proposal_review import pending_proposal_review, submit_plan_review
from sentinel.coordinator.shadow import MemoryAuditSink

from .p3_helpers import P3Run


FULL_PLAN = "PLAN_MARKER_VERIFIED_6"
ROUTE_PLAN = "PLAN_ROUTE_ONLY_2_ZONE"
CATALOGUE = load_catalogue_plans()


def _pre_gateway_version(run: P3Run) -> int:
    proposal = next(
        event
        for event in reversed(run.repo.read_events(run.run_id, 0, 100))
        if event["event_type"] == "PLAN_PROPOSED"
        and event["payload"].get("plan_id") == run.state().plan_id
    )
    return proposal["state_version"] - 1


def _recommendation(run: P3Run, label: str, **overrides) -> dict:
    record = {
        "record": "proposal_recommendation",
        "run_id": run.run_id,
        "state_version": _pre_gateway_version(run),
        "decision_point": "SELECT_PLAN",
        "label": label,
        "reason": "narrowest plan covering the requested route",
        "facts": {
            "operator_request": "route-only survey of the alley stations",
            "candidate_description": "two route stations",
        },
    }
    record.update(overrides)
    return record


def test_pending_view_identifies_actual_plan_and_lists_other_plans_in_fixture_order(tmp_path):
    run = P3Run(tmp_path, "proposal-review-pending")
    try:
        run.propose_plan(ROUTE_PLAN)

        view = pending_proposal_review(run.repo, run.run_id, CATALOGUE, [])

        assert view == {
            "run_id": run.run_id,
            "proposed_plan": {
                "label": ROUTE_PLAN,
                "description": CATALOGUE[1].description,
                "recommendation": None,
            },
            "other_plans": [
                {"label": FULL_PLAN, "description": CATALOGUE[0].description},
            ],
        }
    finally:
        run.close()


def test_other_plans_keep_fixture_order_and_exclude_the_pending_plan(tmp_path):
    run = P3Run(tmp_path, "proposal-review-order")
    extended = [
        CATALOGUE[1],
        CATALOGUE[0],
        type(CATALOGUE[0])("PLAN_THIRD_TEST_ONLY", "third plan", ("THIRD",)),
    ]
    before = list(extended)
    try:
        run.propose_plan(FULL_PLAN)

        view = pending_proposal_review(run.repo, run.run_id, extended, [])

        assert [plan["label"] for plan in view["other_plans"]] == [
            ROUTE_PLAN,
            "PLAN_THIRD_TEST_ONLY",
        ]
        assert extended == before
    finally:
        run.close()


def test_exact_recommendation_displays_reason_together_with_its_cited_facts(tmp_path):
    run = P3Run(tmp_path, "proposal-review-recommendation")
    try:
        run.propose_plan(ROUTE_PLAN)
        record = _recommendation(run, ROUTE_PLAN)

        view = pending_proposal_review(run.repo, run.run_id, CATALOGUE, [record])

        assert view["proposed_plan"]["recommendation"] == {
            "reason": record["reason"],
            "facts": record["facts"],
        }
        assert "reason" not in view["proposed_plan"]
        assert "facts" not in view["proposed_plan"]
    finally:
        run.close()


def test_stale_different_plan_and_unrelated_run_recommendations_are_ignored(tmp_path):
    run = P3Run(tmp_path, "proposal-review-ignore-audit")
    try:
        run.propose_plan(ROUTE_PLAN)
        records = [
            _recommendation(run, ROUTE_PLAN, state_version=_pre_gateway_version(run) - 1),
            _recommendation(run, FULL_PLAN),
            _recommendation(run, ROUTE_PLAN, run_id="another-run"),
        ]

        view = pending_proposal_review(run.repo, run.run_id, CATALOGUE, records)

        assert view["proposed_plan"]["recommendation"] is None
    finally:
        run.close()


def test_latest_matching_pre_gateway_recommendation_wins(tmp_path):
    run = P3Run(tmp_path, "proposal-review-latest")
    try:
        run.propose_plan(ROUTE_PLAN)
        first = _recommendation(run, ROUTE_PLAN, reason="first")
        latest = _recommendation(run, ROUTE_PLAN, reason="latest")

        view = pending_proposal_review(run.repo, run.run_id, CATALOGUE, [first, latest])

        assert view["proposed_plan"]["recommendation"]["reason"] == "latest"
    finally:
        run.close()


def test_pre_gateway_audit_for_gateway_rejected_proposal_cannot_surface(tmp_path):
    run = P3Run(tmp_path, "proposal-review-gateway-rejected")
    try:
        run.propose_plan(ROUTE_PLAN)
        # A proposer can write before submission; if submission is rejected
        # while this plan is already pending, that later record is not the
        # recommendation that caused the persisted pending transition.
        rejected_record = _recommendation(
            run,
            ROUTE_PLAN,
            state_version=run.state().state_version,
            reason="written before a rejected gateway submission",
        )

        view = pending_proposal_review(run.repo, run.run_id, CATALOGUE, [rejected_record])

        assert view["proposed_plan"]["recommendation"] is None
    finally:
        run.close()


def test_recommendation_for_rejected_nonpending_proposal_cannot_surface(tmp_path):
    run = P3Run(tmp_path, "proposal-review-rejected")
    try:
        run.propose_plan(ROUTE_PLAN)
        rejected_record = _recommendation(run, ROUTE_PLAN)
        run.decide_latest_plan("REJECT")
        run.propose_plan(FULL_PLAN)

        view = pending_proposal_review(run.repo, run.run_id, CATALOGUE, [rejected_record])

        assert view["proposed_plan"]["label"] == FULL_PLAN
        assert view["proposed_plan"]["recommendation"] is None
    finally:
        run.close()


def test_returns_none_outside_plan_approval_wait(tmp_path):
    running = P3Run(tmp_path, "proposal-review-running")
    task_wait = P3Run(tmp_path, "proposal-review-task")
    try:
        assert pending_proposal_review(running.repo, running.run_id, CATALOGUE, []) is None

        task_wait.repo.set_pending_approval(task_wait.run_id, "TASK", "task-id")
        assert pending_proposal_review(task_wait.repo, task_wait.run_id, CATALOGUE, []) is None
    finally:
        running.close()
        task_wait.close()


def test_api_has_no_analysis_expansion_and_review_is_read_only(tmp_path):
    run = P3Run(tmp_path, "proposal-review-pure")
    catalogue = list(CATALOGUE)
    try:
        run.propose_plan(ROUTE_PLAN)
        before_catalogue = list(catalogue)
        before_changes = run.repo.connection.total_changes

        view = pending_proposal_review(run.repo, run.run_id, catalogue, [])

        assert set(inspect.signature(pending_proposal_review).parameters) == {
            "repository", "run_id", "catalogue", "audit_records",
        }
        assert "show_analysis" not in repr(view)
        assert "analysis_expanded" not in repr(view)
        assert catalogue == before_catalogue
        assert run.repo.connection.total_changes == before_changes
    finally:
        run.close()


FIXED_REVIEW_TIME = datetime(2026, 9, 20, 4, 5, 6, tzinfo=timezone.utc)


def test_submit_plan_review_builds_operator_command_and_exact_audit(tmp_path):
    run = P3Run(tmp_path, "proposal-review-command")
    sink = MemoryAuditSink()
    submitted = []

    class GatewaySpy:
        def submit(self, command):
            submitted.append(command)
            return {"status": "COMPLETED"}

    try:
        run.propose_plan(ROUTE_PLAN)
        state = run.state()
        proposal = next(
            event for event in reversed(run.repo.read_events(run.run_id, 0, 100))
            if event["event_type"] == "PLAN_PROPOSED"
        )
        proposed_at = datetime.fromisoformat(proposal["recorded_at"].replace("Z", "+00:00"))

        result = submit_plan_review(
            run.repo, GatewaySpy(), run.run_id, "CONFIRM", sink=sink,
            clock=lambda: FIXED_REVIEW_TIME,
        )

        assert result == {"status": "COMPLETED"}
        assert len(submitted) == 1
        command = submitted[0]
        assert command["command_type"] == "OPERATOR_DECIDE"
        assert command["actor_type"] == "OPERATOR"
        assert command["actor_id"] == "OPERATOR_1"
        assert command["expected_state_version"] == state.state_version
        assert command["payload"]["decision"] == "CONFIRM"
        assert command["payload"]["target"]["kind"] == "PLAN"
        reference = command["payload"]["target"]["plan_reference"]
        assert reference["plan_id"] == state.plan_id
        assert tuple(reference["station_ids"]) == state.plan_station_ids
        assert reference["catalogue_id"] == "SURVEY_CATALOGUE_V1"
        assert reference["catalogue_revision"] == "REV_1"
        assert len(reference["plan_sha256"]) == 64
        assert sink.records == [{
            "record": "plan_review",
            "run_id": run.run_id,
            "state_version": state.state_version,
            "decision": "CONFIRM",
            "actor_id": "OPERATOR_1",
            "proposed_label": ROUTE_PLAN,
            "seconds_to_decision": max(0.0, (FIXED_REVIEW_TIME - proposed_at).total_seconds()),
        }]
    finally:
        run.close()


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_point"),
    [("CONFIRM", "WAITING_FOR_OBSERVATION", "WAIT_OBSERVATION"),
     ("REJECT", "RUNNING", "SELECT_PLAN")],
)
def test_submit_plan_review_confirm_and_reject_through_real_gateway(
    tmp_path, decision, expected_status, expected_point
):
    run = P3Run(tmp_path, f"proposal-review-real-{decision.lower()}")
    sink = MemoryAuditSink()
    try:
        run.propose_plan(ROUTE_PLAN)

        submit_plan_review(
            run.repo, CommandGateway(run.repo), run.run_id, decision,
            sink=sink, clock=lambda: FIXED_REVIEW_TIME,
        )

        state = run.state()
        assert state.run_status == expected_status
        assert legal_set(state, CATALOGUE).decision_point == expected_point
        assert sink.records[0]["decision"] == decision
    finally:
        run.close()


def test_submit_plan_review_clamps_negative_duration_to_zero(tmp_path):
    run = P3Run(tmp_path, "proposal-review-duration")
    sink = MemoryAuditSink()
    try:
        run.propose_plan(ROUTE_PLAN)
        submit_plan_review(
            run.repo, run.gateway, run.run_id, "REJECT", sink=sink,
            clock=lambda: datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        assert sink.records == [{
            **sink.records[0],
            "seconds_to_decision": 0.0,
        }]
    finally:
        run.close()


def test_submit_plan_review_rejects_invalid_or_nonpending_review(tmp_path):
    run = P3Run(tmp_path, "proposal-review-invalid")
    sink = MemoryAuditSink()
    try:
        with pytest.raises(CoordinatorError):
            submit_plan_review(run.repo, run.gateway, run.run_id, "MAYBE", sink=sink)
        with pytest.raises(CoordinatorError):
            submit_plan_review(run.repo, run.gateway, run.run_id, "CONFIRM", sink=sink)
        assert sink.records == []
    finally:
        run.close()


def test_submit_plan_review_rejects_missing_persisted_plan(tmp_path):
    run = P3Run(tmp_path, "proposal-review-missing-plan")
    sink = MemoryAuditSink()
    try:
        run.propose_plan(ROUTE_PLAN)
        run.repo.connection.execute(
            "UPDATE runs SET pending_approval_id = ? WHERE run_id = ?",
            ("missing-plan-id", run.run_id),
        )
        run.repo.connection.commit()
        with pytest.raises(CoordinatorError):
            submit_plan_review(run.repo, run.gateway, run.run_id, "CONFIRM", sink=sink)
        assert sink.records == []
    finally:
        run.close()


def test_submit_plan_review_does_not_audit_failed_gateway_submission(tmp_path):
    run = P3Run(tmp_path, "proposal-review-gateway-failure")
    sink = MemoryAuditSink()

    class FailingGateway:
        def submit(self, command):
            raise CoordinatorError("TEST_GATEWAY_FAILURE", "boom")

    try:
        run.propose_plan(ROUTE_PLAN)
        with pytest.raises(CoordinatorError, match="boom"):
            submit_plan_review(run.repo, FailingGateway(), run.run_id, "REJECT", sink=sink)
        assert sink.records == []
    finally:
        run.close()
