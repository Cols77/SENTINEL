import json

from sentinel.evaluation import run_cases
from sentinel.judgment import StaticJudge


def _judge() -> StaticJudge:
    # Wrong on exactly one positive and one negative case -> 1 false_safe, 1 false_escalation.
    return StaticJudge(booleans={"hazard_relevant": (False, 0.9)})


def test_run_cases_scores_and_logs(tmp_path):
    cases = [
        {"case_id": "neg-001", "state": {"observation": "empty corridor"}, "reference": {"hazard_relevant": False}},
        {"case_id": "pos-001", "state": {"observation": "fallen tree"}, "reference": {"hazard_relevant": True}},
        {"case_id": "pos-002", "state": {"observation": "person prone"}, "reference": {"hazard_relevant": True}},
    ]
    metrics, log_path = run_cases(cases, _judge(), boolean_names=["hazard_relevant"], log_path=tmp_path / "log.jsonl")
    assert metrics == {"total": 3, "false_safe": 2, "false_escalation": 0, "unavailable": 0}
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 3
    for case, record in zip(cases, records):
        assert record["case_id"] == case["case_id"]
        assert record["state"] == case["state"]
        assert record["reference"] == case["reference"]
        assert record["judge"]["model"] == "static"
        assert isinstance(record["latency_ms"], int)
        assert record["answers"]["hazard_relevant"] == {"value": False, "probability": 0.9}


def test_run_cases_counts_mixed_outcomes(tmp_path):
    cases = [
        {"case_id": "neg-001", "state": {}, "reference": {"hazard_relevant": False}},
        {"case_id": "pos-001", "state": {}, "reference": {"hazard_relevant": True}},
    ]
    # false_safe (reference True judged False) is the MOST SERIOUS outcome and is counted explicitly.
    _, log1 = run_cases(cases, _judge(), boolean_names=["hazard_relevant"], log_path=tmp_path / "log1.jsonl")
    m1, _ = run_cases(cases, _judge(), boolean_names=["hazard_relevant"], log_path=tmp_path / "log1.jsonl")
    assert m1["false_safe"] == 1
    assert m1["false_escalation"] == 0
    assert log1.exists()

    escalator = StaticJudge(booleans={"hazard_relevant": (True, 0.9)})
    m2, _ = run_cases(cases, escalator, boolean_names=["hazard_relevant"], log_path=tmp_path / "log2.jsonl")
    assert m2["false_escalation"] == 1
    assert m2["false_safe"] == 0
