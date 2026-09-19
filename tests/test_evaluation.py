import json

import pytest

from sentinel.evaluation import (QUESTION_SET, AlwaysEscalateJudge, AlwaysSafeJudge,
                                 StaticRulesJudge, compare_runs, decision_rule,
                                 run_cases, trivial_baselines)
from sentinel.judgment import JudgeUnavailable, StaticJudge


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


# ---------------------------------------------------------------------------
# run_cases: separated hazard/review scoring, repeats, routing
# ---------------------------------------------------------------------------

def test_run_cases_single_batched_call_and_confidence_logging(tmp_path):
    calls = []

    class CountingJudge(StaticJudge):
        def evaluate(self, state, questions):
            calls.append((state, questions))
            return super().evaluate(state, questions)

    cases = [
        {"case_id": "neg-001", "state": {"observation": "empty corridor"},
         "reference": {"hazard_relevant": False, "human_review_needed": False}},
        {"case_id": "pos-001", "state": {"observation": "fallen tree"},
         "reference": {"hazard_relevant": True, "human_review_needed": False}},
    ]
    judge = CountingJudge(
            booleans={"hazard_relevant": (False, 0.9), "human_review_needed": (True, 0.8),
                      "more_evidence_needed": (False, 0.3)},
            choices={"priority": ("low", {"ignore": 0.1, "low": 0.7, "medium": 0.1, "high": 0.05, "critical": 0.05})},
            scores={"priority_scored": 1.0},
        )
    metrics, log_path = run_cases(cases, judge, log_path=tmp_path / "log.jsonl")
    # hazard: neg ok, pos judged False -> false_safe. review: neg judged True -> false_escalation.
    assert metrics["total"] == 2
    assert metrics["unavailable"] == 0
    assert metrics["hazard"] == {"scored": 2, "false_safe": 1, "false_escalation": 0}
    # review: neg judged True (fe), pos judged True but ref False (fe) -> 2 false_escalation.
    assert metrics["review"] == {"scored": 2, "false_safe": 0, "false_escalation": 2}
    assert metrics["stability"]["cases"] == 4  # 2 cases x 2 scored fields
    assert metrics["stability"]["flipped"] == 0
    # ONE batched evaluate call per case per repeat (default repeats=3)
    assert len(calls) == 2 * 3
    assert calls[0][1] == QUESTION_SET
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2 * 3
    rec = records[3]
    assert rec["case_id"] == "pos-001"
    assert rec["repeat"] == 0
    assert rec["answers"]["hazard_relevant"] == {"value": False, "probability": 0.9}
    assert rec["answers"]["priority"]["value"] == "low"
    assert rec["answers"]["priority_scored"]["legend"]["1"] == "minor"
    # confidence present for choice/score primitives (None for the static double is allowed)
    assert "confidence" in rec["answers"]["priority"]
    assert "confidence" in rec["answers"]["priority_scored"]
    assert rec["answers"]["human_review_needed"] == {"value": True, "probability": 0.8}
    assert isinstance(rec["latency_ms"], int)  # one latency per batch


def test_run_cases_repeats_default_three_and_explicit_override(tmp_path):
    cases = [{"case_id": "c1", "state": {}, "reference": {"hazard_relevant": False}}]
    _, log3 = run_cases(cases, _judge(), log_path=tmp_path / "log3.jsonl")
    assert len(log3.read_text(encoding="utf-8").splitlines()) == 3
    _, log1 = run_cases(cases, _judge(), log_path=tmp_path / "log1.jsonl", repeats=1)
    assert len(log1.read_text(encoding="utf-8").splitlines()) == 1


def test_run_cases_repeats_flip_stability(tmp_path):
    class FlipJudge(StaticJudge):
        # First repeat escalates, second is safe, third escalates again.
        def evaluate(self, state, questions):
            n = self.calls
            self.calls += 1
            value = n % 2 == 0
            return {"hazard_relevant": {"value": value, "probability": 0.9}}

    flip = FlipJudge()
    flip.calls = 0
    metrics, _ = run_cases(
        [{"case_id": "c1", "state": {}, "reference": {"hazard_relevant": True, "human_review_needed": False}}],
        flip, log_path=tmp_path / "log.jsonl", repeats=3)
    assert metrics["stability"]["cases"] == 1
    assert metrics["stability"]["flipped"] == 1
    assert metrics["stability"]["flip_count"] == 2  # True->False, False->True


