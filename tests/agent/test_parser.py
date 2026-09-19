import pytest

from sentinel.adapter.llm.parser import parse_chat_completion, parse_tool_calls
from sentinel.contracts import AgentModelResult


AGENT_RUN_ID = "11111111-1111-4111-8111-111111111111"


def completion(*calls, content=None, refusal=None):
    message = {"tool_calls": list(calls)}
    if content is not None:
        message["content"] = content
    if refusal is not None:
        message["refusal"] = refusal
    return {"choices": [{"message": message}]}


def tool(name, arguments):
    return {"id": "call", "type": "function", "function": {"name": name, "arguments": arguments}}


def test_valid_tool_call_becomes_public_typed_result():
    result = parse_chat_completion(
        completion(tool("state.get_summary", "{}")), agent_run_id=AGENT_RUN_ID
    )
    assert isinstance(result, AgentModelResult)
    assert result.status == "TOOL_CALLS"
    assert result.tool_calls[0].tool_name == "state.get_summary"
    assert result.tool_calls[0].arguments == {}


def test_catalogue_plan_arguments_are_schema_validated():
    result = parse_chat_completion(
        completion(tool("inspection.propose_plan", '{"catalogue_plan_id":"CAT_ALLEY"}')),
        agent_run_id=AGENT_RUN_ID,
    )
    assert result.tool_calls[0].arguments == {"catalogue_plan_id": "CAT_ALLEY"}


@pytest.mark.parametrize(
    "raw",
    [
        completion(tool("state.get_summary", "{not json")),
        completion(tool("operator.decide", "{}")),
        completion(tool("inspection.propose_plan", '{"catalogue_plan_id":"bad-id"}')),
        completion(content="do the thing"),
    ],
)
def test_malformed_unknown_invalid_or_prose_is_malformed(raw):
    result = parse_chat_completion(raw, agent_run_id=AGENT_RUN_ID)
    assert result.status == "MALFORMED"
    assert result.tool_calls == []
    assert result.diagnostic and len(result.diagnostic) <= 512


def test_refusal_and_empty_response_are_unavailable():
    for raw in (
        completion(refusal="I cannot help with that"),
        {},
        {"choices": []},
    ):
        result = parse_chat_completion(raw, agent_run_id=AGENT_RUN_ID)
        assert result.status == "UNAVAILABLE"
        assert result.tool_calls == []


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (completion(content="do the thing"), "MALFORMED"),
        (completion(refusal="I cannot help with that"), "UNAVAILABLE"),
    ],
)
def test_non_tool_call_results_omit_tool_calls_before_schema_validation(
    monkeypatch, raw, status
):
    documents = []
    original_from_dict = AgentModelResult.from_dict

    def validating_from_dict(document):
        documents.append(document)
        return original_from_dict(document)

    monkeypatch.setattr(AgentModelResult, "from_dict", validating_from_dict)

    result = parse_chat_completion(raw, agent_run_id=AGENT_RUN_ID)

    assert result.status == status
    assert len(documents) == 1
    assert "tool_calls" not in documents[0]


def test_multiple_calls_are_preserved_in_order_without_execution():
    raw = completion(
        tool("state.get_summary", "{}"),
        tool("wait_for_event", '{"event_types":["PLAN_APPROVED"],"reason":"approval"}'),
    )
    calls = parse_tool_calls(raw)
    assert [call.tool_name for call in calls] == ["state.get_summary", "wait_for_event"]
    assert calls[1].arguments["event_types"] == ["PLAN_APPROVED"]
    assert parse_chat_completion(raw, agent_run_id=AGENT_RUN_ID).status == "MALFORMED"
