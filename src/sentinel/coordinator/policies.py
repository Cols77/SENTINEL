"""RULE and Jev policies over described legal candidates (D9, D11).

``RulePolicy`` is the F executor: it acts only when the legal set leaves a
single candidate and otherwise hands the decision to the operator. ``F``
plus this policy is the demo architecture -- Jev and the LLM run only as
shadow advisors elsewhere (see the plan's Decisions section).

``JevPolicy`` is the fail-closed bridge for ``E`` (the P3 follow-up): at a
judgment point with more than one candidate it asks Jev to choose among the
described candidates, and only a HIGH-confidence, non-escalate pick is
acted on directly. Anything else -- MID/LOW confidence, or the judge itself
failing -- escalates to the operator instead of guessing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .legal_tools import ESCALATE_LABEL, JUDGMENT_POINTS
from .policy import PolicyContext, one_call

INSTRUCTIONS: dict[str, str] = {
    "SELECT_PLAN": "Choose the survey plan that fully satisfies the operator request with the fewest stations and "
                   "least verification burden. Choose 'escalate' if no plan fits or the request is contradictory.",
    "EVIDENCE_GATE": "The detection already passed the static score threshold. Choose 'create_task' if it warrants a "
                     "patrol task. Choose 'escalate' if it duplicates an open task, lies outside the approved plan, "
                     "or otherwise needs operator review.",
}


@dataclass(frozen=True)
class Decision:
    """Audit shape for one policy decision, regardless of which policy made
    it. ``chooser`` is ``RULE`` (a single candidate, no judgment needed),
    ``JEV`` (Jev picked and acted, including a deliberate escalate) or
    ``OPERATOR`` (escalated -- by rule, by low confidence, or by judge
    failure)."""

    chooser: str
    label: str | None
    confidence: float | None
    band: str | None
    legal_labels: tuple[str, ...]
    model: str | None
    error: str | None
    latency_ms: int | None


class RulePolicy:
    """Acts only when the legal set has exactly one candidate; at a
    judgment point (2+ candidates) it always escalates, labelling the
    decision ``OPERATOR``. This is the F executor: rules plus operator."""

    def __init__(self) -> None:
        self.last: Decision | None = None

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        legal = context.legal
        if len(legal.candidates) == 1:
            candidate = legal.candidates[0]
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
                 instructions: dict[str, str] = INSTRUCTIONS) -> None:
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
        assert point in JUDGMENT_POINTS, f"legal set at {point!r} has {len(legal.candidates)} candidates " \
                                          "but is not a known judgment point"
        instruction = self.instructions[point]
        criteria = {candidate.label: candidate.description for candidate in legal.candidates}
        packet = context.state.to_packet()
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
                              legal_labels=legal.labels, model=model, error=None, latency_ms=latency_ms)
        return one_call(request, candidate.tool_name, candidate.arguments)


__all__ = ["INSTRUCTIONS", "Decision", "JevPolicy", "RulePolicy"]
