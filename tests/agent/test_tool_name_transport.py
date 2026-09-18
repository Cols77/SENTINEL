"""Transport-only provider-safe tool-name mapping tests (WP1).

Canonical dotted names are the WP0 enum everywhere internally. The adapter
maps to underscore form only at the provider wire boundary and restores the
canonical form on parse.
"""

from __future__ import annotations

import pytest

from sentinel.adapter.llm.parser import (
    APPROVED_TOOL_NAMES,
    canonical_tool_name,
    parse_chat_completion,
    parse_tool_calls,
    wire_tool_name,
)
from sentinel.adapter.llm.prompt import build_chat_request
from sentinel.contracts import AgentRequest


def _request(legal_tools: list[str] | None = None) -> AgentRequest:
    payload = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_request",
        "agent_run_id": "11111111-1111-4111-8111-111111111111",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "state_version": 1,
        "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
        "deadline": "2099-09-17T00:00:00Z",
        "phase": "PLAN",
        "event_context": [],
        "evidence_context": [],
        "previous_result": None,
        "legal_tools": legal_tools or list(APPROVED_TOOL_NAMES),
        "user_request": "do it",
    }
    return AgentRequest.from_dict(payload)


def test_no_two_v0_names_collide_under_transport_mapping() -> None:
    wire_names = [wire_tool_name(name) for name in APPROVED_TOOL_NAMES]
    assert len(wire_names) == len(set(wire_names))


def test_mapping_is_lossless_for_every_approved_name() -> None:
    for name in APPROVED_TOOL_NAMES:
        assert "." not in wire_tool_name(name)
        assert canonical_tool_name(wire_tool_name(name)) == name


def test_prompt_builder_emits_provider_safe_names_on_wire() -> None:
    request = build_chat_request(_request(), prompt_revision="p", tool_policy_revision="t")
    emitted = [tool["function"]["name"] for tool in request["tools"]]
    assert emitted == [wire_tool_name(name) for name in APPROVED_TOOL_NAMES]
    for name in emitted:
        assert "." not in name
        assert name == name.replace(".", "_")
    # descriptions and parameters untouched
    for tool in request["tools"]:
        assert set(tool["function"]) == {"name", "description", "parameters"}


def test_parser_maps_wire_names_back_to_canonical() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": wire_tool_name("state.get_summary"),
                                "arguments": "{}",
                            }
                        }
                    ]
                }
            }
        ]
    }
    calls = parse_tool_calls(response)
    assert [call.tool_name for call in calls] == ["state.get_summary"]

    result = parse_chat_completion(response, agent_run_id="0f0e0d0c-0b0a-4948-8276-5f5d4c3b2a10")
    assert result.status == "TOOL_CALLS"
    assert result.to_dict()["tool_calls"][0]["tool_name"] == "state.get_summary"


def test_parser_rejects_name_outside_wire_mapping() -> None:
    response = {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "totally_unknown", "arguments": "{}"}}]}}
        ]
    }
    result = parse_chat_completion(response, agent_run_id="0f0e0d0c-0b0a-4948-8276-5f5d4c3b2a10")
    assert result.status == "MALFORMED"
    assert result.to_dict()["diagnostic"] == "UNKNOWN_TOOL"


@pytest.mark.parametrize("name", list(APPROVED_TOOL_NAMES))
def test_round_trip_each_approved_name(name: str) -> None:
    assert canonical_tool_name(wire_tool_name(name)) == name
