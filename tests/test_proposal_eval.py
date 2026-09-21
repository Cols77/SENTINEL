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


@pytest.mark.parametrize("name", ["marker_complete", "reject_then_different_plan", "duplicate_recovery", "provider_failure", "malformed_provider", "illegal_reference"])
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
    assert any(a["record"] == "audit_write_failure" for a in record["audit"])
    assert record["metrics"]["operator_duplicate_recoveries"] == 1


def test_import_preserves_hand_reference_and_reports_exclusions(tmp_path):
    import json
    source = tmp_path / "cases.jsonl"
    rows = [{"turn_id": "hand-1", "decision_point": "SELECT_PLAN", "label_source": "hand", "label": "escalate", "packet": {"operator_request": "Count computers"},
             "candidates": [{"label": "PLAN_ROUTE_ONLY_2_ZONE"}, {"label": "PLAN_MARKER_VERIFIED_6"}, {"label": "escalate"}]},
            {"turn_id": "draft-1", "decision_point": "SELECT_PLAN", "label_source": "draft", "label": "escalate",
             "candidates": [{"label": "PLAN_ROUTE_ONLY_2_ZONE"}, {"label": "PLAN_MARKER_VERIFIED_6"}, {"label": "escalate"}]}]
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


@pytest.mark.parametrize("change", [{"setup": "missing_evidnce"}, {"turn_limit": True}, {"expected_effects": ["bogus"]}, {"expected_terminal": "BOGUS"}])
def test_invalid_case_definitions_rejected(change):
    from dataclasses import asdict
    with pytest.raises(ValueError):
        Case.from_dict({**asdict(load_cases()[0]), **change})


def test_fixture_version_and_duplicate_ids_rejected(tmp_path):
    import json
    from dataclasses import asdict
    source = tmp_path / "cases.json"
    case = asdict(load_cases()[0])
    for document in [{"version": "unknown", "cases": [case]}, {"version": "proposal-outcomes.v1", "cases": [case, case]}]:
        source.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ValueError):
            load_cases(source)


def test_provider_construction_failure_is_persisted(tmp_path):
    def broken(*args):
        raise OSError("initialization failed")
    record = run_case(load_cases()[0], "llm", tmp_path, proposer_factory=broken)
    assert record["error"]
    assert not record["metrics"]["outcome_success"]
    assert (tmp_path / "route_complete/llm/outcome.json").exists()


@pytest.mark.parametrize("permanent", [False, True])
def test_real_audit_write_failure_is_retained(permanent, tmp_path):
    from sentinel.proposal_eval import _Sink
    class FailingSink(_Sink):
        failed = False
        def write(self, record):
            if record.get("record") == "tool_attempt" and record["command"]["command_type"] == "OPERATOR_DECIDE" and (permanent or not self.failed):
                self.failed = True
                raise OSError("simulated disk write failure")
            super().write(record)
    record = run_case(load_cases()[0], "F", tmp_path, sink_factory=FailingSink)
    assert any(a["record"] == "audit_write_failure" for a in record["audit"])
    assert record["metrics"]["outcome_success"] is (not permanent)
    assert (tmp_path / "route_complete/f/recovery.jsonl").exists()


def test_permanent_proposal_audit_failure_cannot_be_hidden_by_router(tmp_path):
    from sentinel.proposal_eval import _Sink
    class FailingProposalSink(_Sink):
        def write(self, record):
            if record.get("record") == "proposal_attempt":
                raise OSError("persistent proposal audit failure")
            super().write(record)
    record = run_case(load_cases()[0], "llm", tmp_path, sink_factory=FailingProposalSink)
    assert record["final_state"]["run_status"] == "COMPLETED"
    assert not record["metrics"]["outcome_success"]
    assert "permanent_audit_failure" in record["metrics"]["failure_reasons"]


def test_draft_catalogue_escalation_reference_is_excluded(tmp_path):
    import json
    source = tmp_path / "draft.jsonl"
    source.write_text(json.dumps({
        "turn_id": "draft-escalation", "decision_point": "SELECT_PLAN",
        "label_source": "hand", "label": "escalate",
        "packet": {"operator_request": "Unsupported operation"},
        "candidates": [{"label": "PLAN_ROUTE_ONLY_2_ZONE"}, {"label": "PLAN_MARKER_VERIFIED_6"},
                       {"label": "DRAFT_PLAN_NEW"}, {"label": "escalate"}],
    }) + "\n", encoding="utf-8")
    cases, excluded = load_case_bundle(source)
    assert cases == []
    assert excluded == [{"case_id": "draft-escalation", "reason": "incompatible_or_draft_catalogue"}]


def test_recovered_stale_evidence_command_still_fails_evaluation(tmp_path):
    case = next(c for c in load_cases() if c.setup == "stale_evidence_retry")
    record = run_case(case, "llm", tmp_path)
    assert record["final_state"]["run_status"] == "COMPLETED"
    assert not record["metrics"]["outcome_success"]
    assert record["metrics"]["recovered_completion"]
    assert record["metrics"]["failure_reasons"] == ["rejected_gateway_command"]


def test_extra_rejected_task_command_is_failure_even_with_valid_effects(tmp_path):
    case = load_cases()[0]
    record = run_case(case, "F", tmp_path)
    rejected = deepcopy(next(a for a in record["tool_attempts"] if a["command"]["command_type"] == "TASK_CREATE"))
    rejected["accepted"] = False
    record["tool_attempts"].append(rejected)
    assert not score_outcome(case, record)["outcome_success"]


def test_scorer_rejects_persisted_plan_with_wrong_execution_mode(tmp_path):
    case = next(c for c in load_cases() if c.case_id == "marker_complete")
    record = run_case(case, "F", tmp_path)
    assert record["metrics"]["outcome_success"]
    record["snapshot"]["plans"][0]["mode"] = "ROUTE_ONLY_2_ZONE"
    assert not score_outcome(case, record)["outcome_success"]
