"""Persisted outcomes, never tool-name presence, establish success."""
from copy import deepcopy
import pytest
from sentinel.proposal_eval import Case, load_cases, load_case_bundle, run_case, score_outcome, aggregate


def _terminal_record(case, *audit):
    return {
        "case_id": case.case_id,
        "arm": "llm",
        "case": case,
        "audit": list(audit),
        "snapshot": {"runs": [{}], "plans": [], "tasks": [], "plan_approvals": []},
        "events": [{"event_type": "RUN_CREATED", "payload": {}}],
        "requests": [{}],
        "tool_attempts": [],
        "loop_results": [{"status": "NEEDS_REVIEW"}],
        "final_state": {"run_status": "RUNNING"},
        "report_complete": True,
        "error": None,
    }


def _proposal_attempt(gate_outcome, raw_choice=None, policy_label="escalate"):
    return {
        "record": "proposal_attempt",
        "gate_outcome": gate_outcome,
        "raw_choice": raw_choice,
        "policy_label": policy_label,
    }


def _route(route):
    return {"record": "architecture_route", "route": route}


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


@pytest.mark.parametrize("name", ["marker_complete", "reject_then_different_plan", "duplicate_recovery"])
def test_successful_recovery_paths(name, tmp_path):
    case = next(c for c in load_cases() if c.case_id == name)
    record = run_case(case, "llm", tmp_path)
    assert record["metrics"]["outcome_success"], record["metrics"]
    assert record["metrics"]["proposal_quality_success"], record["metrics"]


@pytest.mark.parametrize(
    ("name", "counter"),
    [
        ("provider_failure", "provider_failures"),
        ("malformed_provider", "deterministic_refusals"),
        ("illegal_reference", "deterministic_refusals"),
    ],
)
def test_safe_provider_failures_are_negative_supported_quality(name, counter, tmp_path):
    case = next(c for c in load_cases() if c.case_id == name)
    record = run_case(case, "llm", tmp_path)
    metrics = record["metrics"]
    assert not metrics["outcome_success"], metrics
    assert metrics["safe_escalation_trial"], metrics
    assert metrics["proposal_quality_success"] is False
    assert metrics["escalation_success"] is None
    assert metrics[counter] == 1
    assert metrics["accepted_proposals"] == 0
    assert metrics["correct_proposals"] == 0


def test_missing_evidence_is_not_completion(tmp_path):
    case = next(c for c in load_cases() if c.setup == "missing_evidence")
    record = run_case(case, "F", tmp_path)
    assert not record["metrics"]["outcome_success"]
    assert not record["snapshot"]["tasks"]


def test_aggregate_denominators_are_explicit():
    empty = aggregate([])
    assert empty["proposal_case_denominator"] == 0
    assert empty["plan_attempt_denominator"] == 0
    assert empty["escalation_case_denominator"] == 0
    assert empty["outcome_success_rate"] is None
    assert empty["proposal_accuracy"] is None
    assert empty["proposal_coverage"] is None
    assert empty["raw_semantic_accuracy"] is None
    assert empty["raw_semantic_choice_coverage"] is None


def test_raw_semantic_quality_is_separate_from_confidence_and_outcome_gates():
    supported = next(c for c in load_cases() if c.case_id == "provider_failure")
    unsupported = next(c for c in load_cases() if c.case_id == "ambiguous")
    supported_record = _terminal_record(
        supported,
        _proposal_attempt("low_confidence", "PLAN_ROUTE_ONLY_2_ZONE"),
        _route("proposer_escalation"),
    )
    unsupported_record = _terminal_record(
        unsupported,
        _proposal_attempt("accepted", "escalate", "escalate"),
        _route("proposer_escalation"),
    )

    totals = aggregate([supported_record, unsupported_record])

    assert totals["raw_semantic_correct_choices"] == 2
    assert totals["raw_semantic_choice_denominator"] == 2
    assert totals["raw_semantic_accuracy"] == 1.0
    assert totals["raw_semantic_choice_coverage_numerator"] == 2
    assert totals["raw_semantic_choice_coverage_denominator"] == 2
    assert totals["raw_semantic_choice_coverage"] == 1.0
    assert totals["confidence_filtered_correct_choices"] == 1
    assert totals["confidence_filtered_correct_choice_denominator"] == 2
    assert totals["proposal_coverage_numerator"] == 0
    assert totals["proposal_coverage_denominator"] == 1
    assert totals["outcome_successes"] == 1
    assert totals["case_denominator"] == 2


