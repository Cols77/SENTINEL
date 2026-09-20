"""Persisted outcomes, never tool-name presence, establish success."""
from copy import deepcopy
import pytest
from sentinel.proposal_eval import Case, load_cases, load_case_bundle, run_case, score_outcome, aggregate


@pytest.mark.parametrize("arm", ["F", "llm", "jev"])
def test_real_complete_path(arm, tmp_path):
    case = load_cases()[0]
    record = run_case(case, arm, tmp_path)
    assert record["metrics"]["outcome_success"], record["metrics"]
    assert record["snapshot"]["tasks"][0]["status"] == "ASSIGNED"
    assert record["metrics"]["proposal_calls"] == (0 if arm == "F" else 1)


@pytest.mark.parametrize("arm", ["llm", "jev"])
def test_pending_approval_zero_model_calls(arm, tmp_path):
    case = next(c for c in load_cases() if c.setup == "pending_approval")
    record = run_case(case, arm, tmp_path)
    assert record["metrics"]["outcome_success"]
    assert record["metrics"]["proposal_calls"] == 0
    assert record["requests"] == []


@pytest.mark.parametrize("mutation", ["empty", "wrong_plan", "rejected_task", "wrong_assignment", "missing_event", "missing_report", "timeout", "plan_hash", "plan_revision"])
def test_scorer_rejects_false_success(mutation, tmp_path):
    case = load_cases()[0]
    record = deepcopy(run_case(case, "F", tmp_path))
    if mutation == "empty":
        record["snapshot"] = {}
    elif mutation == "wrong_plan":
        record["snapshot"]["plans"][0]["station_ids_json"] = '["WRONG_STATION"]'
    elif mutation == "rejected_task":
        for attempt in record["tool_attempts"]:
            if attempt["command"]["command_type"] == "TASK_CREATE":
                attempt["accepted"] = False
    elif mutation == "wrong_assignment":
        record["snapshot"]["tasks"][0]["proposed_patroller_id"] = "PATROLLER_2"
    elif mutation == "missing_event":
        record["events"] = [e for e in record["events"] if e["event_type"] != "PLAN_APPROVED"]
    elif mutation == "missing_report":
        record["report_complete"] = False
    elif mutation == "plan_hash":
        record["snapshot"]["plans"][0]["plan_sha256"] = "0" * 64
    elif mutation == "plan_revision":
        record["snapshot"]["plans"][0]["catalogue_revision"] = "OTHER"
    else:
        record["error"] = "TURN_BUDGET_EXHAUSTED"
    assert not score_outcome(case, record)["outcome_success"]


def test_unsupported_confident_proposal_is_failure(tmp_path):
    case = next(c for c in load_cases() if c.case_id == "unsupported_computer_count")
    record = run_case(case, "jev", tmp_path)
    assert not record["metrics"]["outcome_success"]
    assert record["metrics"]["false_proposals"] == 1
    assert not record["metrics"]["proposal_quality_success"]


@pytest.mark.parametrize("name", ["marker_complete", "reject_then_different_plan", "duplicate_recovery", "provider_failure", "malformed_provider", "illegal_reference", "stale_evidence_retry"])
def test_recovery_paths(name, tmp_path):
    case = next(c for c in load_cases() if c.case_id == name)
    record = run_case(case, "llm", tmp_path)
    assert record["metrics"]["outcome_success"], record["metrics"]


def test_missing_evidence_is_not_completion(tmp_path):
    case = next(c for c in load_cases() if c.setup == "missing_evidence")
    record = run_case(case, "F", tmp_path)
    assert not record["metrics"]["outcome_success"]
    assert not record["snapshot"]["tasks"]


def test_aggregate_denominators_are_explicit():
    assert aggregate([])["proposal_case_denominator"] == 0
    assert aggregate([])["outcome_success_rate"] is None


def test_audit_interruption_reopens_durable_state(tmp_path):
    case = next(c for c in load_cases() if c.setup == "audit_interruption")
    record = run_case(case, "jev", tmp_path)
    assert record["metrics"]["outcome_success"], record["metrics"]
    assert [a["record"] for a in record["audit"] if a["record"] in {"interruption", "recovery"}] == ["interruption", "recovery"]
    assert record["metrics"]["proposal_calls"] == 1


def test_import_preserves_hand_reference_and_reports_exclusions(tmp_path):
    import json
    source = tmp_path / "cases.jsonl"
    rows = [{"turn_id": "hand-1", "decision_point": "SELECT_PLAN", "label_source": "hand", "label": "escalate", "packet": {"operator_request": "Count computers"}},
            {"turn_id": "draft-1", "decision_point": "SELECT_PLAN", "label_source": "draft", "label": "escalate"}]
    source.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    cases, excluded = load_case_bundle(source)
    assert cases[0].expected_escalation
    assert cases[0].request == "Count computers"
    assert excluded == [{"case_id": "draft-1", "reason": "draft_or_unlabelled_reference"}]


def test_ordered_events_must_match_accepted_targets(tmp_path):
    case = load_cases()[0]
    successful = run_case(case, "F", tmp_path)
    for event_type, mutate in [
        ("TASK_CREATED", lambda p: p.update(perception_result_id="WRONG")),
        ("TASK_CREATED", lambda p: p.update(evidence_artifact_id="WRONG")),
        ("PLAN_APPROVED", lambda p: p["target"]["plan_reference"].update(plan_id="WRONG")),
        ("PATROLLER_ASSIGNMENT_CONFIRMED", lambda p: p["target"].update(task_id="WRONG")),
    ]:
        record = deepcopy(successful)
        event = next(e for e in record["events"] if e["event_type"] == event_type)
        mutate(event["payload"])
        assert not score_outcome(case, record)["outcome_success"], event_type


def test_operator_metrics_distinguish_unique_confirmations(tmp_path):
    case = load_cases()[0]
    record = run_case(case, "F", tmp_path)
    approval = next(a for a in record["tool_attempts"] if a["command"]["command_type"] == "OPERATOR_DECIDE")
    record["tool_attempts"].append(deepcopy(approval))
    rejection = deepcopy(approval)
    rejection["command"]["command_id"] = "different-command"
    rejection["command"]["payload"]["decision"] = "REJECT"
    record["tool_attempts"].append(rejection)
    metrics = score_outcome(case, record)
    assert metrics["operator_approvals"] == 2
    assert metrics["operator_rejections"] == 1
    assert metrics["operator_duplicate_recoveries"] == 1
    assert metrics["operator_decision_attempts"] == 4
