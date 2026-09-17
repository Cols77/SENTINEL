"""Credential-free deterministic AgentModelPort test double."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
from typing import Any, Iterable

from sentinel.contracts import AgentModelResult, AgentRequest, AgentToolCall


_TASK_ID = "22222222-2222-4222-8222-222222222222"
_PERCEPTION_ID = "33333333-3333-4333-8333-333333333333"
_EVIDENCE_ID = "44444444-4444-4444-8444-444444444444"


@dataclass(frozen=True)
class ScriptedCall:
    tool_name: str
    arguments: Mapping[str, Any]


DEFAULT_SCRIPT = (
    ScriptedCall("state.get_summary", MappingProxyType({})),
    ScriptedCall("state.get_recent_events", MappingProxyType({"after_sequence": 0, "limit": 8})),
    ScriptedCall("inspection.propose_plan", MappingProxyType({"catalogue_plan_id": "CAT_ALLEY", "reason": "use the registered route"})),
    ScriptedCall("task.create", MappingProxyType({"perception_result_id": _PERCEPTION_ID, "section_id": "LEFT_ALLEY_MIDDLE", "evidence_artifact_id": _EVIDENCE_ID})),
    ScriptedCall("patroller.rank_for_task", MappingProxyType({"task_id": _TASK_ID})),
    ScriptedCall("assignment.propose", MappingProxyType({"task_id": _TASK_ID, "patroller_id": "PATROLLER_1", "reason": "deterministic top candidate"})),
    ScriptedCall("finish", MappingProxyType({"status": "DONE", "summary": "simulated bounded run complete"})),
)


class ScriptedFakeAgentModel:
    """Return explicit scripted calls and never invoke the injected read port."""

    source_kind = "SIMULATED"

    def __init__(self, script: Iterable[ScriptedCall | Mapping[str, Any]] = DEFAULT_SCRIPT):
        normalized: list[ScriptedCall] = []
        for item in script:
            if isinstance(item, ScriptedCall):
                normalized.append(
                    ScriptedCall(item.tool_name, MappingProxyType(dict(item.arguments)))
                )
                continue
            if isinstance(item, Mapping):
                name = item.get("tool_name")
                arguments = item.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, Mapping):
                    raise ValueError("script entries require tool_name and object arguments")
                normalized.append(ScriptedCall(name, MappingProxyType(dict(arguments))))
                continue
            raise TypeError("script entries must be ScriptedCall or mapping values")
        self.script = tuple(normalized)
        self._index = 0

    @staticmethod
    def _result(
        request: AgentRequest,
        *,
        call: AgentToolCall | None = None,
        status: str = "TOOL_CALLS",
        intent: str | None = None,
        diagnostic: str | None = None,
    ) -> AgentModelResult:
        document: dict[str, Any] = {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_result",
            "agent_run_id": request.agent_run_id,
            "status": status,
            "tool_calls": [] if call is None else [call.to_dict()],
        }
        if intent is not None:
            document["intent"] = intent
        if diagnostic is not None:
            document["diagnostic"] = diagnostic[:512]
        return AgentModelResult.from_dict(document)

    def _call_result(self, request: AgentRequest, step: ScriptedCall) -> AgentModelResult:
        try:
            call = AgentToolCall.from_dict(
                {
                    "contract_version": "sentinel.v0",
                    "wire_type": "agent_tool_call",
                    "tool_name": step.tool_name,
                    "arguments": dict(step.arguments),
                }
            )
        except Exception:
            return self._result(
                request,
                status="MALFORMED",
                diagnostic="INVALID_SCRIPTED_CALL",
            )
        return self._result(request, call=call)

    def propose(self, request: AgentRequest, read_tools: Any = None) -> AgentModelResult:
        if not isinstance(request, AgentRequest):
            raise TypeError("request must be an AgentRequest")

        previous = request.previous_result
        if isinstance(previous, Mapping) and previous.get("status") == "REJECTED":
            error_code = previous.get("error_code")
            event_type = error_code if isinstance(error_code, str) and error_code else "REJECTED"
            return self._call_result(
                request,
                ScriptedCall(
                    "wait_for_event",
                    MappingProxyType(
                        {
                            "event_types": [event_type[:64]],
                            "reason": "wait after coordinator rejection",
                        }
                    ),
                ),
            )

        if self._index >= len(self.script):
            return self._result(
                request,
                status="NO_ACTION",
                intent="NO_ACTION_REQUIRED",
            )

        step = self.script[self._index]
        if step.tool_name not in request.legal_tools:
            return self._result(
                request,
                status="MALFORMED",
                diagnostic="SCRIPTED_TOOL_NOT_LEGAL",
            )
        self._index += 1
        return self._call_result(request, step)


__all__ = ["DEFAULT_SCRIPT", "ScriptedCall", "ScriptedFakeAgentModel"]