def test_complete_expected_escalation_scores_deliberate_choice():
    case = next(c for c in load_cases() if c.case_id == "ambiguous")
    record = _terminal_record(
        case,
        _proposal_attempt("accepted", "escalate", "escalate"),
        _route("proposer_escalation"),
    )

    metrics = score_outcome(case, record)

    assert metrics["outcome_success"] is True
    assert metrics["proposal_quality_success"] is None
    assert metrics["escalation_success"] is True
    assert metrics["explicit_escalations"] == 1
    assert metrics["confidence_gate_escalations"] == 0
    assert metrics["architecture_f_fallbacks"] == 0
    assert metrics["false_proposals"] == 0
    assert metrics["accepted_proposals"] == 0
    assert metrics["proposal_calls"] == 1
    assert metrics["plan_attempt_denominator"] == 0


@pytest.mark.parametrize(
    "mutation",
    ["missing_report", "missing_events", "wrong_terminal", "missing_route"],
)
def test_explicit_escalation_requires_complete_persisted_route_evidence(mutation):
    case = next(c for c in load_cases() if c.case_id == "ambiguous")
    record = _terminal_record(
        case,
        _proposal_attempt("accepted", "escalate", "escalate"),
        _route("proposer_escalation"),
    )
    if mutation == "missing_report":
        record["report_complete"] = False
    elif mutation == "missing_events":
        record["events"] = []
    elif mutation == "wrong_terminal":
        record["final_state"]["run_status"] = "COMPLETED"
    else:
        record["audit"] = [
            item for item in record["audit"]
            if item.get("record") != "architecture_route"
        ]

    metrics = score_outcome(case, record)

    assert metrics["safe_escalation_trial"] is False
    assert metrics["escalation_success"] is False
    if mutation != "missing_route":
        assert metrics["outcome_success"] is False


@pytest.mark.parametrize(
    ("audit", "expected"),
    [
        ((_proposal_attempt("low_confidence", "escalate"), _route("proposer_escalation")), {"confidence_gate_escalations": 1}),
        ((_proposal_attempt("low_margin", "escalate"), _route("proposer_escalation")), {"confidence_gate_escalations": 1}),
        ((_proposal_attempt("invalid_answer", "escalate"), _route("proposer_escalation")), {"deterministic_refusals": 1}),
        ((_proposal_attempt("provider_error"), _route("proposer_escalation")), {"provider_failures": 1}),
        ((_proposal_attempt("accepted", "PLAN_ROUTE_ONLY_2_ZONE", "PLAN_ROUTE_ONLY_2_ZONE"), _route("fallback_F")), {"architecture_f_fallbacks": 1}),
    ],
)
def test_accepted_escalation_and_failure_counters_are_disjoint(audit, expected):
    case = next(c for c in load_cases() if c.case_id == "ambiguous")
    metrics = score_outcome(case, _terminal_record(case, *audit))
    counters = {
        key: metrics[key]
        for key in (
            "explicit_escalations",
            "confidence_gate_escalations",
            "deterministic_refusals",
            "provider_failures",
            "architecture_f_fallbacks",
        )
    }
    assert counters == {
        "explicit_escalations": 0,
        "confidence_gate_escalations": 0,
        "deterministic_refusals": 0,
        "provider_failures": 0,
        "architecture_f_fallbacks": 0,
        **expected,
    }
    assert metrics["escalation_success"] is False
    assert metrics["proposal_quality_success"] is None


