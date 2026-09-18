"""B8: ordered multi-tool result shape and atomic V0 maxItems=1 rejection."""
from __future__ import annotations


def _chat_response(tool_calls):
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for name, args in tool_calls
                    ]
                }
            }
        ]
    }


import json


def test_parser_preserves_two_ordered_tool_calls_without_execution():
    from sentinel.adapter.llm.parser import parse_tool_calls

    calls = parse_tool_calls(
        _chat_response(
            [
                ("state.get_summary", {}),
                (
                    "state.get_recent_events",
                    {"after_sequence": 0, "limit": 8},
                ),
            ]
        )
    )
    assert [call.tool_name for call in calls] == [
        "state.get_summary",
        "state.get_recent_events",
    ]
    # The parser executes nothing; it only converts documents.
    assert all(call.to_dict()["wire_type"] == "agent_tool_call" for call in calls)


def test_parse_chat_completion_enforces_v0_schema_cardinality_without_execution():
    from sentinel.adapter.llm.parser import parse_chat_completion

    result = parse_chat_completion(
        _chat_response(
            [
                ("state.get_summary", {}),
                (
                    "state.get_recent_events",
                    {"after_sequence": 0, "limit": 8},
                ),
            ]
        ),
        agent_run_id="22222222-2222-4222-8222-222222222222",
    )
    # The V0 wire schema pins AgentModelResult.tool_calls to maxItems=1, so the
    # adapter-local result cannot carry two calls; the ordered collection shape
    # stays reserved in parse_tool_calls for a later contract revision, and the
    # runner rejects any multi-call response before executing either call.
    assert result.status == "MALFORMED"
    assert result.diagnostic == "ONE_TOOL_CALL_REQUIRED"
    assert not result.to_dict().get("tool_calls")
