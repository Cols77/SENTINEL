"""Approval-gated LLM plan proposer for architecture E."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import time
from typing import Any

from sentinel.contracts import AgentModelResult, AgentRequest
from sentinel.decision_prompt import (
    SELECT_PLAN_ESCALATION_CRITERION,
    SELECT_PLAN_INSTRUCTION,
    SELECT_PLAN_PLAN_CRITERION_PREFIX,
)
from sentinel.jev_choice import ChoiceAssessment, assess_choice
from sentinel.judgment import ChoiceJudge

from .legal_tools import ESCALATE_LABEL, Candidate, SELECT_PLAN
from .policy import PolicyContext, one_call
from .shadow import AuditSink

_ESCALATION_SUMMARY = "plan proposal needs operator review"
_SELECT_PLAN_TOOLS = frozenset({"inspection.propose_plan", "finish"})


def _write_attempt(
    sink: AuditSink, context: PolicyContext, *, advisor: str, started: float,
    gate_outcome: str, policy_label: str, raw_choice: Any = None,
    confidence: float | None = None,
    probabilities: dict[str, float] | None = None, margin: float | None = None,
    selected_probability: float | None = None,
    confidence_source: str | None = None,
    provider_summary: str | None = None,
) -> None:
    """Keep provider attempts separate from reviewable recommendations.

    Record only a string choice, a bounded accepted escalation summary, and
    finite metrics, never provider payloads or exception messages. Error
    categories are fixed boundary outcomes.
    """
    label = raw_choice if isinstance(raw_choice, str) else None
    record = {
        "record": "proposal_attempt",
        "run_id": context.state.run_id,
        "state_version": context.state.state_version,
        "decision_point": context.legal.decision_point,
        "advisor": advisor,
        "raw_choice": label,
        "judge_label": label,
        "policy_label": policy_label,
        "gate_outcome": gate_outcome,
        "confidence": confidence,
        "probabilities": probabilities,
        "margin": margin,
        "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
        "error_category": gate_outcome if gate_outcome in {
            "provider_error", "invalid_answer",
        } else None,
    }
    if advisor == "jev":
        record.update({
            "selected_probability": selected_probability,
            "confidence_source": confidence_source,
        })
    if provider_summary is not None:
        record["provider_summary"] = provider_summary[:512]
    sink.write(record)


def _as_valid_result(result: Any) -> dict[str, Any] | None:
    try:
        if isinstance(result, AgentModelResult):
            document = result.to_dict()
        elif isinstance(result, Mapping):
            document = dict(result)
        else:
            return None
        return AgentModelResult.from_dict(document).to_dict()
    except Exception:  # noqa: BLE001 -- untrusted adapter output is fail-closed
        return None


def parse_provider_escalation(result: Any, *, agent_run_id: str) -> str | None:
    """Return a bounded provider explanation for one valid escalation call."""
    document = _as_valid_result(result)
    if (
        document is None
        or document.get("agent_run_id") != agent_run_id
        or document.get("status") != "TOOL_CALLS"
    ):
        return None
    calls = document.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return None
    call = calls[0]
    if not isinstance(call, Mapping) or call.get("tool_name") != "finish":
        return None
    arguments = call.get("arguments")
    if not isinstance(arguments, Mapping) or arguments.get("status") != "NEEDS_REVIEW":
        return None
    summary = arguments.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    return summary.strip()[:512]


def is_canonical_escalation(result: Any, *, agent_run_id: str) -> bool:
    """Recognize only the controller-owned escalation emitted by this boundary."""
    if type(result) is not dict:
        return False
    expected = one_call(
        {"agent_run_id": agent_run_id},
        "finish",
        {"status": "NEEDS_REVIEW", "summary": _ESCALATION_SUMMARY},
    )
    return result == expected


def _select_plan_tools(context: PolicyContext) -> list[str]:
    tools: list[str] = []
    for candidate in context.legal.candidates:
        if candidate.tool_name not in _SELECT_PLAN_TOOLS:
            raise ValueError(f"unsupported SELECT_PLAN tool: {candidate.tool_name}")
        if candidate.tool_name not in tools:
            tools.append(candidate.tool_name)
    return tools


def _matches_context(
    *, run_id: str, state_version: int, context: PolicyContext,
) -> bool:
    return (
        run_id == context.state.run_id
        and state_version == context.state.state_version
    )


def _bound_jev_state(
    request: Mapping[str, Any], context: PolicyContext,
) -> dict[str, Any] | None:
    run_id = request.get("run_id")
    agent_run_id = request.get("agent_run_id")
    state_version = request.get("state_version")
    if (
        not isinstance(run_id, str)
        or not run_id.strip()
        or not isinstance(agent_run_id, str)
        or not agent_run_id.strip()
        or isinstance(state_version, bool)
        or not isinstance(state_version, int)
        or not _matches_context(
            run_id=run_id, state_version=state_version, context=context,
        )
    ):
        return None
    return {
        **context.state.to_packet(),
        "run_id": run_id,
        "agent_run_id": agent_run_id,
        "state_version": state_version,
    }


@dataclass(frozen=True)
class JevProposalGates:
    """Acceptance thresholds for a Jev plan recommendation."""

    act_at: float = 0.60
    min_margin: float = 0.15


class JevProposer:
    """Let Jev select one complete legal plan call, pending operator approval."""

    def __init__(
        self,
        judge: ChoiceJudge,
        sink: AuditSink,
        gates: JevProposalGates = JevProposalGates(),
    ) -> None:
        self.judge = judge
        self.sink = sink
        self.gates = gates

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        if context.legal.decision_point != SELECT_PLAN:
            return self._escalate(request)

        provider_state = _bound_jev_state(request, context)
        if provider_state is None:
            _write_attempt(
                self.sink, context, advisor="jev", started=time.perf_counter(),
                gate_outcome="invalid_answer", policy_label=ESCALATE_LABEL,
            )
            return self._escalate(request)

        candidates = {
            candidate.label: candidate
            for candidate in context.legal.candidates
            if candidate.tool_name in _SELECT_PLAN_TOOLS
        }
        if not candidates:
            _write_attempt(
                self.sink, context, advisor="jev", started=time.perf_counter(),
                gate_outcome="invalid_answer", policy_label=ESCALATE_LABEL,
            )
            return self._escalate(request)

        criteria = {
            label: (
                SELECT_PLAN_ESCALATION_CRITERION
                if label == ESCALATE_LABEL
                else SELECT_PLAN_PLAN_CRITERION_PREFIX + candidate.description
            )
            for label, candidate in candidates.items()
        }
        started = time.perf_counter()
        try:
            answer = self.judge.choice(
                state=provider_state,
                instruction=f"At {SELECT_PLAN}, {SELECT_PLAN_INSTRUCTION}",
                criteria=criteria,
                name="select_plan",
            )
        except Exception:  # noqa: BLE001 -- provider boundary is fail-closed
            _write_attempt(self.sink, context, advisor="jev", started=started,
                           gate_outcome="provider_error",
                           policy_label=ESCALATE_LABEL)
            return self._escalate(request)
        assessment = assess_choice(
            answer, tuple(candidates), escalation_label=ESCALATE_LABEL,
            act_at=self.gates.act_at, min_margin=self.gates.min_margin,
        )
        if (
            assessment.outcome not in {"MALFORMED_ANSWER", "ILLEGAL_OR_MALFORMED"}
            and {label for label, _ in assessment.probabilities} != set(candidates)
        ):
            assessment = ChoiceAssessment(
                selected_label=assessment.selected_label,
                final_label=ESCALATE_LABEL,
                outcome="ILLEGAL_OR_MALFORMED",
                confidence=assessment.confidence,
                selected_probability=assessment.selected_probability,
                confidence_source=assessment.confidence_source,
                margin=assessment.margin,
                probabilities=assessment.probabilities,
            )
        gate_outcome = self._audit_outcome(assessment)
        policy_label = assessment.final_label
        _write_attempt(
            self.sink, context, advisor="jev", started=started,
            gate_outcome=gate_outcome, policy_label=policy_label,
            raw_choice=assessment.selected_label,
            confidence=assessment.confidence,
            probabilities=dict(assessment.probabilities),
            margin=assessment.margin,
            selected_probability=assessment.selected_probability,
            confidence_source=self._audit_confidence_source(assessment),
        )
        if assessment.outcome != "ACCEPTED":
            return self._escalate(request)

        label = assessment.final_label
        confidence = assessment.confidence
        probabilities = dict(assessment.probabilities)
        margin = assessment.margin
        candidate = candidates[label]
        if candidate.tool_name == "finish":
            return self._escalate(request)
        reason = f"{context.legal.decision_point}: {candidate.description}"[:512]
        arguments = {**candidate.arguments, "reason": reason}
        result = one_call(request, candidate.tool_name, arguments)
        self.sink.write(
            {
                "record": "proposal_recommendation",
                "run_id": context.state.run_id,
                "state_version": context.state.state_version,
                "decision_point": context.legal.decision_point,
                "label": label,
                "reason": reason,
                "facts": {
                    "operator_request": context.state.request_text,
                    "candidate_description": candidate.description,
                },
                "advisor": "jev",
                "judge_label": label,
                "policy_label": label,
                "confidence": confidence,
                "probabilities": probabilities,
                "margin": margin,
            }
        )
        return AgentModelResult.from_dict(result).to_dict()

    @staticmethod
    def _audit_outcome(assessment: ChoiceAssessment) -> str:
        return {
            "ACCEPTED": "accepted",
            "LOW_CONFIDENCE": "low_confidence",
            "THIN_MARGIN": "low_margin",
        }.get(assessment.outcome, "invalid_answer")

    @staticmethod
    def _audit_confidence_source(assessment: ChoiceAssessment) -> str | None:
        return {
            "SCALAR": "provider_distribution_confidence",
            "SELECTED_PROBABILITY": "selected_probability_fallback",
        }.get(assessment.confidence_source)

    @staticmethod
    def _escalate(request: dict[str, Any]) -> dict[str, Any]:
        return one_call(
            request,
            "finish",
            {"status": "NEEDS_REVIEW", "summary": _ESCALATION_SUMMARY[:256]},
        )


class LlmProposer:
    """Ask an agent model for one current catalogue-plan recommendation.

    The returned proposal is still only an intent: ``AgentLoop`` submits it
    through the command gateway, where it becomes pending operator approval.
    """

    def __init__(self, adapter: Any, sink: AuditSink) -> None:
        self.adapter = adapter
        self.sink = sink

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        if context.legal.decision_point != SELECT_PLAN:
            return self._escalate(request)

        narrowed = {**request, "legal_tools": _select_plan_tools(context)}
        typed_request = AgentRequest.from_dict(narrowed)
        # The adapter's legacy `read_tools` parameter intentionally carries
        # PolicyContext so its prompt can render the current decision packet.
        started = time.perf_counter()
        if not _matches_context(
            run_id=typed_request.run_id,
            state_version=typed_request.state_version,
            context=context,
        ):
            _write_attempt(
                self.sink, context, advisor="llm", started=started,
                gate_outcome="invalid_answer", policy_label=ESCALATE_LABEL,
            )
            return self._escalate(request)
        try:
            model_result = self.adapter.propose(typed_request, context)
        except Exception:  # noqa: BLE001 -- the adapter boundary is fail-closed
            _write_attempt(self.sink, context, advisor="llm", started=started,
                           gate_outcome="provider_error",
                           policy_label=ESCALATE_LABEL)
            return self._escalate(request)
        raw_choice = None
        provider_summary = None
        accepted = None
        gate_outcome = "invalid_answer"
        try:
            raw_document = (model_result.to_dict() if isinstance(model_result, AgentModelResult)
                            else model_result)
            if isinstance(raw_document, Mapping):
                raw_calls = raw_document.get("tool_calls")
                if isinstance(raw_calls, list) and len(raw_calls) == 1:
                    raw_call = raw_calls[0]
                    if isinstance(raw_call, Mapping):
                        raw_arguments = raw_call.get("arguments")
                        if (
                            raw_call.get("tool_name") == "inspection.propose_plan"
                            and isinstance(raw_arguments, Mapping)
                        ):
                            raw_choice = raw_arguments.get("catalogue_plan_id")
            provider_summary = parse_provider_escalation(
                model_result, agent_run_id=typed_request.agent_run_id
            )
            if provider_summary is not None:
                raw_choice = ESCALATE_LABEL
                gate_outcome = "accepted"
            else:
                accepted = self._accepted_proposal(
                    model_result, context, agent_run_id=typed_request.agent_run_id
                )
                if accepted is not None:
                    gate_outcome = "accepted"
                else:
                    document = self._as_dict(model_result)
                    if document is not None and document.get("status") == "UNAVAILABLE":
                        gate_outcome = "provider_error"
        except Exception:  # noqa: BLE001 -- untrusted adapter output is fail-closed
            accepted = None
        _write_attempt(self.sink, context, advisor="llm", started=started,
                       raw_choice=raw_choice, gate_outcome=gate_outcome,
                       policy_label=(
                           raw_choice if accepted is not None else ESCALATE_LABEL
                       ),
                       provider_summary=provider_summary)
        if provider_summary is not None:
            return self._escalate(request)
        if accepted is None:
            return self._escalate(request)
        plan_id, reason, candidate = accepted

        result = one_call(
            request,
            "inspection.propose_plan",
            {"catalogue_plan_id": plan_id, "reason": reason},
        )
        # This record is written before AgentLoop submits the intent to the
        # gateway. Consumers must match it to the actual pending plan and
        # state version; a gateway-rejected proposal must never be presented
        # later merely because this recommendation record exists.
        self.sink.write(
            {
                "record": "proposal_recommendation",
                "run_id": context.state.run_id,
                "state_version": context.state.state_version,
                "decision_point": context.legal.decision_point,
                "label": plan_id,
                "reason": reason,
                "facts": {
                    "operator_request": context.state.request_text,
                    "candidate_description": candidate.description,
                },
            }
        )
        return AgentModelResult.from_dict(result).to_dict()

    def _accepted_proposal(
        self, model_result: Any, context: PolicyContext, *, agent_run_id: str,
    ) -> tuple[str, str, Candidate] | None:
        document = self._as_dict(model_result)
        if (
            document is None
            or document.get("agent_run_id") != agent_run_id
            or document.get("status") != "TOOL_CALLS"
        ):
            return None

        calls = document.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            return None
        call = calls[0]
        if not isinstance(call, dict) or call.get("tool_name") != "inspection.propose_plan":
            return None

        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            return None
        plan_id = arguments.get("catalogue_plan_id")
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return None
        reason = reason.strip()[:512]

        candidates = {
            candidate.label: candidate
            for candidate in context.legal.candidates
            if candidate.tool_name == "inspection.propose_plan"
        }
        candidate = candidates.get(plan_id)
        if candidate is None:
            return None
        return plan_id, reason, candidate

    @staticmethod
    def _as_dict(result: Any) -> dict[str, Any] | None:
        return _as_valid_result(result)

    @staticmethod
    def _escalate(request: dict[str, Any]) -> dict[str, Any]:
        return one_call(
            request,
            "finish",
            {"status": "NEEDS_REVIEW", "summary": _ESCALATION_SUMMARY[:256]},
        )

    def forget(self, agent_run_id: str) -> None:
        forget = getattr(self.adapter, "forget", None)
        if callable(forget):
            forget(agent_run_id)


__all__ = [
    "JevProposalGates",
    "JevProposer",
    "LlmProposer",
    "is_canonical_escalation",
    "parse_provider_escalation",
]
