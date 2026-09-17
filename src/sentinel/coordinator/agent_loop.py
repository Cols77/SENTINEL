"""Bounded, provider-neutral coordinator agent loop."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Callable

from sentinel.contracts import AgentModelResult, AgentRequest, validate_document


_TOOLS = ["state.get_summary", "state.get_recent_events", "inspection.propose_plan", "task.create", "patroller.rank_for_task", "assignment.propose", "wait_for_event", "finish"]
_TERMINAL = {"COMPLETED", "FAILED", "EMERGENCY"}


class AgentLoop:
    """Run a model through typed requests, one validated tool call per turn."""

    def __init__(self, port, model: Callable[[dict[str, Any]], dict[str, Any]], *, max_turns: int = 8):
        self.port = port
        self.model = model
        self.max_turns = min(max(1, max_turns), 8)

    def run(self, run_id: str, agent_run_id: str, user_request: str, *, deadline: str = "2999-01-01T00:00:00Z") -> dict[str, Any]:
        previous = None
        cursor = 0
        for _turn in range(self.max_turns):
            state = self.port.read_state(run_id)
            if state.get("run_status") in _TERMINAL:
                return {"status": state["run_status"], "summary": "run reached a terminal state", "state_version": state.get("state_version", 0)}
            events = self.port.read_events(run_id, cursor, 32)
            cursor = max([cursor, *[int(event.get("sequence_no", cursor)) for event in events]])
            request = AgentRequest(agent_run_id=agent_run_id, run_id=run_id, state_version=int(state.get("state_version", 0)), allowed_intent_schema_id="https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult", deadline=deadline, phase=str(state.get("agent_phase", "PLAN")), event_context=[self._event_summary(event) for event in events], evidence_context=[], legal_tools=_TOOLS, previous_result=previous, user_request=user_request).to_dict()
            try:
                raw_result = self.model(request)
                if raw_result.get("status") == "TOOL_CALLS" and len(raw_result.get("tool_calls", ())) > 1:
                    return {"status": "FAILED", "error_code": "MULTIPLE_TOOL_CALLS", "summary": "V0 accepts exactly one tool call per turn"}
                model_result = AgentModelResult.from_dict(raw_result)
            except Exception:
                return {"status": "FAILED", "error_code": "INVALID_AGENT_RESULT", "summary": "agent result failed contract validation"}
            if model_result.status == "INTENT":
                return {"status": "NEEDS_REVIEW", "summary": "agent intent requires operator review"}
            if model_result.status in {"NO_ACTION", "MALFORMED", "UNAVAILABLE"}:
                return {"status": "FAILED", "summary": model_result.diagnostic or "agent did not produce an actionable result"}
            if len(model_result.tool_calls) != 1:
                return {"status": "FAILED", "error_code": "MULTIPLE_TOOL_CALLS", "summary": "V0 accepts exactly one tool call per turn"}
            call = model_result.tool_calls[0]
            if call.tool_name == "finish":
                return {"status": call.arguments["status"], "summary": call.arguments["summary"]}
            outcome = self._execute(call.tool_name, call.arguments, run_id, agent_run_id, model_result.agent_run_id, state)
            previous = outcome
        return {"status": "NEEDS_REVIEW", "error_code": "MAX_TURNS", "summary": "agent turn budget exhausted"}

    @staticmethod
    def _event_summary(event):
        return {"sequence_no": event.get("sequence_no", 0), "event_type": event.get("event_type", ""), "summary": str(event.get("event_type", ""))[:128]}

    def _execute(self, tool_name, arguments, run_id, agent_run_id, result_agent_run_id, state):
        if tool_name == "state.get_summary":
            value = self.port.read_state(run_id)
            result = {"kind": "state_summary", "run_status": str(value.get("run_status", "UNKNOWN")), "phase": str(value.get("agent_phase", "UNKNOWN")), "state_version": int(value.get("state_version", 0))}
        elif tool_name == "state.get_recent_events":
            value = self.port.read_events(run_id, arguments["after_sequence"], arguments["limit"])
            result = {"kind": "event_list", "events": [self._event_summary(item) for item in value]}
        elif tool_name == "wait_for_event":
            result = {"kind": "wait"}
        else:
            payload = dict(arguments)
            command_type = {"inspection.propose_plan": "AGENT_INTENT_PROPOSE", "task.create": "TASK_CREATE", "patroller.rank_for_task": "TASK_RANK", "assignment.propose": "ASSIGNMENT_PROPOSE"}.get(tool_name)
            if not command_type:
                result = {"kind": "empty"}
            else:
                fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                key = "agent-" + hashlib.sha256((agent_run_id + ":" + tool_name + ":" + fingerprint).encode()).hexdigest()
                command = {"contract_version": "sentinel.v0", "wire_type": "command_envelope", "command_id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)), "command_type": command_type, "run_id": run_id, "actor_type": "AGENT", "actor_id": agent_run_id, "correlation_id": agent_run_id, "idempotency_key": key, "issued_at": "2999-01-01T00:00:00Z", "payload": payload}
                result = {"kind": "empty", "response": self.port.submit(command)}
        return {"contract_version": "sentinel.v0", "wire_type": "agent_tool_result", "tool_name": tool_name, "status": "ACCEPTED", "error_code": None, "state_version": int(state.get("state_version", 0)), "event_cursor": 0, "legal_tools": _TOOLS, "safe_summary": "bounded coordinator result", "result": result}
