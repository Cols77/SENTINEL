"""Shadow replay harness: score a judge against reference-labeled cases.

Policy-relevant safety rules:
- a false_safe (reference True judged False) is the MOST SERIOUS outcome and is
  counted explicitly, never folded into a single accuracy number;
- hazard and human-review questions are scored SEPARATELY against their own v2
  reference labels; cases whose label for a field is null (ambiguous) are
  skipped for that field only;
- agreement between two runs compares only the DECISION value per question
  (never probabilities, confidence, or legend), and probability deltas are
  reported separately so float jitter cannot masquerade as disagreement.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from sentinel.judgment import BoundedJudge, JudgeUnavailable


QUESTION_SET = [
    {
        "name": "hazard_relevant",
        "type": "noul",
        "instruction": "Is the observed object an operational trail-opening hazard requiring action?",
        "criteria": None,
    },
    {
        "name": "priority",
        "type": "choice",
        "instruction": "What priority applies to this observation?",
        "criteria": {"ignore": "no action warranted", "low": "minor, monitor",
                     "medium": "should be inspected soon", "high": "blocks trail or immediate risk",
                     "critical": "life-safety or immediate response required"},
    },
    {
        "name": "priority_scored",
        "type": "score",
        "instruction": "Score the urgency of acting on this observation.",
        "criteria": ["not a hazard", "minor", "needs inspection", "urgent"],
    },
    {
        "name": "human_review_needed",
        "type": "noul",
        "instruction": "Should a human review this observation before action?",
        "criteria": None,
    },
    {
        "name": "more_evidence_needed",
        "type": "noul",
        "instruction": "Is more evidence needed before acting on this observation?",
        "criteria": None,
    },
]

#: noul questions scored against v2 reference labels, keyed by label field.
SCORABLE_NOUL_FIELDS = {
    "hazard_relevant": "hazard_relevant",
    "human_review_needed": "human_review_needed",
}

DEFAULT_REPEATS = 3


def _field_metrics() -> dict[str, int]:
    return {"scored": 0, "false_safe": 0, "false_escalation": 0}


def _route_band(answer: dict[str, Any]) -> str:
    """Three-band routing from the judge's own confidence, when it provides one.

    Falls back to 'unknown' when the answer carries no self-assessed
    confidence; the plain 0.5 probability threshold is reported separately.
    """
    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
        return "unknown"
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _decision_value(answer: dict[str, Any]) -> Any:
    """The decision a question's answer represents (never floats/metadata)."""
    if "value" not in answer:
        return None
    value = answer["value"]
    if isinstance(value, bool):
        return bool(value)
    return value


