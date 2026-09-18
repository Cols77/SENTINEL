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
        "criteria": {"true": "operational trail-opening hazard requiring action",
                     "false": "not operationally relevant"},
    },
    {
        "name": "priority",
        "type": "choice",
        "instruction": "What priority applies to this observation?",
        "criteria": {"ignore": "no action warranted", "low": "minor, monitor",
                     "medium": "should be inspected soon", "high": "blocks trail or immediate risk"},
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