def test_fallback_f_cannot_earn_plan_or_escalation_credit():
    case = next(c for c in load_cases() if c.case_id == "route_complete")
    record = _terminal_record(
        case,
        _proposal_attempt("provider_error"),
        _route("fallback_F"),
    )
    record["final_state"]["run_status"] = "NEEDS_REVIEW"

    metrics = score_outcome(case, record)

    assert metrics["safe_escalation_trial"] is False
    assert metrics["proposal_quality_success"] is False
    assert metrics["escalation_success"] is None
    assert metrics["accepted_proposals"] == 0
    assert metrics["correct_proposals"] == 0


def test_aggregate_uses_applicable_case_and_attempt_denominators():
    escalation_case = next(c for c in load_cases() if c.case_id == "ambiguous")
    supported_case = next(c for c in load_cases() if c.case_id == "provider_failure")
    escalation = _terminal_record(
        escalation_case,
        _proposal_attempt("accepted", "escalate", "escalate"),
        _route("proposer_escalation"),
    )
    supported_failure = _terminal_record(
        supported_case,
        _proposal_attempt("provider_error"),
        _route("proposer_escalation"),
    )
    supported_failure["final_state"]["run_status"] = "NEEDS_REVIEW"

    totals = aggregate([escalation, supported_failure])

    assert totals["case_denominator"] == 2
    assert totals["proposal_case_denominator"] == 1
    assert totals["escalation_case_denominator"] == 1
    assert totals["proposal_call_denominator"] == 2
    assert totals["plan_attempt_denominator"] == 0
    assert totals["proposal_coverage_denominator"] == 1
    assert totals["accepted_proposal_denominator"] == 0
    assert totals["explicit_escalations"] == 1
    assert totals["provider_failures"] == 1
    assert totals["proposal_quality_successes"] == 0
    assert totals["escalation_successes"] == 1


def test_false_plan_on_escalation_case_enters_plan_denominators(tmp_path):
    supported = next(c for c in load_cases() if c.case_id == "route_complete")
    record = run_case(supported, "llm", tmp_path)
    escalation_case = Case(
        case_id=supported.case_id,
        request=supported.request,
        expected_escalation=True,
        expected_terminal="COMPLETED",
        expected_effects=(),
    )
    record["case"] = escalation_case
    record.pop("metrics")

    metrics = score_outcome(escalation_case, record)
    totals = aggregate([record])

    assert metrics["proposal_quality_success"] is None
    assert metrics["false_proposals"] == 1
    assert metrics["accepted_proposals"] == 1
    assert metrics["proposal_denominator"] == 1
    assert metrics["plan_attempt_denominator"] == 1
    assert totals["proposal_case_denominator"] == 1
    assert totals["accepted_proposal_denominator"] == 1
    assert totals["proposal_call_denominator"] == 1
    assert totals["plan_attempt_denominator"] == 1
    assert totals["proposal_coverage_denominator"] == 0
    assert totals["proposal_coverage"] is None


def test_supported_explicit_escalation_is_not_a_plan_denominator():
    case = next(c for c in load_cases() if c.case_id == "provider_failure")
    record = _terminal_record(
        case,
        _proposal_attempt("accepted", "escalate", "escalate"),
        _route("proposer_escalation"),
    )

    metrics = score_outcome(case, record)
    totals = aggregate([record])

    assert metrics["proposal_quality_success"] is False
    assert metrics["explicit_escalations"] == 1
    assert metrics["accepted_proposals"] == 0
    assert metrics["proposal_calls"] == 1
    assert metrics["proposal_denominator"] == 1
    assert metrics["plan_attempt_denominator"] == 0
    assert totals["proposal_case_denominator"] == 1
    assert totals["accepted_proposal_denominator"] == 0
    assert totals["proposal_call_denominator"] == 1
    assert totals["plan_attempt_denominator"] == 0
    assert totals["proposal_coverage_denominator"] == 1
    assert totals["proposal_accuracy"] is None
    assert totals["proposal_coverage"] == 0.0


