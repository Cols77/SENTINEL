"""Shadow replay harness: score a judge against reference-labeled cases.

Policy-relevant safety rule: a false_safe (reference True judged False) is the
MOST SERIOUS outcome and is counted explicitly, never folded into a single
accuracy number.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from sentinel.judgment import BoundedJudge, JudgeUnavailable


def run_cases(
    cases: Iterable[dict],
    judge: BoundedJudge,
    *,
    boolean_names: Sequence[str],
    log_path: Path,
) -> tuple[dict[str, int], Path]:
    """Replay reference-labeled cases through the judge (shadow-only).

    Returns (metrics, log_path). Appends one JSONL record per case with
    case_id/state/reference/judge.model/latency_ms/answers.
    """
    metrics = {"total": 0, "false_safe": 0, "false_escalation": 0, "unavailable": 0}
    model = getattr(judge, "model", "static")
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as log:
        for case in cases:
            metrics["total"] += 1
            answers: dict[str, dict[str, Any]] = {}
            for name in boolean_names:
                start = time.perf_counter()
                latency_ms = 0
                try:
                    result = judge.boolean(case["state"], f"Answer {name}.", name=name)
                except JudgeUnavailable:
                    metrics["unavailable"] += 1
                    answers[name] = {"error": "unavailable"}
                    continue
                latency_ms = int((time.perf_counter() - start) * 1000)
                answers[name] = result
                reference = case["reference"].get(name)
                if isinstance(reference, bool) and reference != result["value"]:
                    # reference True judged False is a false safe -> most serious.
                    if reference:
                        metrics["false_safe"] += 1
                    else:
                        metrics["false_escalation"] += 1
            log.write(json.dumps({
                "case_id": case["case_id"],
                "state": case["state"],
                "reference": case["reference"],
                "judge": {"model": model},
                "latency_ms": latency_ms,
                "answers": answers,
            }) + "\n")
    return metrics, log_path
