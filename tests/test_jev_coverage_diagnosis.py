"""Regression pins for the Astra follow-up diagnosis: why Jev's live
SELECT_PLAN/EVIDENCE_GATE shadow sweep (``artifacts/decisions/live-shadow-jev.md``)
never reaches HIGH-band confidence (``coverage_high`` 0.0 / 0.0139 across 144
JevPolicy calls, ``act_at=0.80``).

Full write-up: ``artifacts/decisions/jev-coverage-diagnosis.md``. Verdict:
THRESHOLD MISMATCH (C) -- the confidence-reading pipeline is verified correct
(``JevPolicy.decide`` / ``JevJudge.choice`` are separately unit-tested in
``tests/coordinator/test_policies.py`` and ``tests/test_judgment.py``), and
Jev's own ``confidence`` field is proven, from real committed live output, to
be populated, to vary widely, and to clear 0.80 on a comparable ``choice``/
``score`` question shape -- so "the field never populates" (A) is false. But
on the SELECT_PLAN/EVIDENCE_GATE 2-3-option act-or-escalate task shape it
essentially never does (0/72, 1/72), including on EVIDENCE_GATE turns that are
unambiguous by the deterministic rule. This module has NO access to the raw
per-call SELECT_PLAN/EVIDENCE_GATE confidence values -- that JSONL was never
committed for the sweep the two .md reports summarize (the raw-row logging in
``scripts/shadow_sweep.py`` landed in 0275218, after that sweep ran) -- so
these tests pin what IS committed: the real live confidence distribution on
the closest comparable fixture, and the sweep's own reported aggregates, so a
future change to either is caught rather than silently re-litigated.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_JEV_V2 = REPO_ROOT / "artifacts" / "jev" / "v2-live-jev.jsonl"
SHADOW_JEV_MD = REPO_ROOT / "artifacts" / "decisions" / "live-shadow-jev.md"
SHADOW_LLM_MD = REPO_ROOT / "artifacts" / "decisions" / "live-shadow-llm.md"


def _load_v2_live_jev() -> list[dict]:
    records = [
        json.loads(line)
        for line in LIVE_JEV_V2.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records, "artifacts/jev/v2-live-jev.jsonl must be a non-empty committed live run"
    return records


def _confidences(records: list[dict], question: str) -> list[float]:
    values = []
    for record in records:
        answer = record.get("answers", {}).get(question)
        if isinstance(answer, dict):
            confidence = answer.get("confidence")
            if isinstance(confidence, (int, float)):
                values.append(confidence)
    return values


def test_jev_choice_confidence_field_is_populated_and_varies():
    """Falsifies verdict (A) for the general adapter path: on real, committed
    live Jev output (a ``choice`` question, 21 cases x 3 repeats), the
    ``confidence`` field returned by the /api/alpha/decisions endpoint is
    present on every record and spans a wide range -- it is not stuck at
    None (which would silently force every caller onto the probabilities
    fallback) and not stuck near zero."""
    records = _load_v2_live_jev()
    confidences = _confidences(records, "priority")
    assert len(confidences) == len(records) == 63, \
        "every live 'priority' choice answer must carry a confidence value"
    assert min(confidences) < 0.30, "the field must show real low-end variation, not a floor"
    assert max(confidences) >= 0.90, "pin the observed ceiling: Jev's own confidence field can clear act_at (0.80)"


def test_jev_choice_confidence_clears_act_at_on_a_comparable_question_shape():
    """Pins the observed HIGH-band (>=0.80) rate on the closest committed
    comparable to SELECT_PLAN/EVIDENCE_GATE: a live Jev 'choice'/'score'
    question over structured state. If this ever drops to ~0%, the
    SELECT_PLAN/EVIDENCE_GATE sweep's 0% stops looking like a task-shape
    effect and starts looking like a regression in the adapter itself."""
    records = _load_v2_live_jev()
    priority = _confidences(records, "priority")
    scored = _confidences(records, "priority_scored")

    high_priority = sum(1 for c in priority if c >= 0.80)
    high_scored = sum(1 for c in scored if c >= 0.80)

    # Observed on 2026-09-19/20 committed live data: 12/63 and 19/63.
    assert high_priority >= 8, "Jev must clear act_at on a nontrivial share of 'priority' picks"
    assert high_scored >= 12, "Jev must clear act_at on a nontrivial share of 'priority_scored' picks"


def _parse_metric(report_text: str, section: str, key: str) -> float:
    section_text = report_text.split(f"## {section}", 1)[1]
    section_text = section_text.split("## ", 1)[0]
    match = re.search(rf"`{re.escape(key)}`:\s*([0-9.]+)", section_text)
    assert match, f"metric {key!r} not found under section {section!r}"
    return float(match.group(1))


def test_live_shadow_jev_coverage_high_ceiling_is_pinned():
    """Pins the committed SELECT_PLAN/EVIDENCE_GATE sweep's own reported
    numbers (``artifacts/decisions/live-shadow-jev.md``): Jev reaches
    HIGH-band confidence essentially never on this task shape (0/72
    EVIDENCE_GATE picks, 1/72 SELECT_PLAN picks, out of 144 total calls),
    even though the previous test proves the same field clears 0.80 on a
    comparable question elsewhere. A future re-run that changes this
    materially should update this test AND
    ``artifacts/decisions/jev-coverage-diagnosis.md`` together, not silently
    drift past it."""
    text = SHADOW_JEV_MD.read_text(encoding="utf-8")
    assert _parse_metric(text, "EVIDENCE_GATE", "coverage_high") == 0.0
    assert _parse_metric(text, "EVIDENCE_GATE", "escalation_rate") == 1.0
    select_plan_coverage = _parse_metric(text, "SELECT_PLAN", "coverage_high")
    assert 0.0 < select_plan_coverage < 0.02, "exactly 1 of 72 SELECT_PLAN picks clears act_at"
    assert _parse_metric(text, "SELECT_PLAN", "false_act") == 0.0


def test_live_shadow_llm_coverage_high_is_the_contrasting_baseline():
    """The LLM arm over the SAME 144 turns clears HIGH-band far more often
    (coverage_high 0.958 / 0.639) -- proving the sweep/scoring plumbing
    (``sentinel.shadow_eval.score``, the HIGH band comparison) is not itself
    suppressing every advisor's coverage; only Jev's is near zero on this
    fixture."""
    text = SHADOW_LLM_MD.read_text(encoding="utf-8")
    assert _parse_metric(text, "EVIDENCE_GATE", "coverage_high") > 0.9
    assert _parse_metric(text, "SELECT_PLAN", "coverage_high") > 0.5


def test_evidence_gate_fixture_includes_unambiguous_create_task_turns():
    """Pins a fact the diagnosis relies on: EVIDENCE_GATE's fixture is not
    uniformly ambiguous. 6 of its 24 turns are unambiguous by the
    deterministic rule (score high/very_high, inside the plan, no
    duplicate -- a textbook create_task) -- yet Jev's live sweep still
    reported 0/72 HIGH-band EVIDENCE_GATE picks, i.e. not even the easy
    turns cleared act_at. That is the strongest evidence for a task-shape
    ceiling (verdict C) rather than uniformly-warranted low confidence
    (verdict B)."""
    turns = [
        json.loads(line)
        for line in (REPO_ROOT / "fixtures" / "decisions" / "turns.v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    evidence_gate = [t for t in turns if t["decision_point"] == "EVIDENCE_GATE"]
    assert len(evidence_gate) == 24
    unambiguous = [
        t for t in evidence_gate
        if t["label"] == "create_task"
        and t["packet"]["facts"]["detection_score_band"] in ("high", "very_high")
        and t["packet"]["facts"]["detection_inside_plan"] is True
        and t["packet"]["facts"]["detection_duplicates_open_task"] is False
    ]
    assert len(unambiguous) == 6
