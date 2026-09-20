"""An advisor instruction must not assert a premise the packet can contradict.

Found by an adversarial review of the Jev coverage diagnosis, 2026-09-20. The
`EVIDENCE_GATE` instruction introduced by P3a Task 7 opened with three factual
assertions — that the detection passed the score rules, that it lies inside the
approved plan, and that a task already exists at the same station. All three
are conditions the packet reports, and the advisor is consulted at *every*
`EVIDENCE_GATE` turn, not only at the duplicate turns that make it a judgment
point. On the v1 sweep fixture that instruction was false about duplicates on
24 of 24 turns and false about plan membership on 12 of 24.

That matters beyond tidiness: the shadow sweep exists to measure an advisor's
self-reported confidence, and a judge reasoning from a stated premise its own
evidence contradicts has every reason to report lower confidence. Any paid
sweep run against such an instruction measures the contradiction as much as
the advisor.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sentinel.coordinator.policies import INSTRUCTIONS

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/decisions/turns.v1.jsonl"

# Phrases that describe a condition the decision packet reports. Each may only
# appear inside a conditional clause, never as a bare statement of fact.
_FACT_DEPENDENT = (
    "already exists",
    "already open",
    "same station",
    "inside the approved plan",
    "passed the score",
    "near-tie",
    "ranked within",
)
_CONDITIONAL = ("if", "whether", "where", "may", "can", "unless", "when", "carry", "carries")

# The invariant binds only where the advisor is consulted REGARDLESS of the
# facts, because only there can a stated premise turn out false:
#   SELECT_PLAN   — reached whenever no plan is approved yet.
#   EVIDENCE_GATE — always offers two candidates, so `JevPolicy` always calls
#                   the judge, duplicate or not, in-plan or not.
# ASSIGN is excluded deliberately: it offers two candidates only on a near-tie,
# and on a clear gap it is a single-candidate RULE turn where the judge is
# never called. Its premise is therefore true whenever it is sent. See
# test_assign_instruction_is_only_sent_on_near_ties_so_may_state_it.
_UNCONDITIONALLY_CONSULTED = ("EVIDENCE_GATE", "SELECT_PLAN")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _renderings(point: str) -> list[tuple[str, str]]:
    """Every string a decision point can actually send, labelled. A plain
    instruction has one; a fact-derived one has a rendering per branch, and
    EVERY branch must satisfy the invariant -- checking only the default would
    miss exactly the wording that caused the defect."""
    value = INSTRUCTIONS[point]
    if not callable(value):
        return [("static", value)]
    return [
        ("no-duplicate", value({"detection_duplicates_open_task": False})),
        ("duplicate", value({"detection_duplicates_open_task": True})),
        ("facts-missing", value({})),
    ]


def _assert_no_bare_premise(point: str, branch: str, text: str) -> None:
    for sentence in _sentences(text):
        lowered = sentence.lower()
        mentioned = [p for p in _FACT_DEPENDENT if p in lowered]
        if not mentioned:
            continue
        if branch == "duplicate" and mentioned == ["already open"]:
            continue  # true by construction: this branch is only sent when it holds
        assert any(re.search(rf"\b{w}\b", lowered) for w in _CONDITIONAL), (
            f"{point} [{branch}]: this sentence states a packet-dependent condition as "
            f"fact, with no conditional marker: {sentence!r} (mentions {mentioned})")


@pytest.mark.parametrize("point", _UNCONDITIONALLY_CONSULTED)
def test_no_instruction_asserts_a_fact_dependent_premise(point: str):
    """Every fact-dependent phrase must sit inside a conditional clause, in
    every rendering the point can produce.

    One exception is principled: a fact-DERIVED instruction may state the very
    fact it branched on, because it is true by construction in that branch.
    The duplicate rendering is only ever sent when a duplicate exists."""
    assert point in INSTRUCTIONS
    for branch, text in _renderings(point):
        _assert_no_bare_premise(point, branch, text)


def test_evidence_gate_turns_the_advisor_sees_mostly_contradict_the_old_premises():
    """The regression this guards, stated in numbers rather than prose. If these
    counts ever change, the instruction's wording must be re-examined."""
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    gate = [r for r in rows if r["decision_point"] == "EVIDENCE_GATE"]
    duplicates = sum(1 for r in gate if r["packet"]["facts"].get("detection_duplicates_open_task"))
    inside = sum(1 for r in gate if r["packet"]["facts"].get("detection_inside_plan"))
    assert len(gate) == 24
    assert duplicates == 0, "no v1 EVIDENCE_GATE turn has a duplicate open task"
    assert inside == 12, "half the v1 EVIDENCE_GATE turns lie outside the approved plan"


def test_evidence_gate_instruction_still_offers_both_candidates_by_name():
    """Accuracy must not cost the advisor its options: both labels stay named
    in every branch."""
    for _branch, text in _renderings("EVIDENCE_GATE"):
        assert "'create_task'" in text and "'escalate'" in text


def test_evidence_gate_branches_describe_different_decisions():
    """The point of deriving from facts: a duplicate turn asks "same hazard or
    a second one?", a plain turn asks "does this warrant a task at all?". If
    the two renderings were identical the callable would be pointless."""
    by_branch = dict(_renderings("EVIDENCE_GATE"))
    assert by_branch["duplicate"] != by_branch["no-duplicate"]
    assert "second, distinct hazard" in by_branch["duplicate"]
    assert "same hazard seen again" in by_branch["duplicate"]
    assert "outside the approved plan" in by_branch["no-duplicate"]


def test_absent_facts_fall_back_to_the_non_duplicate_wording():
    """A packet without the duplicate fact must not claim one exists."""
    by_branch = dict(_renderings("EVIDENCE_GATE"))
    assert by_branch["facts-missing"] == by_branch["no-duplicate"]


def test_assign_instruction_is_only_sent_on_near_ties_so_may_state_it():
    """Contrast case, so the rule above is not read as "never state anything".
    ASSIGN offers two candidates only on a near-tie; on a clear gap it is a
    single-candidate RULE turn and `JevPolicy` never calls the judge. So its
    instruction may state the near-tie as fact — it is always true when sent."""
    assert "near-tie" in INSTRUCTIONS["ASSIGN"]
