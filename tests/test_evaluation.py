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
    assert by_name["hazard_relevant"]["criteria"] == {
        "true": "operational trail-opening hazard requiring action",
        "false": "not operationally relevant"}
    assert by_name["priority"]["type"] == "choice"
    assert by_name["priority"]["criteria"] == {
        "ignore": "no action warranted", "low": "minor, monitor",
        "medium": "should be inspected soon", "high": "blocks trail or immediate risk"}
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
        choices={"priority": ("low", {"ignore": 0.1, "low": 0.7, "medium": 0.1, "high": 0.1})},
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
