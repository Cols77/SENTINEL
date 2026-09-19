import json

from sentinel.evaluation import QUESTION_SET, run_cases
from sentinel.judgment import StaticJudge


def _judge() -> StaticJudge:
    # Wrong on exactly one positive and one negative case -> 1 false_safe, 1 false_escalation.
    return StaticJudge(booleans={"hazard_relevant": (False, 0.9)})


def test_question_set_has_five_questions_with_expected_types():
    by_name = {q["name"]: q for q in QUESTION_SET}
    assert set(by_name) == {"hazard_relevant", "priority", "priority_scored",
                            "human_review_needed", "more_evidence_needed"}
    assert by_name["hazard_relevant"]["type"] == "noul"
    assert by_name["hazard_relevant"]["criteria"] is None  # Jev docs: criteria-less noul
    assert by_name["priority"]["type"] == "choice"
    assert by_name["priority"]["criteria"] == {
        "ignore": "no action warranted", "low": "minor, monitor",
        "medium": "should be inspected soon", "high": "blocks trail or immediate risk",
        "critical": "life-safety or immediate response required"}
    assert by_name["priority_scored"]["type"] == "score"
    assert by_name["priority_scored"]["criteria"] == ["not a hazard", "minor", "needs inspection", "urgent"]
    assert by_name["human_review_needed"]["type"] == "noul"
    assert by_name["more_evidence_needed"]["type"] == "noul"
    for q in QUESTION_SET:
        assert isinstance(q["instruction"], str) and q["instruction"]


def test_run_cases_single_batched_call_and_confidence_logging(tmp_path):
    calls = []

    class CountingJudge(StaticJudge):
        def evaluate(self, state, questions):
            calls.append((state, questions))
            return super().evaluate(state, questions)

    cases = [
        {"case_id": "neg-001", "state": {"observation": "empty corridor"}, "reference": {"hazard_relevant": False}},
        {"case_id": "pos-001", "state": {"observation": "fallen tree"}, "reference": {"hazard_relevant": True}},
    ]
    judge = CountingJudge(
            booleans={"hazard_relevant": (False, 0.9), "human_review_needed": (True, 0.8),
                      "more_evidence_needed": (False, 0.3)},
            choices={"priority": ("low", {"ignore": 0.1, "low": 0.7, "medium": 0.1, "high": 0.05, "critical": 0.05})},
            scores={"priority_scored": 1.0},
        )
    metrics, log_path = run_cases(cases, judge, log_path=tmp_path / "log.jsonl")
    assert metrics == {"total": 2, "false_safe": 1, "false_escalation": 0, "unavailable": 0}
    # ONE batched evaluate call per case, carrying the full QUESTION_SET
    assert len(calls) == 2
    assert calls[0][1] == QUESTION_SET
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    rec = records[1]
    assert rec["case_id"] == "pos-001"
    assert rec["answers"]["hazard_relevant"] == {"value": False, "probability": 0.9}
    assert rec["answers"]["priority"]["value"] == "low"
    assert rec["answers"]["priority_scored"]["legend"]["1"] == "minor"
    # confidence present for choice/score primitives (None for the static double is allowed)
    assert "confidence" in rec["answers"]["priority"]
    assert "confidence" in rec["answers"]["priority_scored"]
    assert rec["answers"]["human_review_needed"] == {"value": True, "probability": 0.8}
    assert isinstance(rec["latency_ms"], int)  # one latency per batch


def test_run_cases_scores_and_logs(tmp_path):
    cases = [
        {"case_id": "neg-001", "state": {"observation": "empty corridor"}, "reference": {"hazard_relevant": False}},
        {"case_id": "pos-001", "state": {"observation": "fallen tree"}, "reference": {"hazard_relevant": True}},
        {"case_id": "pos-002", "state": {"observation": "person prone"}, "reference": {"hazard_relevant": True}},
    ]
    metrics, log_path = run_cases(cases, _judge(), log_path=tmp_path / "log.jsonl")
    assert metrics == {"total": 3, "false_safe": 2, "false_escalation": 0, "unavailable": 0}
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 3
    for case, record in zip(cases, records):
        assert record["case_id"] == case["case_id"]
        assert record["state"] == case["state"]
        assert record["reference"] == case["reference"]
        assert record["judge"]["model"] == "static"
        assert record["answers"]["hazard_relevant"] == {"value": False, "probability": 0.9}


