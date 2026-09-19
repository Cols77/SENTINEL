"""Shadow replay harness: score a judge against reference-labeled cases.

Policy-relevant safety rule: a false_safe (reference True judged False) is the
MOST SERIOUS outcome and is counted explicitly, never folded into a single
accuracy number.
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


def run_cases(
    cases: Iterable[dict],
    judge: BoundedJudge,
    *,
    log_path: Path,
) -> tuple[dict[str, int], Path]:
    """Replay reference-labeled cases through the judge (shadow-only).

    All QUESTION_SET questions go to the judge in ONE batched evaluate() call
    per case (the API evaluates them in parallel). Appends one JSONL record
    per case with case_id/state/reference/judge.model/latency_ms/answers —
    latency_ms is measured once per batch. Every answer is logged, including
    confidence where the judge provides it.
    """
    metrics = {"total": 0, "false_safe": 0, "false_escalation": 0, "unavailable": 0}
    model = getattr(judge, "model", "static")
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as log:
        for case in cases:
            metrics["total"] += 1
            start = time.perf_counter()
            try:
                answers: dict[str, dict[str, Any]] = judge.evaluate(case["state"], QUESTION_SET)
            except JudgeUnavailable:
                metrics["unavailable"] += 1
                answers = {"error": {"unavailable": True}}
                log.write(json.dumps({
                    "case_id": case["case_id"],
                    "state": case["state"],
                    "reference": case["reference"],
                    "judge": {"model": model},
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "answers": answers,
                }) + "\n")
                continue
            latency_ms = int((time.perf_counter() - start) * 1000)
            reference = case["reference"]
            if isinstance(reference.get("hazard_relevant"), bool):
                result = answers["hazard_relevant"]["value"]
                if reference["hazard_relevant"] != result:
                    # reference True judged False is a false safe -> most serious.
                    if reference["hazard_relevant"]:
                        metrics["false_safe"] += 1
                    else:
                        metrics["false_escalation"] += 1
            log.write(json.dumps({
                "case_id": case["case_id"],
                "state": case["state"],
                "reference": reference,
                "judge": {"model": model},
                "latency_ms": latency_ms,
                "answers": answers,
            }) + "\n")
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
            if case_id in records:
                raise ValueError(f"duplicate case_id in run log {path}: {case_id!r}")
            records[case_id] = rec
    return records


def _count_reference_outcomes(records: dict[str, dict]) -> dict[str, int]:
    """Recompute false_safe/false_escalation from each record's reference labels.

    Cases without a boolean reference for hazard_relevant are simply not scored
    (they still participate in agreement comparison).
    """
    counts = {"false_safe": 0, "false_escalation": 0}
    for rec in records.values():
        ref = rec.get("reference", {})
        if not isinstance(ref.get("hazard_relevant"), bool):
            continue
        try:
            judged = rec["answers"]["hazard_relevant"]["value"]
        except (KeyError, TypeError):
            continue  # unavailable/error records carry no scorable answer
        if ref["hazard_relevant"] != judged:
            if ref["hazard_relevant"]:
                counts["false_safe"] += 1
            else:
                counts["false_escalation"] += 1
    return counts


def compare_runs(file_a, file_b) -> dict[str, Any]:
    """Compare two run JSONL logs produced by run_cases.

    Returns per-case agreement, per-question agreement, false_safe /
    false_escalation per run with deltas (b - a), mean latency delta, and —
    when usage.cost is present in the judge metadata — cost totals per run.
    """
    run_a = _load_run_records(file_a)
    run_b = _load_run_records(file_b)
    common = sorted(set(run_a) & set(run_b))
    if set(run_a) != set(run_b):
        only_a = sorted(set(run_a) - set(run_b))
        only_b = sorted(set(run_b) - set(run_a))
        raise ValueError(f"case sets differ (a-only: {only_a}, b-only: {only_b})")

    per_case_agreement: dict[str, bool] = {}
    question_totals: dict[str, list[int]] = {}
    latencies: dict[str, list[float]] = {"a": [], "b": []}
    for case_id in common:
        rec_a, rec_b = run_a[case_id], run_b[case_id]
        latencies["a"].append(rec_a["latency_ms"])
        latencies["b"].append(rec_b["latency_ms"])
        ans_a, ans_b = rec_a.get("answers", {}), rec_b.get("answers", {})
        if ans_a != ans_b:
            per_case_agreement[case_id] = False
        elif not ans_a:
            per_case_agreement[case_id] = False  # both unavailable: not comparable
        else:
            per_case_agreement[case_id] = True
        for q in set(ans_a) | set(ans_b):
            totals = question_totals.setdefault(q, [0, 0])
            if q in ans_a and q in ans_b:
                totals[0] += 1
                if ans_a[q] == ans_b[q]:
                    totals[1] += 1
            elif q in ans_a or q in ans_b:
                totals[0] += 1  # mismatch: answer present in only one run

    summary: dict[str, Any] = {
        "case_count": len(common),
        "per_case_agreement": per_case_agreement,
        "agreement_rate": (sum(per_case_agreement.values()) / len(common)) if common else 0.0,
        "per_question_agreement": {
            q: (hits / total) if total else 0.0
            for q, (total, hits) in sorted(question_totals.items())
        },
    }
    outcomes = {"a": _count_reference_outcomes(run_a), "b": _count_reference_outcomes(run_b)}
    for key in ("false_safe", "false_escalation"):
        summary[key] = {
            "a": outcomes["a"][key], "b": outcomes["b"][key],
            "delta": outcomes["b"][key] - outcomes["a"][key],
        }
    mean_a = sum(latencies["a"]) / len(latencies["a"]) if latencies["a"] else 0.0
    mean_b = sum(latencies["b"]) / len(latencies["b"]) if latencies["b"] else 0.0
    summary["mean_latency_ms"] = {"a": mean_a, "b": mean_b, "delta": mean_b - mean_a}

    def _cost(records: dict[str, dict]) -> float | None:
        costs = [r["judge"]["usage"].get("cost") for r in records.values()
                 if isinstance(r.get("judge", {}).get("usage"), dict)
                 and r["judge"]["usage"].get("cost") is not None]
        return sum(costs) if costs else None

    cost_a, cost_b = _cost(run_a), _cost(run_b)
    if cost_a is not None or cost_b is not None:
        summary["cost"] = {"a": cost_a, "b": cost_b}
    return summary
