"""`judge_label` separates what the JUDGE said from what the POLICY did.

The 2026-09-20 sweep reported `escalation_rate: 1.0` for Jev and it was read,
including in a committed comparison artifact, as "Jev abstains on every turn".
It did not say that. `JevPolicy` records its own post-banding action, which for
any non-HIGH answer is the escalate candidate — so a MID-confidence *correct*
pick was recorded as an escalation, and the judge's actual behaviour on those
144 calls was never captured.

These tests pin the distinction, and in particular the case that produced the
false conclusion: a judge that picks correctly every time but never confidently.
"""
from __future__ import annotations

from sentinel.coordinator.policies import Decision
from sentinel.coordinator.shadow import _advisor_entry
from sentinel.shadow_eval import score

ESCALATE = "escalate"


def _row(turn_id: str, *, label: str, judge_label: str | None, band: str) -> dict:
    """One judgment audit record in the shape `score()` consumes."""
    return {
        "record": "judgment", "turn_id": turn_id, "decision_point": "SELECT_PLAN",
        "advisors": {"jev": {"label": label, "judge_label": judge_label, "band": band,
                             "confidence": 0.9 if band == "HIGH" else 0.6,
                             "model": "jev-test", "latency_ms": 10, "error": None}},
    }


def _scored(rows: list[dict], truth_label: str) -> dict:
    """Score `rows` against a truth map keyed by turn_id, as `score()` expects."""
    truth = {row["turn_id"]: truth_label for row in rows}
    return score(rows, truth)["jev"]["SELECT_PLAN"]


def test_a_judge_that_is_always_right_but_never_confident_is_no_longer_read_as_abstaining():
    """The exact shape that produced the false conclusion. Every pick is correct;
    none is HIGH. The policy escalates every time — correctly, that is its
    fail-closed design — but the judge never abstained."""
    rows = [_row(f"t{i}", label=ESCALATE, judge_label="PLAN_A", band="MID") for i in range(10)]
    result = _scored(rows, "PLAN_A")

    # Policy view: escalated every turn, and its action never matched truth.
    assert result["escalation_rate"] == 1.0
    assert result["agreement"] == 0.0
    # Judge view: never escalated, and was right every time.
    assert result["judge_escalation_rate"] == 0.0
    assert result["judge_agreement"] == 1.0
    assert result["judge_labels_recorded"] == 10
    # And the promotion-relevant numbers are untouched: no HIGH picks, so no
    # coverage and no false acts. The judge being right does not promote it.
    assert result["coverage_high"] == 0.0
    assert result["false_act"] == 0


def test_a_judge_that_genuinely_abstains_is_still_reported_as_abstaining():
    """The contrast case — otherwise the new metric would just flatter Jev."""
    rows = [_row(f"t{i}", label=ESCALATE, judge_label=ESCALATE, band="MID") for i in range(10)]
    result = _scored(rows, "PLAN_A")
    assert result["escalation_rate"] == 1.0
    assert result["judge_escalation_rate"] == 1.0
    assert result["judge_agreement"] == 0.0


def test_metrics_are_none_not_zero_when_no_row_carries_a_judge_label():
    """A report rendered from a sweep predating the field must say "not
    recorded", never silently claim the judge never escalated."""
    rows = [_row(f"t{i}", label=ESCALATE, judge_label=None, band="MID") for i in range(5)]
    result = _scored(rows, "PLAN_A")
    assert result["judge_labels_recorded"] == 0
    assert result["judge_agreement"] is None
    assert result["judge_escalation_rate"] is None
    assert result["escalation_rate"] == 1.0          # the policy view still works


def test_high_band_picks_agree_across_both_views():
    """When the judge is confident, policy and judge coincide by construction —
    the fallback never fires — so the two views must not diverge."""
    rows = [_row(f"t{i}", label="PLAN_A", judge_label="PLAN_A", band="HIGH") for i in range(10)]
    result = _scored(rows, "PLAN_A")
    assert result["agreement"] == result["judge_agreement"] == 1.0
    assert result["escalation_rate"] == result["judge_escalation_rate"] == 0.0
    assert result["coverage_high"] == 1.0


def test_advisor_entry_carries_the_judge_label_through_to_the_audit_record():
    decision = Decision(chooser="OPERATOR", label=ESCALATE, confidence=0.6, band="MID",
                        legal_labels=("PLAN_A", ESCALATE), model="jev-test", error=None,
                        latency_ms=12, judge_label="PLAN_A")
    entry = _advisor_entry(decision, fallback_latency_ms=99, error=None)
    assert entry["label"] == ESCALATE and entry["judge_label"] == "PLAN_A"


def test_a_failed_advisor_records_no_judge_label():
    entry = _advisor_entry(None, fallback_latency_ms=99, error="advisor timed out")
    assert entry["judge_label"] is None and entry["error"] == "advisor timed out"


def test_decision_defaults_judge_label_to_none_for_rule_turns():
    """A RULE turn consults no judge, so there is nothing to record."""
    decision = Decision(chooser="RULE", label="create_task", confidence=None, band=None,
                        legal_labels=("create_task",), model=None, error=None, latency_ms=None)
    assert decision.judge_label is None