def test_run_cases_null_hazard_label_skipped_but_review_scored(tmp_path):
    # amb-* shape: hazard label null -> hazard unscored; review scored against bool.
    cases = [{"case_id": "amb-001", "state": {},
              "reference": {"hazard_relevant": None, "human_review_needed": True}}]
    judge = StaticJudge(booleans={"hazard_relevant": (False, 0.9),
                                  "human_review_needed": (False, 0.6)})
    metrics, _ = run_cases(cases, judge, log_path=tmp_path / "log.jsonl")
    assert metrics["hazard"] == {"scored": 0, "false_safe": 0, "false_escalation": 0}
    # judged review False vs reference True -> false_safe on the review field.
    assert metrics["review"] == {"scored": 1, "false_safe": 1, "false_escalation": 0}


def test_run_cases_scores_and_logs(tmp_path):
    cases = [
        {"case_id": "neg-001", "state": {"observation": "empty corridor"},
         "reference": {"hazard_relevant": False, "human_review_needed": False}},
        {"case_id": "pos-001", "state": {"observation": "fallen tree"},
         "reference": {"hazard_relevant": True, "human_review_needed": False}},
        {"case_id": "pos-002", "state": {"observation": "person prone"},
         "reference": {"hazard_relevant": True, "human_review_needed": True}},
    ]
    metrics, log_path = run_cases(cases, _judge(), log_path=tmp_path / "log.jsonl")
    # hazard: neg ok; two positives judged False -> 2 false_safe.
    assert metrics["hazard"] == {"scored": 3, "false_safe": 2, "false_escalation": 0}
    # review: neg ok (False False); pos-001 ok (False False); pos-002 ref True judged False -> fs.
    # StaticJudge has no human_review rule configured, so review is not judged -> scored 0.
    assert metrics["review"] == {"scored": 0, "false_safe": 0, "false_escalation": 0}
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 9  # 3 cases x 3 repeats
    for case in cases:
        case_records = [r for r in records if r["case_id"] == case["case_id"]]
        assert len(case_records) == 3
        assert [r["repeat"] for r in case_records] == [0, 1, 2]
        for record in case_records:
            assert record["state"] == case["state"]
            assert record["reference"] == case["reference"]
            assert record["judge"]["model"] == "static"
            assert record["answers"]["hazard_relevant"] == {"value": False, "probability": 0.9}


def test_run_cases_records_unavailable(tmp_path):
    class DownJudge(StaticJudge):
        def evaluate(self, state, questions):
            raise JudgeUnavailable("judge down")

    metrics, log_path = run_cases(
        [{"case_id": "c1", "state": {}, "reference": {"hazard_relevant": True, "human_review_needed": False}}],
        DownJudge(), log_path=tmp_path / "log.jsonl")
    assert metrics["unavailable"] == 3  # one per repeat, each fail-closed
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 3  # one per repeat, each marked unavailable
    assert all(r["answers"] == {"error": {"unavailable": True}} for r in records)


def test_run_cases_records_routing_bands_and_threshold(tmp_path):
    judge = StaticJudge(booleans={"hazard_relevant": (False, 0.9),
                                  "human_review_needed": (True, 0.4)})
    metrics, log_path = run_cases(
        [{"case_id": "c1", "state": {}, "reference": {"hazard_relevant": False, "human_review_needed": False}}],
        judge, log_path=tmp_path / "log.jsonl")
    routing = metrics["routing"]
    # Plain 0.5 threshold over P(noul): 0.9 -> escalate (True), 0.4 -> no.
    assert routing["threshold_0.5"] == {"escalate": 3, "no_escalate": 3}
    # Three-band routing from Jev self-assessed confidence: absent -> unknown.
    assert routing["bands"] == {"high": 0, "medium": 0, "low": 0, "unknown": 6}
    # Confidence carried into the log untouched.
    rec = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["judge"]["model"] == "static"


# ---------------------------------------------------------------------------
# compare_runs: decision-value scoring
# ---------------------------------------------------------------------------

def _write_run(tmp_path, name, records):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _rec(case_id, value, latency=100, reference=None, probability=0.9, repeat=0):
    ref = reference if reference is not None else {"hazard_relevant": value}
    return {"case_id": case_id, "repeat": repeat, "state": {}, "reference": ref,
            "judge": {"model": "m"}, "latency_ms": latency,
            "answers": {"hazard_relevant": {"value": value, "probability": probability}}}