def test_low_confidence_raw_escalation_is_counted_as_escalation_not_plan_attempt():
    case = next(c for c in load_cases() if c.case_id == "provider_failure")
    record = _terminal_record(
        case,
        _proposal_attempt("low_confidence", "escalate", "escalate"),
        _route("proposer_escalation"),
    )

    metrics = score_outcome(case, record)
    totals = aggregate([record])

    assert metrics["explicit_escalations"] == 0
    assert metrics["raw_escalation_choices"] == 1
    assert metrics["raw_plan_choices"] == 0
    assert metrics["confidence_gate_escalations"] == 1
    assert metrics["plan_attempt_denominator"] == 0
    assert metrics["proposal_coverage_denominator"] == 1
    assert totals["explicit_escalations"] == 0
    assert totals["raw_escalation_choices"] == 1
    assert totals["raw_plan_choices"] == 0
    assert totals["plan_attempt_denominator"] == 0
    assert totals["proposal_coverage_denominator"] == 1
    assert totals["proposal_coverage"] == 0.0


def test_supported_plan_without_persisted_plan_or_gateway_intent_gets_no_credit():
    case = Case(
        case_id="supported-no-persistence",
        request="Survey only the two alley zones.",
        acceptable_plans=("PLAN_ROUTE_ONLY_2_ZONE",),
        expected_terminal="RUNNING",
        expected_effects=(),
    )
    record = _terminal_record(
        case,
        _proposal_attempt(
            "accepted", "PLAN_ROUTE_ONLY_2_ZONE", "PLAN_ROUTE_ONLY_2_ZONE",
        ),
        _route("proposer"),
    )

    metrics = score_outcome(case, record)

    assert metrics["outcome_success"] is False
    assert metrics["proposal_quality_success"] is False
    assert metrics["correct_supported_trial"] is False


def test_supported_plan_credit_requires_command_and_plan_from_same_run(tmp_path):
    case = next(c for c in load_cases() if c.case_id == "route_complete")
    record = run_case(case, "llm", tmp_path)
    proposal = next(
        attempt for attempt in record["tool_attempts"]
        if attempt["command"]["command_type"] == "AGENT_INTENT_PROPOSE"
    )
    proposal["command"]["run_id"] = "wrong-run"

    metrics = score_outcome(case, record)

    assert metrics["outcome_success"] is False
    assert metrics["proposal_quality_success"] is False
    assert metrics["correct_supported_trial"] is False


def test_mixed_attempts_keep_event_counts_and_denominators_coherent(tmp_path):
    case = next(c for c in load_cases() if c.case_id == "route_complete")
    record = run_case(case, "llm", tmp_path)
    record["audit"].insert(0, _proposal_attempt("invalid_answer"))
    record.pop("metrics")

    metrics = score_outcome(case, record)
    totals = aggregate([record])

    assert metrics["proposal_quality_success"] is True
    assert metrics["proposal_calls"] == 2
    assert metrics["accepted_plan_attempts"] == 1
    assert metrics["accepted_escalation_attempts"] == 0
    assert metrics["deterministic_refusals"] == 1
    assert metrics["provider_failures"] == 0
    assert metrics["confidence_gate_escalations"] == 0
    assert metrics["plan_attempt_denominator"] == 1
    assert metrics["proposal_coverage_denominator"] == 2
    assert totals["proposal_case_denominator"] == 1
    assert totals["proposal_call_denominator"] == 2
    assert totals["plan_attempt_denominator"] == 1
    assert totals["proposal_coverage_denominator"] == 2
    assert totals["proposal_accuracy"] == 1.0
    assert totals["proposal_coverage"] == 0.5


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