def test_run_cases_counts_mixed_outcomes(tmp_path):
    cases = [
        {"case_id": "neg-001", "state": {}, "reference": {"hazard_relevant": False}},
        {"case_id": "pos-001", "state": {}, "reference": {"hazard_relevant": True}},
    ]
    # false_safe (reference True judged False) is the MOST SERIOUS outcome and is counted explicitly.
    _, log1 = run_cases(cases, _judge(), log_path=tmp_path / "log1.jsonl")
    m1, _ = run_cases(cases, _judge(), log_path=tmp_path / "log1.jsonl")
    assert m1["false_safe"] == 1
    assert m1["false_escalation"] == 0
    assert log1.exists()

    escalator = StaticJudge(booleans={"hazard_relevant": (True, 0.9)})
    m2, _ = run_cases(cases, escalator, log_path=tmp_path / "log2.jsonl")
    assert m2["false_escalation"] == 1
    assert m2["false_safe"] == 0


def test_run_cases_records_unavailable(tmp_path):
    from sentinel.judgment import JudgeUnavailable

    class DownJudge(StaticJudge):
        def evaluate(self, state, questions):
            raise JudgeUnavailable("judge down")

    metrics, _ = run_cases(
        [{"case_id": "c1", "state": {}, "reference": {"hazard_relevant": True}}],
        DownJudge(), log_path=tmp_path / "log.jsonl")
    assert metrics["unavailable"] == 1


# ---------------------------------------------------------------------------
# compare_runs: cross-run comparison metrics
# ---------------------------------------------------------------------------

def _write_run(tmp_path, name, records):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _rec(case_id, value, latency=100, reference=None):
    ref = reference if reference is not None else {"hazard_relevant": value}
    return {"case_id": case_id, "state": {}, "reference": ref, "judge": {"model": "m"},
            "latency_ms": latency,
            "answers": {"hazard_relevant": {"value": value, "probability": 0.9}}}


def test_compare_runs_agreement_and_deltas(tmp_path):
    from sentinel.evaluation import compare_runs

    a = _write_run(tmp_path, "a.jsonl", [
        _rec("c1", True, latency=100), _rec("c2", False, latency=200)])
    b = _write_run(tmp_path, "b.jsonl", [
        _rec("c1", True, latency=150), _rec("c2", True, latency=300, reference={"hazard_relevant": False})])
    s = compare_runs(a, b)
    assert s["case_count"] == 2
    assert s["per_case_agreement"] == {"c1": True, "c2": False}
    assert s["agreement_rate"] == 0.5
    assert s["per_question_agreement"] == {"hazard_relevant": 0.5}
    # deltas: b minus a. a: ref c1 True judged True (ok), c2 False judged False (ok) -> 0/0.
    # b: c1 True True ok; c2 ref False judged True -> false_escalation.
    assert s["false_safe"] == {"a": 0, "b": 0, "delta": 0}
    assert s["false_escalation"] == {"a": 0, "b": 1, "delta": 1}
    assert s["mean_latency_ms"] == {"a": 150.0, "b": 225.0, "delta": 75.0}


def test_compare_runs_missing_reference_label_skips_scoring(tmp_path):
    from sentinel.evaluation import compare_runs

    a = _write_run(tmp_path, "a.jsonl", [_rec("c1", True, reference={})])
    b = _write_run(tmp_path, "b.jsonl", [_rec("c1", False, reference={})])
    s = compare_runs(a, b)
    assert s["agreement_rate"] == 0.0  # answers differ even without a reference label
    assert s["false_safe"] == {"a": 0, "b": 0, "delta": 0}
    assert s["false_escalation"] == {"a": 0, "b": 0, "delta": 0}


def test_compare_runs_mismatched_case_sets_fail(tmp_path):
    from sentinel.evaluation import compare_runs
    import pytest

    a = _write_run(tmp_path, "a.jsonl", [_rec("c1", True)])
    b = _write_run(tmp_path, "b.jsonl", [_rec("other", True)])
    with pytest.raises(ValueError):
        compare_runs(a, b)


def test_compare_runs_cost_totals_when_usage_present(tmp_path):
    from sentinel.evaluation import compare_runs

    ra = _rec("c1", True)
    rb = _rec("c1", True)
    ra["judge"]["usage"] = {"cost": 0.01}
    rb["judge"]["usage"] = {"cost": 0.03}
    a = _write_run(tmp_path, "a.jsonl", [ra])
    b = _write_run(tmp_path, "b.jsonl", [rb])
    s = compare_runs(a, b)
    assert s["cost"] == {"a": 0.01, "b": 0.03}