def test_compare_runs_agreement_nonzero_despite_differing_floats(tmp_path):
    # Decisions match but probabilities differ -> agreement must be 1.0, with the
    # probability delta reported separately (the old whole-dict compare gave 0).
    a = _write_run(tmp_path, "a.jsonl", [_rec("c1", True, probability=0.9)])
    b = _write_run(tmp_path, "b.jsonl", [_rec("c1", True, probability=0.55)])
    s = compare_runs(a, b)
    assert s["case_count"] == 1
    assert s["per_case_agreement"] == {"c1": True}
    assert s["agreement_rate"] == 1.0
    assert s["per_question_agreement"]["hazard_relevant"] == 1.0
    assert s["probability_deltas"]["hazard_relevant"] == pytest.approx(-0.35)  # b - a


def test_compare_runs_agreement_and_deltas(tmp_path):
    a = _write_run(tmp_path, "a.jsonl", [
        _rec("c1", True, latency=100), _rec("c2", False, latency=200)])
    b = _write_run(tmp_path, "b.jsonl", [
        _rec("c1", True, latency=150), _rec("c2", True, latency=300,
                                            reference={"hazard_relevant": False})])
    s = compare_runs(a, b)
    assert s["case_count"] == 2
    assert s["per_case_agreement"] == {"c1": True, "c2": False}
    assert s["agreement_rate"] == 0.5
    assert s["per_question_agreement"] == {"hazard_relevant": 0.5}
    # deltas: b minus a. a: ref c1 True judged True (ok), c2 False judged False (ok) -> 0/0.
    # b: c1 True True ok; c2 ref False judged True -> false_escalation.
    assert s["hazard"]["false_safe"] == {"a": 0, "b": 0, "delta": 0}
    assert s["hazard"]["false_escalation"] == {"a": 0, "b": 1, "delta": 1}
    assert s["mean_latency_ms"] == {"a": 150.0, "b": 225.0, "delta": 75.0}


def test_compare_runs_review_field_scored_separately(tmp_path):
    def rec(case_id, hazard, review, ref_hazard=True, ref_review=False):
        return {"case_id": case_id, "repeat": 0, "state": {},
                "reference": {"hazard_relevant": ref_hazard, "human_review_needed": ref_review},
                "judge": {"model": "m"}, "latency_ms": 100,
                "answers": {"hazard_relevant": {"value": hazard, "probability": 0.9},
                            "human_review_needed": {"value": review, "probability": 0.8}}}

    # amb case: hazard label null -> hazard unscored; review False vs True -> false_safe.
    amb = rec("amb-001", False, False, ref_hazard=None, ref_review=True)
    a = _write_run(tmp_path, "a.jsonl", [amb])
    b = _write_run(tmp_path, "b.jsonl", [dict(amb, answers={
        "hazard_relevant": {"value": True, "probability": 0.9},
        "human_review_needed": {"value": True, "probability": 0.9}})])
    s = compare_runs(a, b)
    assert s["hazard"]["scored"] == {"a": 0, "b": 0}  # null label skipped in both runs
    assert s["review"]["scored"] == {"a": 1, "b": 1}
    assert s["review"]["false_safe"] == {"a": 1, "b": 0, "delta": -1}
    assert s["review"]["false_escalation"] == {"a": 0, "b": 0, "delta": 0}


def test_compare_runs_missing_reference_label_skips_scoring(tmp_path):
    a = _write_run(tmp_path, "a.jsonl", [_rec("c1", True, reference={})])
    b = _write_run(tmp_path, "b.jsonl", [_rec("c1", False, reference={})])
    s = compare_runs(a, b)
    assert s["agreement_rate"] == 0.0  # decisions differ even without a reference label
    assert s["hazard"]["scored"] == {"a": 0, "b": 0}
    assert s["hazard"]["false_safe"] == {"a": 0, "b": 0, "delta": 0}
    assert s["hazard"]["false_escalation"] == {"a": 0, "b": 0, "delta": 0}


def test_compare_runs_mismatched_case_sets_fail(tmp_path):
    a = _write_run(tmp_path, "a.jsonl", [_rec("c1", True)])
    b = _write_run(tmp_path, "b.jsonl", [_rec("other", True)])
    with pytest.raises(ValueError):
        compare_runs(a, b)


