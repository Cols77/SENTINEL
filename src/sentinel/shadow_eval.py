"""Shadow advisor evaluation (P2 Task 5): score advisor picks recorded by
``ShadowLadder`` (Task 2) against ground truth, without ever letting the
result feed back into what is executed.

``score`` consumes the same ``judgment``-record shape the audit sink writes
-- ``{"decision_point", "advisors": {name: {"label", "band", "error",
"latency_ms", ...}}, ...}`` -- plus, per record, either a ``turn_id`` (the
offline fixture sweep, Task 5 Step 4) or bare ``run_id``/``state_version``/
``decision_point`` fields (a live audit file scored against operator
choices, Task 6). The same scorer works for both: it looks the record's
truth up by ``turn_id`` first, then by the ``(run_id, state_version,
decision_point)`` tuple, and simply drops any record it cannot match.

This module never calls a judge and never touches the gateway -- it is
read-only arithmetic over already-recorded picks. It has no authority over
what ran; see ``REPORT_HEADER``.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Sequence

from .coordinator.legal_tools import ESCALATE_LABEL

REPORT_HEADER = (
    "Shadow only. No advisor has authority. Promotion criteria live in P3 "
    "and require n ≥ 60 HIGH-band picks per decision point."
)

# 95% two-sided normal quantile, used for every Wilson interval below.
_Z95 = 1.959963985

HIGH_BAND = "HIGH"


def wilson_lower_95(successes: int, n: int) -> float:
    """The lower bound of the 95% Wilson score interval for ``successes``
    out of ``n`` Bernoulli trials. Unlike a plain normal approximation, this
    stays well-behaved (and honestly conservative) at small ``n`` and at
    ``successes == n`` -- exactly the small-sample regime a shadow sweep
    reports in before it has 60 HIGH-band picks."""
    if n <= 0:
        return 0.0
    phat = successes / n
    z2 = _Z95 * _Z95
    denom = 1 + z2 / n
    center = phat + z2 / (2 * n)
    margin = _Z95 * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)
    return (center - margin) / denom


def _truth_for(record: dict[str, Any], truth: Mapping[Any, str]) -> str | None:
    turn_id = record.get("turn_id")
    if turn_id is not None and turn_id in truth:
        return truth[turn_id]
    key = (record.get("run_id"), record.get("state_version"), record.get("decision_point"))
    return truth.get(key)


def _turn_key(record: dict[str, Any]) -> Any:
    turn_id = record.get("turn_id")
    if turn_id is not None:
        return turn_id
    return (record.get("run_id"), record.get("state_version"), record.get("decision_point"))


def _score_group(rows: Sequence[tuple[dict[str, Any], dict[str, Any], str]]) -> dict[str, Any]:
    n = len(rows)
    correct = 0
    escalate_count = 0
    error_count = 0
    false_act = 0
    high_total = 0
    high_correct = 0
    latencies: list[float] = []
    labels_by_turn: dict[Any, list[Any]] = {}
    # Judge-level counters, kept strictly separate from the policy-level ones
    # above. ``label`` is what the POLICY did; ``judge_label`` is what the JUDGE
    # said. They differ on every non-HIGH answer, and scoring only the former
    # is what made the 2026-09-20 sweep read as "Jev abstained on every turn"
    # when in truth its own picks were never recorded.
    judge_n = 0
    judge_correct = 0
    judge_escalate = 0

    for record, entry, truth_label in rows:
        label = entry.get("label")
        band = entry.get("band")
        error = entry.get("error")
        latency_ms = entry.get("latency_ms")
        judge_label = entry.get("judge_label")

        if error is not None:
            error_count += 1
        if label == truth_label:
            correct += 1
        if label == ESCALATE_LABEL:
            escalate_count += 1
        if judge_label is not None:
            judge_n += 1
            if judge_label == truth_label:
                judge_correct += 1
            if judge_label == ESCALATE_LABEL:
                judge_escalate += 1
        if band == HIGH_BAND:
            high_total += 1
            if label == truth_label:
                high_correct += 1
            if label != ESCALATE_LABEL and truth_label == ESCALATE_LABEL:
                false_act += 1
        if latency_ms is not None:
            latencies.append(latency_ms)

        labels_by_turn.setdefault(_turn_key(record), []).append(label)

    flipped_turns = sum(1 for labels in labels_by_turn.values() if len(set(labels)) > 1)
    distinct_turns = len(labels_by_turn)

    return {
        "n": n,
        "agreement": (correct / n) if n else 0.0,
        "coverage_high": (high_total / n) if n else 0.0,
        "selective_accuracy": (high_correct / high_total) if high_total else 0.0,
        "false_act": false_act,
        # POLICY-level: how often the policy escalated, and how often its final
        # action matched the truth label. Operationally real -- this is the
        # operator load -- but it is NOT a measurement of the judge.
        "escalation_rate": (escalate_count / n) if n else 0.0,
        # JUDGE-level: the same two questions asked of the judge's own pick.
        # ``None`` rather than 0.0 when no row carries a judge_label, so a
        # report rendered from a sweep predating that field says "not
        # recorded" instead of silently claiming the judge never escalated.
        "judge_labels_recorded": judge_n,
        "judge_agreement": (judge_correct / judge_n) if judge_n else None,
        "judge_escalation_rate": (judge_escalate / judge_n) if judge_n else None,
        "error_rate": (error_count / n) if n else 0.0,
        "latency_ms_median": statistics.median(latencies) if latencies else None,
        "stability": (1 - flipped_turns / distinct_turns) if distinct_turns else 1.0,
        "wilson_lower_95": wilson_lower_95(high_correct, high_total),
    }


def score(records: list[dict[str, Any]], truth: Mapping[Any, str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Score every advisor's recorded picks, per decision point, against
    ``truth``. Returns ``{advisor_name: {decision_point: {metric: value}}}``.

    Only ``"judgment"``-shaped records (those carrying an ``"advisors"``
    map) are considered; anything else (e.g. a ``"rule"`` audit record) is
    ignored. A record whose truth cannot be found (neither by ``turn_id``
    nor by the ``(run_id, state_version, decision_point)`` tuple) is
    excluded from every advisor's counts for that record, rather than
    raising -- a partially-labelled sweep still scores whatever is labelled.
    """
    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], str]]] = {}
    for record in records:
        advisors = record.get("advisors")
        if not advisors:
            continue
        truth_label = _truth_for(record, truth)
        if truth_label is None:
            continue
        decision_point = record["decision_point"]
        for advisor_name, entry in advisors.items():
            grouped.setdefault((advisor_name, decision_point), []).append((record, entry, truth_label))

    report: dict[str, dict[str, dict[str, Any]]] = {}
    for (advisor_name, decision_point), rows in grouped.items():
        report.setdefault(advisor_name, {})[decision_point] = _score_group(rows)
    return report


__all__ = ["REPORT_HEADER", "score", "wilson_lower_95"]