def run_cases(
    cases: Iterable[dict],
    judge: BoundedJudge,
    *,
    log_path: Path,
    repeats: int = DEFAULT_REPEATS,
) -> tuple[dict[str, Any], Path]:
    """Replay reference-labeled cases through the judge (shadow-only).

    All QUESTION_SET questions go to the judge in ONE batched evaluate() call
    per case per repeat. Every repeat is logged with its ``repeat`` index;
    a per-case stability metric counts answers whose decision flips across
    repeats. Jev confidence (where provided) is preserved verbatim in the
    log, and three-band routing (high/medium/low) is reported alongside the
    plain 0.5 probability threshold.

    Metrics are reported separately per scorable noul field (hazard and
    human review); a case whose reference label for a field is null is
    skipped for that field only.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    metrics: dict[str, Any] = {
        "total": 0,
        "unavailable": 0,
        "hazard": _field_metrics(),
        "review": _field_metrics(),
        "stability": {"cases": 0, "flipped": 0, "flip_count": 0},
        "routing": {"threshold_0.5": {"escalate": 0, "no_escalate": 0},
                    "bands": {"high": 0, "medium": 0, "low": 0, "unknown": 0}},
    }
    model = getattr(judge, "model", "static")
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    decisions_by_case: dict[str, dict[str, list[Any]]] = {}
    with log_path.open("a", encoding="utf-8") as log:
        for case in cases:
            metrics["total"] += 1
            case_id = case["case_id"]
            reference = case["reference"]
            decisions_by_case[case_id] = {}
            for repeat in range(repeats):
                start = time.perf_counter()
                try:
                    answers: dict[str, dict[str, Any]] = judge.evaluate(case["state"], QUESTION_SET)
                except JudgeUnavailable:
                    metrics["unavailable"] += 1
                    answers = {"error": {"unavailable": True}}
                    log.write(json.dumps({
                        "case_id": case_id,
                        "repeat": repeat,
                        "state": case["state"],
                        "reference": reference,
                        "judge": {"model": model},
                        "latency_ms": int((time.perf_counter() - start) * 1000),
                        "answers": answers,
                    }) + "\n")
                    continue
                latency_ms = int((time.perf_counter() - start) * 1000)
                for question, answer in answers.items():
                    field = SCORABLE_NOUL_FIELDS.get(question)
                    if field is None:
                        continue
                    label = reference.get(field)
                    if not isinstance(label, bool):
                        continue  # null label: skipped for this field only
                    value = answer.get("value")
                    if not isinstance(value, bool):
                        continue
                    field_metrics = (
                        metrics["review"] if field == "human_review_needed" else metrics["hazard"])
                    routing = metrics["routing"]
                    if repeat == repeats - 1:
                        # Accuracy counts once per case, from the FINAL repeat;
                        # earlier repeats feed the stability metric only.
                        field_metrics["scored"] += 1
                        if label != value:
                            # reference True judged False is a false safe -> most serious.
                            if label:
                                field_metrics["false_safe"] += 1
                            else:
                                field_metrics["false_escalation"] += 1
                    prob = answer.get("probability")
                    if isinstance(prob, (int, float)) and 0.0 <= prob <= 1.0:
                        if value:
                            routing["threshold_0.5"]["escalate"] += 1
                        else:
                            routing["threshold_0.5"]["no_escalate"] += 1
                    routing["bands"][_route_band(answer)] += 1
                    decisions_by_case[case_id].setdefault(field, []).append(value)
                log.write(json.dumps({
                    "case_id": case_id,
                    "repeat": repeat,
                    "state": case["state"],
                    "reference": reference,
                    "judge": {"model": model},
                    "latency_ms": latency_ms,
                    "answers": answers,
                }) + "\n")
            # stability: did any scored field's decision flip across repeats?
            for field, decisions in decisions_by_case[case_id].items():
                metrics["stability"]["cases"] += 1
                flips = sum(1 for i in range(1, len(decisions)) if decisions[i] != decisions[i - 1])
                if flips:
                    metrics["stability"]["flipped"] += 1
                    metrics["stability"]["flip_count"] += flips
    return metrics, log_path


def _load_run_records(path) -> dict[str, dict]:
    path = Path(path)
    records: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            case_id = rec["case_id"]
            repeat = rec.get("repeat", 0)
            key = (case_id, repeat)
            if key in records:
                raise ValueError(f"duplicate case_id/repeat in run log {path}: {key!r}")
            records[key] = rec
    return records


def _field_outcomes(records: dict[tuple, dict], field: str) -> dict[str, int]:
    """Score one noul field against its own reference label, skipping nulls."""
    counts = _field_metrics()
    for rec in records.values():
        label = rec.get("reference", {}).get(field)
        if not isinstance(label, bool):
            continue  # null label: unscored for this field
        try:
            judged = rec["answers"][field]["value"]
        except (KeyError, TypeError):
            continue  # unavailable/error records carry no scorable answer
        if not isinstance(judged, bool):
            continue
        counts["scored"] += 1
        if label != judged:
            if label:
                counts["false_safe"] += 1
            else:
                counts["false_escalation"] += 1
    return counts


def compare_runs(file_a, file_b) -> dict[str, Any]:
    """Compare two run JSONL logs produced by run_cases.

    Agreement is computed on DECISION VALUES only (bool per noul question,
    option per choice, score bucket per score): differing probabilities,
    confidence, or legend metadata never reduce agreement. Probability
    deltas are reported separately. false_safe / false_escalation are scored
    separately for hazard and human-review fields against the v2 labels;
    null-label cases are skipped for that field. Logs with per-repeat records
    are compared repeat-by-repeat (case, repeat) pairs.
    """
    run_a = _load_run_records(file_a)
    run_b = _load_run_records(file_b)
    if set(run_a) != set(run_b):
        only_a = sorted(set(run_a) - set(run_b))
        only_b = sorted(set(run_b) - set(run_a))
        raise ValueError(f"case sets differ (a-only: {only_a}, b-only: {only_b})")
    common = sorted(run_a)

    per_case_agreement: dict[str, list[bool]] = {}
    question_totals: dict[str, list[int]] = {}
    prob_deltas: dict[str, list[float]] = {}
    latencies: dict[str, list[float]] = {"a": [], "b": []}
    for key in common:
        case_id = key[0]
        rec_a, rec_b = run_a[key], run_b[key]
        latencies["a"].append(rec_a["latency_ms"])
        latencies["b"].append(rec_b["latency_ms"])
        ans_a, ans_b = rec_a.get("answers", {}), rec_b.get("answers", {})
        if ans_a.get("error", {}).get("unavailable") and ans_b.get("error", {}).get("unavailable"):
            agree = False  # both unavailable: not comparable
        else:
            scorable = [q for q in set(ans_a) | set(ans_b) if "error" not in ans_a.get(q, {})
                        and "error" not in ans_b.get(q, {})]
            agree = bool(scorable) and all(
                _decision_value(ans_a.get(q, {})) == _decision_value(ans_b.get(q, {}))
                for q in scorable)
        per_case_agreement.setdefault(case_id, []).append(agree)
        for q in set(ans_a) | set(ans_b):
            totals = question_totals.setdefault(q, [0, 0])
            if q in ans_a and q in ans_b:
                totals[0] += 1
                if _decision_value(ans_a[q]) == _decision_value(ans_b[q]):
                    totals[1] += 1
                pa = ans_a[q].get("probability")
                pb = ans_b[q].get("probability")
                if isinstance(pa, (int, float)) and isinstance(pb, (int, float)):
                    prob_deltas.setdefault(q, []).append(pb - pa)
            elif q in ans_a or q in ans_b:
                totals[0] += 1  # mismatch: answer present in only one run

    summary: dict[str, Any] = {
        "case_count": len({cid for cid, _ in common}),
        "comparison_count": len(common),
        "per_case_agreement": {cid: all(flags) for cid, flags in per_case_agreement.items()},
        "agreement_rate": (sum(all(flags) for flags in per_case_agreement.values()) / len(common)) if common else 0.0,
        "per_question_agreement": {
            q: (hits / total) if total else 0.0
            for q, (total, hits) in sorted(question_totals.items())
        },
        "probability_deltas": {
            q: sum(deltas) / len(deltas) for q, deltas in sorted(prob_deltas.items())
        },
    }
    for label, field in (("hazard", "hazard_relevant"), ("review", "human_review_needed")):
        outcomes = {"a": _field_outcomes(run_a, field), "b": _field_outcomes(run_b, field)}
        summary[label] = {
            "scored": {"a": outcomes["a"]["scored"], "b": outcomes["b"]["scored"]},
            "false_safe": {
                "a": outcomes["a"]["false_safe"], "b": outcomes["b"]["false_safe"],
                "delta": outcomes["b"]["false_safe"] - outcomes["a"]["false_safe"]},
            "false_escalation": {
                "a": outcomes["a"]["false_escalation"], "b": outcomes["b"]["false_escalation"],
                "delta": outcomes["b"]["false_escalation"] - outcomes["a"]["false_escalation"]},
        }
    mean_a = sum(latencies["a"]) / len(latencies["a"]) if latencies["a"] else 0.0
    mean_b = sum(latencies["b"]) / len(latencies["b"]) if latencies["b"] else 0.0
    summary["mean_latency_ms"] = {"a": mean_a, "b": mean_b, "delta": mean_b - mean_a}

    def _cost(records: dict[tuple, dict]) -> float | None:
        costs = [r["judge"]["usage"].get("cost") for r in records.values()
                 if isinstance(r.get("judge", {}).get("usage"), dict)
                 and r["judge"]["usage"].get("cost") is not None]
        return sum(costs) if costs else None

    cost_a, cost_b = _cost(run_a), _cost(run_b)
    if cost_a is not None or cost_b is not None:
        summary["cost"] = {"a": cost_a, "b": cost_b}
    return summary


# ---------------------------------------------------------------------------
# Trivial baselines + the fixture-ranking decision rule
# ---------------------------------------------------------------------------

class AlwaysSafeJudge:
    """Baseline that never escalates: answers False to every noul question."""

    model = "baseline-always-safe"

    def evaluate(self, state, questions):
        return {q["name"]: {"value": False, "probability": 0.0, "confidence": 1.0}
                for q in questions if q["type"] == "noul"}


class AlwaysEscalateJudge:
    """Baseline that escalates everything: answers True to every noul question."""

    model = "baseline-always-escalate"

    def evaluate(self, state, questions):
        return {q["name"]: {"value": True, "probability": 1.0, "confidence": 1.0}
                for q in questions if q["type"] == "noul"}


class StaticRulesJudge:
    """Deterministic hand-written rules, no model: the 'obvious' baseline.

    Hazard when a confident, non-duplicate, sizable object sits on the trail;
    review whenever the decision is low-confidence or the state says so.
    """

    model = "baseline-static-rules"
    MIN_CONFIDENCE = 0.8
    MIN_SIZE_M = 0.3

    def _obs(self, state):
        return state.get("observation") or {}

    def _hazard(self, state):
        obs = self._obs(state)
        if obs.get("duplicate_of_existing_detection"):
            return False, obs.get("perception_confidence")
        conf = obs.get("perception_confidence")
        size = obs.get("size_m")
        on_trail = obs.get("position_relation") == "on_trail"
        confident = isinstance(conf, (int, float)) and conf >= self.MIN_CONFIDENCE
        sizable = isinstance(size, (int, float)) and size >= self.MIN_SIZE_M
        if on_trail and confident and (sizable or size is None):
            return True, conf
        return False, conf

    def evaluate(self, state, questions):
        hazard, conf = self._hazard(state)
        uncertain = not isinstance(conf, (int, float)) or conf < self.MIN_CONFIDENCE
        answers = {}
        for q in questions:
            if q["type"] != "noul":
                continue
            if q["name"] == "hazard_relevant":
                answers[q["name"]] = {"value": hazard, "probability": conf,
                                      "confidence": None if conf is None else conf}
            else:
                answers[q["name"]] = {"value": bool(uncertain or not hazard),
                                      "probability": None, "confidence": None}
        return answers


def trivial_baselines(cases: list[dict], *, repeats: int = DEFAULT_REPEATS) -> dict[str, Any]:
    """Run the three built-in trivial baselines through the same scorer.

    Known counts the acceptance tests assert: always-escalate has 0 false_safe
    and one false_escalation per reference-negative scored case. Baselines are
    deterministic, so they run with repeats=1 (repeats add nothing for them).
    """
    import tempfile

    results: dict[str, Any] = {}
    for name, judge in (("always_safe", AlwaysSafeJudge()),
                        ("always_escalate", AlwaysEscalateJudge()),
                        ("static_rules", StaticRulesJudge())):
        log_path = Path(tempfile.gettempdir()) / f"sentinel_eval_baseline_{name}.jsonl"
        metrics, _ = run_cases(cases, judge, log_path=log_path, repeats=1)
        results[name] = metrics
    return results


def decision_rule(judge_metrics: dict[str, Any], baselines: dict[str, Any]) -> dict[str, Any]:
    """Decision rule the fixture encodes: rank a judge only if it beats the
    always-escalate baseline on false-escalation at equal-or-better false-safe.

    A judge that merely ties always-escalate is indistinguishable from the
    trivial policy on this fixture -> ``ranks_fixture`` is False.
    """
    escalate = baselines["always_escalate"]
    hazard = judge_metrics["hazard"]
    base_hazard = escalate["hazard"]
    equal_or_better_fs = hazard["false_safe"] <= base_hazard["false_safe"]
    beats_fe = hazard["false_escalation"] < base_hazard["false_escalation"]
    return {
        "ranks_fixture": bool(equal_or_better_fs and beats_fe),
        "false_safe": {"judge": hazard["false_safe"], "always_escalate": base_hazard["false_safe"]},
        "false_escalation": {"judge": hazard["false_escalation"],
                             "always_escalate": base_hazard["false_escalation"]},
        "equal_or_better_false_safe": bool(equal_or_better_fs),
        "beats_false_escalation": bool(beats_fe),
    }
