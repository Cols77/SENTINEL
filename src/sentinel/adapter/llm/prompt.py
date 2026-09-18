"""Deterministic Chat Completions tools and bounded agent context."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from sentinel.contracts import AgentRequest

from .parser import APPROVED_TOOL_NAMES, wire_tool_name

PROMPT_MAX_TEXT = 512


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


_TOOL_DEFINITIONS = (
    _function(wire_tool_name("state.get_summary"), "Read the current run state summary.", {"type": "object", "additionalProperties": False, "properties": {}}),
    _function(
        wire_tool_name("state.get_recent_events"), "Read a bounded page of recent lifecycle events.",
        {"type": "object", "additionalProperties": False, "required": ["after_sequence", "limit"], "properties": {"after_sequence": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 32}}},
    ),
    _function(
        wire_tool_name("inspection.propose_plan"), "Propose a configured catalogue plan for review.",
        {"type": "object", "additionalProperties": False, "required": ["catalogue_plan_id"], "properties": {"catalogue_plan_id": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"}, "reason": {"type": "string", "maxLength": 512}}},
    ),
    _function(
        wire_tool_name("task.create"), "Propose a task from an available observation and evidence reference.",
        {"type": "object", "additionalProperties": False, "required": ["perception_result_id", "section_id", "evidence_artifact_id"], "properties": {"perception_result_id": {"type": "string", "format": "uuid"}, "section_id": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"}, "evidence_artifact_id": {"type": "string", "format": "uuid"}}},
    ),
    _function(
        wire_tool_name("patroller.rank_for_task"), "Read the coordinator ranking for a proposed task.",
        {"type": "object", "additionalProperties": False, "required": ["task_id"], "properties": {"task_id": {"type": "string", "format": "uuid"}}},
    ),
    _function(
        wire_tool_name("assignment.propose"), "Propose a configured patroller for coordinator review.",
        {"type": "object", "additionalProperties": False, "required": ["task_id", "patroller_id", "reason"], "properties": {"task_id": {"type": "string", "format": "uuid"}, "patroller_id": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"}, "reason": {"type": "string", "minLength": 1, "maxLength": 512}}},
    ),
    _function(
        wire_tool_name("wait_for_event"), "Wait for named lifecycle events before the next bounded turn.",
        {"type": "object", "additionalProperties": False, "required": ["event_types", "reason"], "properties": {"event_types": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string", "minLength": 1, "maxLength": 64}}, "reason": {"type": "string", "minLength": 1, "maxLength": 512}}},
    ),
    _function(
        wire_tool_name("finish"), "Finish the current bounded agent turn with a reviewable summary.",
        {"type": "object", "additionalProperties": False, "required": ["status", "summary"], "properties": {"status": {"type": "string", "enum": ["DONE", "NEEDS_REVIEW", "FAILED"]}, "summary": {"type": "string", "minLength": 1, "maxLength": 512}}},
    ),
)

APPROVED_TOOL_NAMES = tuple(APPROVED_TOOL_NAMES)


def _text(value: Any, limit: int = PROMPT_MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def _bounded_events(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    bounded: list[dict[str, Any]] = []
    for event in events[:32]:
        if not isinstance(event, Mapping) or not isinstance(event.get("sequence_no"), int):
            continue
        bounded.append({"sequence_no": event["sequence_no"], "event_type": _text(event.get("event_type", ""), 64), "summary": _text(event.get("summary", ""))})
    return bounded


def _bounded_evidence(evidence: Any) -> list[dict[str, Any]]:
    if not isinstance(evidence, list):
        return []
    bounded: list[dict[str, Any]] = []
    for item in evidence[:32]:
        if not isinstance(item, Mapping):
            continue
        bounded.append({"artifact_id": _text(item.get("artifact_id", ""), 128), "source_kind": _text(item.get("source_kind", ""), 64), "status": _text(item.get("status", ""), 64), "summary": _text(item.get("summary", ""))})
    return bounded


def _bounded_previous(previous: Any) -> dict[str, Any] | None:
    if not isinstance(previous, Mapping):
        return None
    result = previous.get("result")
    safe_result: dict[str, Any] = {}
    if isinstance(result, Mapping):
        if isinstance(result.get("kind"), str):
            safe_result["kind"] = result["kind"][:64]
        for name in ("run_status", "phase", "state_version"):
            if name in result and isinstance(result[name], (str, int)):
                safe_result[name] = result[name]
    bounded: dict[str, Any] = {}
    for name in ("tool_name", "status", "error_code"):
        value = previous.get(name)
        if value is not None:
            bounded[name] = _text(value, 128)
    for name in ("state_version", "event_cursor"):
        if isinstance(previous.get(name), int):
            bounded[name] = previous[name]
    if isinstance(previous.get("legal_tools"), list):
        bounded["legal_tools"] = [name for name in previous["legal_tools"] if name in APPROVED_TOOL_NAMES][:8]
    if "safe_summary" in previous:
        bounded["safe_summary"] = _text(previous["safe_summary"], 1024)
    bounded["result"] = safe_result
    return bounded


def _event_cursor(request: AgentRequest, previous: Mapping[str, Any] | None) -> int:
    if previous is not None and isinstance(previous.get("event_cursor"), int):
        return previous["event_cursor"]
    events = _bounded_events(request.event_context)
    return events[-1]["sequence_no"] if events else 0


def build_chat_request(request: AgentRequest, *, prompt_revision: str, tool_policy_revision: str) -> dict[str, Any]:
    """Build a stable provider payload from the shared request contract."""
    if not isinstance(request, AgentRequest):
        raise TypeError("request must be an AgentRequest")
    previous = _bounded_previous(request.previous_result)
    context = {
        "config": {"prompt_revision": prompt_revision, "tool_policy_revision": tool_policy_revision},
        "agent_run_id": request.agent_run_id,
        "run_id": request.run_id,
        "state_version": request.state_version,
        "phase": request.phase,
        "deadline": request.deadline,
        "event_cursor": _event_cursor(request, previous),
        "event_context": _bounded_events(request.event_context),
        "evidence_context": _bounded_evidence(request.evidence_context),
        "legal_tools": list(request.legal_tools),
        "previous_result": previous,
        "user_request": request.user_request,
    }
    return {
        "messages": [
            {"role": "system", "content": (
                "You are the decision-point executor for a bounded SENTINEL workflow. "
                "Each response you receive carries state, lifecycle-event, and evidence summaries "
                "plus a phase; your job is to advance the workflow by selecting the one action "
                "that is state-appropriate at this decision point. "
                "State-inspection tools (state.get_summary, state.get_recent_events) exist only "
                "for verifying or reconciling context that is unclear or contradicted; they are "
                "not for exploration when the provided context is already sufficient to act. "
                "When the legal_tools list contains exactly one actionable domain tool that "
                "matches the request, call that tool with the concrete identifier already present "
                "in the context (for example an identifier listed in the event context) instead "
                "of re-inspecting state. "
                "Invariants: return exactly one tool call per response, using only a legal tool; "
                "treat the structured context as facts; never invent evidence or identifiers."
            )},
            {"role": "user", "content": json.dumps(context, sort_keys=True, separators=(",", ":"))},
        ],
        "tools": copy.deepcopy(list(_TOOL_DEFINITIONS)),
        "tool_choice": "auto",
    }


__all__ = ["APPROVED_TOOL_NAMES", "build_chat_request"]