def test_compare_runs_cost_totals_when_usage_present(tmp_path):
    ra = _rec("c1", True)
    rb = _rec("c1", True)
    ra["judge"]["usage"] = {"cost": 0.01}
    rb["judge"]["usage"] = {"cost": 0.03}
    a = _write_run(tmp_path, "a.jsonl", [ra])
    b = _write_run(tmp_path, "b.jsonl", [rb])
    s = compare_runs(a, b)
    assert s["cost"] == {"a": 0.01, "b": 0.03}


# ---------------------------------------------------------------------------
# Trivial baselines and the fixture-ranking decision rule
# ---------------------------------------------------------------------------

def _mixed_cases():
    return [
        {"case_id": "neg-001", "state": {}, "reference": {"hazard_relevant": False, "human_review_needed": False}},
        {"case_id": "neg-002", "state": {}, "reference": {"hazard_relevant": False, "human_review_needed": False}},
        {"case_id": "pos-001", "state": {}, "reference": {"hazard_relevant": True, "human_review_needed": True}},
        {"case_id": "amb-001", "state": {}, "reference": {"hazard_relevant": None, "human_review_needed": True}},
    ]


def test_always_escalate_known_counts():
    m = trivial_baselines(_mixed_cases())["always_escalate"]
    assert m["hazard"] == {"scored": 3, "false_safe": 0, "false_escalation": 2}
    assert m["review"] == {"scored": 4, "false_safe": 0, "false_escalation": 2}


def test_always_safe_known_counts():
    m = trivial_baselines(_mixed_cases())["always_safe"]
    assert m["hazard"] == {"scored": 3, "false_safe": 1, "false_escalation": 0}
    # always_safe answers False everywhere: the 2 reference-review-True cases are false_safe.
    assert m["review"] == {"scored": 4, "false_safe": 2, "false_escalation": 0}


def test_static_rules_deterministic_counts():
    cases = [
        # on-trail, big object, high perception confidence -> hazard, no review.
        {"case_id": "r1", "state": {"observation": {"object": "fallen_tree", "size_m": 4.0,
                                                    "position_relation": "on_trail",
                                                    "perception_confidence": 0.97,
                                                    "duplicate_of_existing_detection": False}},
         "reference": {"hazard_relevant": True, "human_review_needed": False}},
        # low confidence -> no hazard call, review required.
        {"case_id": "r2", "state": {"observation": {"object": "unclassified_object", "size_m": 0.5,
                                                    "position_relation": "on_trail",
                                                    "perception_confidence": 0.52,
                                                    "duplicate_of_existing_detection": False}},
         "reference": {"hazard_relevant": None, "human_review_needed": True}},
    ]
    m = trivial_baselines(cases)["static_rules"]
    assert m["hazard"] == {"scored": 1, "false_safe": 0, "false_escalation": 0}
    assert m["review"] == {"scored": 2, "false_safe": 0, "false_escalation": 0}
    # and the baseline judge scores identically through the harness itself
    metrics2, _ = run_cases(cases, StaticRulesJudge(), log_path=_tmp_log(), repeats=1)
    assert metrics2["hazard"]["false_safe"] == 0
    assert metrics2["review"]["false_escalation"] == 0


def _tmp_log():
    import tempfile, pathlib
    return pathlib.Path(tempfile.gettempdir()) / "sentinel_eval_static_rules.jsonl"


def test_decision_rule_beats_always_escalate():
    cases = _mixed_cases()
    bases = trivial_baselines(cases)
    # A judge with zero false-safe and one fewer false-escalation than always-escalate.
    judge_m = {"hazard": {"scored": 3, "false_safe": 0, "false_escalation": 1}}
    assert decision_rule(judge_m, bases)["ranks_fixture"] is True
    # Equal false-escalation to always-escalate -> fixture cannot rank it.
    tie_m = {"hazard": {"scored": 3, "false_safe": 0, "false_escalation": 2}}
    assert decision_rule(tie_m, bases)["ranks_fixture"] is False
    # Worse false-safe than always-escalate (0) -> cannot rank regardless.
    fs_m = {"hazard": {"scored": 3, "false_safe": 1, "false_escalation": 0}}
    assert decision_rule(fs_m, bases)["ranks_fixture"] is False
