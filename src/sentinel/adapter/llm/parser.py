"""Pure conversion of Chat Completions responses into SENTINEL contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sentinel.contracts import AgentModelResult, AgentToolCall


APPROVED_TOOL_NAMES = (
    "state.get_summary",
    "state.get_recent_events",
    "inspection.propose_plan",
    "task.create",
    "patroller.rank_for_task",
    "assignment.propose",
    "wait_for_event",
    "finish",
)


class ChatCompletionParseError(ValueError):
    """Raised internally when a provider response is not a typed tool call."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _choices(response: Mapping[str, Any]) -> Sequence[Any]:
    choices = response.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
        raise ChatCompletionParseError("MALFORMED_RESPONSE")
    return choices


def _message(response: Mapping[str, Any]) -> Mapping[str, Any]:
    choices_value = response.get("choices")
    if choices_value is None:
        raise ChatCompletionParseError("EMPTY_RESPONSE")
    choices = _choices(response)
    if not choices:
        raise ChatCompletionParseError("EMPTY_RESPONSE")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ChatCompletionParseError("MALFORMED_RESPONSE")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise ChatCompletionParseError("MALFORMED_RESPONSE")
    return message


def _raw_calls(response: Mapping[str, Any]) -> list[Any]:
    message = _message(response)
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        raise ChatCompletionParseError("MALFORMED_TOOL_CALL")
    return raw_calls


def _parse_one(raw_call: Any) -> AgentToolCall:
    if not isinstance(raw_call, Mapping):
        raise ChatCompletionParseError("MALFORMED_TOOL_CALL")
    function = raw_call.get("function")
    if not isinstance(function, Mapping):
        raise ChatCompletionParseError("MALFORMED_TOOL_CALL")
    name = function.get("name")
    if not isinstance(name, str) or name not in APPROVED_TOOL_NAMES:
        raise ChatCompletionParseError("UNKNOWN_TOOL")
    raw_arguments = function.get("arguments")
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ChatCompletionParseError("MALFORMED_ARGUMENTS") from exc
    elif isinstance(raw_arguments, Mapping):
        # This is useful for deterministic fakes and remains equivalent to the
        # JSON object returned by Chat Completions.
        arguments = dict(raw_arguments)
    else:
        raise ChatCompletionParseError("MALFORMED_ARGUMENTS")
    if not isinstance(arguments, dict):
        raise ChatCompletionParseError("MALFORMED_ARGUMENTS")

    document = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_call",
        "tool_name": name,
        "arguments": arguments,
    }
    try:
        return AgentToolCall.from_dict(document)
    except Exception as exc:
        # ContractValidationError is intentionally not exposed as a provider
        # diagnostic. The caller only needs the stable malformed outcome.
        raise ChatCompletionParseError("INVALID_ARGUMENTS") from exc


def parse_tool_calls(response: Mapping[str, Any]) -> list[AgentToolCall]:
    """Parse every returned call in provider order, without invoking tools.

    The helper preserves multi-call order for future protocol versions. V0's
    one-call result limit is applied by :func:`parse_chat_completion`.
    """
    if not isinstance(response, Mapping):
        raise ChatCompletionParseError("MALFORMED_RESPONSE")
    return [_parse_one(raw_call) for raw_call in _raw_calls(response)]


def _result(
    agent_run_id: str,
    status: str,
    *,
    tool_calls: list[AgentToolCall] | None = None,
    diagnostic: str | None = None,
) -> AgentModelResult:
    if diagnostic is not None:
        diagnostic = diagnostic[:512]
    document: dict[str, Any] = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_result",
        "agent_run_id": agent_run_id,
        "status": status,
        "tool_calls": [call.to_dict() for call in (tool_calls or [])],
    }
    if diagnostic is not None:
        document["diagnostic"] = diagnostic
    return AgentModelResult.from_dict(document)


def parse_chat_completion(response: Mapping[str, Any], *, agent_run_id: str) -> AgentModelResult:
    """Return a schema-validated result and never execute a model-selected call."""
    if not isinstance(response, Mapping):
        return _result(agent_run_id, "MALFORMED", diagnostic="MALFORMED_RESPONSE")

    try:
        message = _message(response)
    except ChatCompletionParseError as exc:
        if exc.reason == "EMPTY_RESPONSE":
            return _result(agent_run_id, "UNAVAILABLE", diagnostic="EMPTY_RESPONSE")
        return _result(agent_run_id, "MALFORMED", diagnostic=exc.reason)

    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        return _result(agent_run_id, "UNAVAILABLE", diagnostic="PROVIDER_REFUSAL")

    try:
        calls = parse_tool_calls(response)
    except ChatCompletionParseError as exc:
        return _result(agent_run_id, "MALFORMED", diagnostic=exc.reason)

    if len(calls) != 1:
        return _result(agent_run_id, "MALFORMED", diagnostic="ONE_TOOL_CALL_REQUIRED")
    return _result(agent_run_id, "TOOL_CALLS", tool_calls=calls)


__all__ = [
    "APPROVED_TOOL_NAMES",
    "ChatCompletionParseError",
    "parse_chat_completion",
    "parse_tool_calls",
]
