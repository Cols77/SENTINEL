"""Approval-gated LLM plan proposer for architecture E."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import time
from typing import Any

from sentinel.contracts import AgentModelResult, AgentRequest

from .legal_tools import Candidate, SELECT_PLAN
from .policy import PolicyContext, one_call
from .shadow import AuditSink

_PROPOSER_TOOLS = ["inspection.propose_plan", "finish"]
_ESCALATION_SUMMARY = "plan proposal needs operator review"


def _write_attempt(
    sink: AuditSink, context: PolicyContext, *, advisor: str, started: float,
    raw_choice: Any = None, gate_outcome: str, confidence: float | None = None,
    probabilities: dict[str, float] | None = None, margin: float | None = None,
) -> None:
    """Keep provider attempts separate from reviewable recommendations.

    Record only a string choice and finite metrics, never provider payloads or
    exception messages. Error categories are fixed boundary outcomes.
    """
    label = raw_choice if isinstance(raw_choice, str) else None
    sink.write({
        "record": "proposal_attempt",
        "run_id": context.state.run_id,
        "state_version": context.state.state_version,
        "decision_point": context.legal.decision_point,
        "advisor": advisor,
        "raw_choice": label,
        "judge_label": label,
        "policy_label": label if gate_outcome == "accepted" else "escalate",
        "gate_outcome": gate_outcome,
        "confidence": confidence,
        "probabilities": probabilities,
        "margin": margin,
        "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
        "error_category": gate_outcome if gate_outcome in {
            "provider_error", "invalid_answer",
        } else None,
    })


@dataclass(frozen=True)
class JevProposalGates:
    """Acceptance thresholds for a Jev plan recommendation."""

    act_at: float = 0.60
    min_margin: float = 0.15


class JevProposer:
    """Let Jev select one complete legal plan call, pending operator approval."""

    def __init__(
        self,
        judge: Any,
        sink: AuditSink,
        gates: JevProposalGates = JevProposalGates(),
    ) -> None:
        self.judge = judge
        self.sink = sink
        self.gates = gates

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        if context.legal.decision_point != SELECT_PLAN:
            return self._escalate(request)

        candidates = {
            candidate.label: candidate
            for candidate in context.legal.candidates
            if candidate.tool_name == "inspection.propose_plan"
        }
        if not candidates:
            return self._escalate(request)

        criteria = {
            label: candidate.description for label, candidate in candidates.items()
        }
        started = time.perf_counter()
        try:
            answer = self.judge.choice(
                state=context.state.to_packet(),
                instruction=(
                    "At SELECT_PLAN, choose the one catalogue plan that best satisfies "
                    "the operator request. Use only the supplied criteria."
                ),
                criteria=criteria,
                name="select_plan",
            )
        except Exception:  # noqa: BLE001 -- provider boundary is fail-closed
            _write_attempt(self.sink, context, advisor="jev", started=started,
                           gate_outcome="provider_error")
            return self._escalate(request)
        try:
            assessment = self._assess_answer(answer, tuple(candidates))
        except Exception:  # noqa: BLE001 -- untrusted answer is fail-closed
            assessment = {"gate_outcome": "invalid_answer"}
        _write_attempt(self.sink, context, advisor="jev", started=started, **assessment)
        if assessment["gate_outcome"] != "accepted":
            return self._escalate(request)

        label = assessment["raw_choice"]
        confidence = assessment["confidence"]
        probabilities = assessment["probabilities"]
        margin = assessment["margin"]
        candidate = candidates[label]
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

    def _assess_answer(
        self, answer: Any, labels: tuple[str, ...]
    ) -> dict[str, Any]:
        assessment: dict[str, Any] = {"gate_outcome": "invalid_answer"}
        if not isinstance(answer, Mapping):
            return assessment
        label = answer.get("value")
        assessment["raw_choice"] = label
        raw_confidence = answer.get("confidence")
        confidence = self._probability(raw_confidence)
        assessment["confidence"] = confidence
        raw_probabilities = answer.get("probabilities")
        probabilities: dict[str, float] = {}
        if isinstance(raw_probabilities, Mapping):
            for key, value in raw_probabilities.items():
                probability = self._probability(value)
                if isinstance(key, str) and probability is not None:
                    probabilities[key] = probability
            assessment["probabilities"] = probabilities
        if (
            not isinstance(label, str)
            or label not in labels
            or not isinstance(raw_probabilities, Mapping)
            or set(raw_probabilities) != set(labels)
            or set(probabilities) != set(labels)
        ):
            return assessment

        # Jev's optional confidence is commonly present as None. Only absent
        # or None values fall back; explicitly malformed values stay invalid.
        if raw_confidence is None:
            confidence = probabilities[label]
            assessment["confidence"] = confidence
        if confidence is None:
            return assessment
        if max(probabilities, key=probabilities.get) != label:
            return assessment

        runners_up = [value for key, value in probabilities.items() if key != label]
        margin = probabilities[label] - (max(runners_up) if runners_up else 0.0)
        assessment["margin"] = margin
        if confidence < self.gates.act_at:
            assessment["gate_outcome"] = "low_confidence"
        elif margin < self.gates.min_margin:
            assessment["gate_outcome"] = "low_margin"
        else:
            assessment["gate_outcome"] = "accepted"
        return assessment

    @staticmethod
    def _probability(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            resolved = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(resolved) or not 0.0 <= resolved <= 1.0:
            return None
        return resolved

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

        narrowed = {**request, "legal_tools": list(_PROPOSER_TOOLS)}
        typed_request = AgentRequest.from_dict(narrowed)
        # The adapter's legacy `read_tools` parameter intentionally carries
        # PolicyContext so its prompt can render the current decision packet.
        started = time.perf_counter()
        try:
            model_result = self.adapter.propose(typed_request, context)
        except Exception:  # noqa: BLE001 -- the adapter boundary is fail-closed
            _write_attempt(self.sink, context, advisor="llm", started=started,
                           gate_outcome="provider_error")
            return self._escalate(request)
        raw_choice = None
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
                        if isinstance(raw_arguments, Mapping):
                            raw_choice = raw_arguments.get("catalogue_plan_id")
            accepted = self._accepted_proposal(model_result, context)
            if accepted is not None:
                gate_outcome = "accepted"
            else:
                document = self._as_dict(model_result)
                if document is not None and document.get("status") == "UNAVAILABLE":
                    gate_outcome = "provider_error"
        except Exception:  # noqa: BLE001 -- untrusted adapter output is fail-closed
            accepted = None
        _write_attempt(self.sink, context, advisor="llm", started=started,
                       raw_choice=raw_choice, gate_outcome=gate_outcome)
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
        self, model_result: Any, context: PolicyContext,
    ) -> tuple[str, str, Candidate] | None:
        document = self._as_dict(model_result)
        if document is None or document.get("status") != "TOOL_CALLS":
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


__all__ = ["JevProposalGates", "JevProposer", "LlmProposer"]
