"""Approval-gated LLM plan proposer for architecture E."""
from __future__ import annotations

from typing import Any

from sentinel.contracts import AgentModelResult, AgentRequest

from .legal_tools import SELECT_PLAN
from .policy import PolicyContext, one_call
from .shadow import AuditSink

_PROPOSER_TOOLS = ["inspection.propose_plan", "finish"]
_ESCALATION_SUMMARY = "plan proposal needs operator review"


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
        model_result = self.adapter.propose(typed_request, context)
        document = self._as_dict(model_result)
        if document is None or document.get("status") != "TOOL_CALLS":
            return self._escalate(request)

        calls = document.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            return self._escalate(request)
        call = calls[0]
        if not isinstance(call, dict) or call.get("tool_name") != "inspection.propose_plan":
            return self._escalate(request)

        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            return self._escalate(request)
        plan_id = arguments.get("catalogue_plan_id")
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return self._escalate(request)
        reason = reason.strip()[:512]

        candidates = {
            candidate.arguments.get("catalogue_plan_id"): candidate
            for candidate in context.legal.candidates
            if candidate.tool_name == "inspection.propose_plan"
        }
        candidate = candidates.get(plan_id)
        if candidate is None:
            return self._escalate(request)

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

    @staticmethod
    def _as_dict(result: Any) -> dict[str, Any] | None:
        if isinstance(result, AgentModelResult):
            return result.to_dict()
        if isinstance(result, dict):
            try:
                return AgentModelResult.from_dict(result).to_dict()
            except (TypeError, ValueError, KeyError):
                return None
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
        if forget is not None:
            forget(agent_run_id)


__all__ = ["LlmProposer"]
