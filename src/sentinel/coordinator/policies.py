"""RULE and Jev policies over described legal candidates (D9, D11).

``RulePolicy`` is the F executor: it acts on the legal set's single
candidate, or on a deterministic ``rule_choice`` where one has been computed
(Task A: EVIDENCE_GATE), and otherwise hands the decision to the operator
(SELECT_PLAN, which has no rule_choice). ``F`` plus this policy is the demo
architecture -- Jev and the LLM run only as shadow advisors elsewhere (see
the plan's Decisions section).

``JevPolicy`` is the fail-closed bridge for ``E`` (the P3 follow-up): at a
judgment point with more than one candidate it asks Jev to choose among the
described candidates, and only a HIGH-confidence, non-escalate pick is
acted on directly. Anything else -- MID/LOW confidence, or the judge itself
failing -- escalates to the operator instead of guessing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from .legal_tools import ADVISORY_POINTS, ESCALATE_LABEL, needs_judgment
from .policy import PolicyContext, one_call

def _evidence_gate_instruction(facts: Mapping[str, Any]) -> str:
    """``EVIDENCE_GATE`` is reached in two genuinely different situations, and
    no single static sentence is both true and useful for each.

    The advisor is consulted at EVERY ``EVIDENCE_GATE`` turn -- the point is in
    ``ADVISORY_POINTS`` and always offers two candidates -- not only at the
    duplicate turns that make it a judgment point. A static instruction
    asserting "a task already exists at the same station" was therefore false
    on all 24 turns of the v1 sweep fixture, and "lies inside the approved
    plan" false on 12 of them. Asking a judge to reason from a premise its own
    evidence contradicts corrupts its answer and its self-reported confidence
    -- the quantity the shadow sweep exists to measure.

    The P1/P2 session, which wrote the original wording, proposed removing all
    claims and stating only the decision. That is certainly safe, but it also
    removes the escalation criteria, leaving the judge to infer from two thin
    candidate descriptions what "escalate" is for -- which would degrade the
    same measurement by a different route. Deriving the wording from the facts
    keeps the guidance and makes every claim true by construction. The
    conditional-prose form that hardened into an assertion is gone either way,
    and `tests/coordinator/test_instruction_premises.py` now fails if it
    returns."""
    if facts.get("detection_duplicates_open_task"):
        return ("A task is already open at this detection's station. Choose 'create_task' if this is a "
                "second, distinct hazard that needs its own task. Choose 'escalate' if it is the same "
                "hazard seen again, or the evidence cannot tell the two apart.")
    return ("Decide whether this detection should become a patrol task. Choose 'create_task' if it "
            "warrants one on its own evidence. Choose 'escalate' if it needs operator review -- for "
            "instance a station outside the approved plan, a marginal score, or evidence you cannot "
            "read confidently.")


# Values are either a plain string, or a callable taking the packet's facts
# (see `_evidence_gate_instruction`). `JevPolicy.decide` resolves both.
INSTRUCTIONS: dict[str, Any] = {
    "SELECT_PLAN": "Choose the survey plan that fully satisfies the operator request with the fewest stations and "
                   "least verification burden. Choose 'escalate' if no plan fits or the request is contradictory.",
    "EVIDENCE_GATE": _evidence_gate_instruction,
    "ASSIGN": "Two or more patrollers are ranked within a near-tie. Choose 'assign_rank_1' if the top-ranked "
              "patroller is an acceptable assignment. Choose 'escalate' if the operator should pick.",
}


@dataclass(frozen=True)
class Decision:
    """Audit shape for one policy decision, regardless of which policy made
    it. ``chooser`` is ``RULE`` (a single candidate, or a deterministic
    ``rule_choice`` -- no judgment needed either way), ``JEV`` (Jev picked
    and acted, including a deliberate escalate) or ``OPERATOR`` (escalated
    -- at a judgment point with no rule_choice, by low confidence, or by
    judge failure)."""

    chooser: str
    label: str | None
    confidence: float | None
    band: str | None
    legal_labels: tuple[str, ...]
    model: str | None
    error: str | None
    latency_ms: int | None
    # The judge's OWN pick, before banding -- `answer["value"]`. Distinct from
    # ``label``, which is what the policy went on to do: for any non-HIGH answer
    # that is the escalate candidate, not what the judge said.
    #
    # Without this the two are conflated, and that conflation produced a real
    # false conclusion. The 2026-09-20 sweep reported escalation_rate 1.0 for
    # Jev and it was read as "Jev abstained on every turn"; in fact a
    # MID-confidence *correct* pick was recorded as an escalation, so the
    # judge's actual behaviour on those 144 calls was never captured at all.
    # ``None`` where no judge was consulted (a RULE turn) or where the judge
    # raised before answering.
    judge_label: str | None = None


class RulePolicy:
    """Acts on the legal set's single candidate, or on ``legal.rule_choice``
    when a deterministic rule has computed one (Task A: EVIDENCE_GATE) --
    both are labelled chooser ``RULE``. Only where neither applies (a
    judgment point with 2+ candidates and no rule_choice -- SELECT_PLAN)
    does it escalate, labelling the decision ``OPERATOR``. This is the F
    executor: rules plus operator."""

    def __init__(self) -> None:
        self.last: Decision | None = None

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        legal = context.legal
        if len(legal.candidates) == 1:
            candidate = legal.candidates[0]
            self.last = Decision(chooser="RULE", label=candidate.label, confidence=None, band=None,
                                  legal_labels=legal.labels, model=None, error=None, latency_ms=None)
            return one_call(request, candidate.tool_name, candidate.arguments)
        if legal.rule_choice is not None:
            candidate = legal.by_label(legal.rule_choice)
            self.last = Decision(chooser="RULE", label=candidate.label, confidence=None, band=None,
                                  legal_labels=legal.labels, model=None, error=None, latency_ms=None)
            return one_call(request, candidate.tool_name, candidate.arguments)
        candidate = legal.by_label(ESCALATE_LABEL)
        self.last = Decision(chooser="OPERATOR", label=candidate.label, confidence=None, band=None,
                              legal_labels=legal.labels, model=None, error=None, latency_ms=None)
        return one_call(request, candidate.tool_name, candidate.arguments)


class JevPolicy:
    """Calls Jev only at a judgment point with more than one candidate.
    HIGH confidence (>= ``act_at``) acts; MID (>= ``review_below``) and LOW
    confidence escalate to the operator instead. A pick of ``escalate`` is
    honoured in any band -- that is Jev's own deliberate decision, not a
    confidence fallback. ``JudgeUnavailable`` or any other exception from
    the judge fails closed to the operator, with the error recorded."""

    def __init__(self, judge: Any, *, act_at: float = 0.80, review_below: float = 0.50,
                 instructions: dict[str, Any] = INSTRUCTIONS) -> None:
        self.judge = judge
        self.act_at = act_at
        self.review_below = review_below
        self.instructions = instructions
        self.last: Decision | None = None

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        legal = context.legal
        if len(legal.candidates) == 1:
            candidate = legal.candidates[0]
            self.last = Decision(chooser="RULE", label=candidate.label, confidence=None, band=None,
                                  legal_labels=legal.labels, model=None, error=None, latency_ms=None)
            return one_call(request, candidate.tool_name, candidate.arguments)

        point = legal.decision_point
        assert point in ADVISORY_POINTS or needs_judgment(legal), \
            f"legal set at {point!r} has {len(legal.candidates)} candidates " \
            "but is neither a declared advisory point nor an unsettled judgment turn"
        criteria = {candidate.label: candidate.description for candidate in legal.candidates}
        packet = context.state.to_packet()
        # An instruction may be a plain string, or a callable taking the
        # packet's facts. The callable form exists so a decision point reached
        # in genuinely different situations can be described accurately in each
        # without asserting anything false in the others -- see
        # `_evidence_gate_instruction`. A custom `instructions` mapping of plain
        # strings keeps working unchanged.
        instruction = self.instructions[point]
        if callable(instruction):
            instruction = instruction(packet.get("facts", {}))
        model = getattr(self.judge, "_model", None)

        start = time.perf_counter()
        try:
            answer = self.judge.choice(packet, instruction, criteria=criteria, name=point.lower())
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            escalate = legal.by_label(ESCALATE_LABEL)
            self.last = Decision(chooser="OPERATOR", label=escalate.label, confidence=None, band=None,
                                  legal_labels=legal.labels, model=model, error=str(exc), latency_ms=latency_ms)
            return one_call(request, escalate.tool_name, escalate.arguments)
        latency_ms = int((time.perf_counter() - start) * 1000)

        label = answer["value"]
        confidence = answer.get("confidence")
        if confidence is None:
            confidence = answer["probabilities"][label]
        band = "HIGH" if confidence >= self.act_at else "MID" if confidence >= self.review_below else "LOW"

        if label == ESCALATE_LABEL:
            chooser, candidate = "JEV", legal.by_label(ESCALATE_LABEL)
        elif band == "HIGH":
            chooser, candidate = "JEV", legal.by_label(label)
        else:
            chooser, candidate = "OPERATOR", legal.by_label(ESCALATE_LABEL)

        self.last = Decision(chooser=chooser, label=candidate.label, confidence=confidence, band=band,
                              legal_labels=legal.labels, model=model, error=None, latency_ms=latency_ms,
                              judge_label=label)
        return one_call(request, candidate.tool_name, candidate.arguments)


__all__ = ["INSTRUCTIONS", "Decision", "JevPolicy", "RulePolicy"]
