"""``survey.llm_adapter`` -- the live path behind ``--mode agent``. Every
transport here is a fake; nothing reaches a socket, and no key is real."""
import json

import pytest

from sentinel.survey.candidates import ESCALATE_LABEL
from sentinel.survey.llm_adapter import (AdapterUnavailable, LlmAdapter, build_tools,
                                         to_document)
from sentinel.survey.proposer import LlmProposer, chosen_label
from tests.survey.test_proposer import CONTEXT, REQUEST, REVIEW_CONTEXT, _Sink


def _reply(name, arguments, model="z-ai/glm-5.3"):
    return {"model": model, "choices": [{"message": {"tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]}


class _Transport:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, headers, payload):
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _adapter(monkeypatch, *replies, **kwargs):
    monkeypatch.setenv("TEST_AGENT_KEY", "not-a-real-key")
    transport = _Transport(*replies)
    adapter = LlmAdapter("TEST_AGENT_KEY", "https://example.invalid", "z-ai/glm-5.3",
                         transport=transport, **kwargs)
    return adapter, transport


def test_the_declared_tools_enumerate_exactly_the_legal_sweeps():
    tools = {t["function"]["name"]: t for t in build_tools(CONTEXT.legal.candidates)}
    assert set(tools) == {"survey_propose_sweep", "survey_escalate"}
    enum = tools["survey_propose_sweep"]["function"]["parameters"]["properties"]["option_id"]["enum"]
    assert enum == ["RAIL_MID_HEIGHT", "RAIL_HIGH_WIDE"]
    review = {t["function"]["name"] for t in build_tools(REVIEW_CONTEXT.legal.candidates)}
    assert "survey_submit_report" in review


def test_a_models_sweep_call_and_reason_reach_the_operator_through_the_proposer(monkeypatch):
    adapter, transport = _adapter(monkeypatch, _reply(
        "survey_propose_sweep",
        {"option_id": "RAIL_HIGH_WIDE", "reason": "The wide rail sees every desk at once."}))
    sink = _Sink()
    result = LlmProposer(adapter, sink).decide(REQUEST, CONTEXT)

    assert chosen_label(result) == "RAIL_HIGH_WIDE"
    assert result["tool_calls"][0]["arguments"]["reason"] == "The wide rail sees every desk at once."
    payload = transport.calls[0]["payload"]
    assert payload["tool_choice"] == "required" and payload["temperature"] == 0
    assert "count the computers" in payload["messages"][1]["content"]
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer not-a-real-key"
    assert sink.records[-1]["adapter_raw"] == adapter.last_raw


def test_a_sweep_the_model_was_not_offered_escalates(monkeypatch):
    adapter, _ = _adapter(monkeypatch, _reply(
        "survey_propose_sweep", {"option_id": "RAIL_LOW_NEAR", "reason": "looks fine"}))
    sink = _Sink()
    assert chosen_label(LlmProposer(adapter, sink).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL
    assert sink.records[-1]["outcome"] == "unknown_option"


def test_a_malformed_reply_escalates_and_is_kept_in_the_audit_record(monkeypatch):
    adapter, _ = _adapter(monkeypatch, {"choices": [{"message": {"content": "I would fly the rail."}}]})
    sink = _Sink()
    assert chosen_label(LlmProposer(adapter, sink).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL
    # prose instead of a tool call is zero calls, which the proposer names precisely
    assert sink.records[-1]["outcome"] == "wrong_call_count"
    assert sink.records[-1]["adapter_raw"]["choices"][0]["message"]["content"] == "I would fly the rail."


def test_a_transport_failure_escalates_without_leaking_the_key(monkeypatch):
    adapter, _ = _adapter(monkeypatch, RuntimeError("connection reset"))
    sink = _Sink()
    assert chosen_label(LlmProposer(adapter, sink).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL
    assert sink.records[-1]["outcome"] == "adapter_error:AdapterUnavailable"
    assert "not-a-real-key" not in json.dumps(sink.records)


def test_a_missing_key_is_refused_before_any_call(monkeypatch):
    monkeypatch.delenv("TEST_AGENT_KEY", raising=False)
    transport = _Transport()
    adapter = LlmAdapter("TEST_AGENT_KEY", "https://example.invalid", "m", transport=transport)
    with pytest.raises(AdapterUnavailable, match="TEST_AGENT_KEY"):
        adapter.propose(REQUEST, CONTEXT)
    assert transport.calls == []


def test_the_adapter_refuses_a_fourth_paid_call(monkeypatch):
    ok = _reply("survey_propose_sweep", {"option_id": "RAIL_MID_HEIGHT", "reason": "r"})
    adapter, transport = _adapter(monkeypatch, ok, ok, ok, ok)
    for _ in range(3):
        adapter.propose(REQUEST, CONTEXT)
    with pytest.raises(AdapterUnavailable, match="at most 3"):
        adapter.propose(REQUEST, CONTEXT)
    assert len(transport.calls) == 3


@pytest.mark.parametrize("resolved", ["other/model", "z-ai/glm-5.3-mini"])
def test_a_different_model_than_pinned_is_refused(monkeypatch, resolved):
    """Including a sibling that merely shares the pinned name as a prefix."""
    adapter, _ = _adapter(monkeypatch, _reply(
        "survey_propose_sweep", {"option_id": "RAIL_MID_HEIGHT", "reason": "r"}, model=resolved))
    with pytest.raises(AdapterUnavailable, match="resolved"):
        adapter.propose(REQUEST, CONTEXT)


def test_the_models_own_escalation_keeps_its_reason_and_is_not_called_illegal(monkeypatch):
    adapter, _ = _adapter(monkeypatch, _reply(
        "survey_escalate", {"reason": "the request does not say which desks to count"}))
    sink = _Sink()
    result = LlmProposer(adapter, sink).decide(REQUEST, CONTEXT)
    assert chosen_label(result) == ESCALATE_LABEL
    assert sink.records[-1]["outcome"] == "model_escalated"
    assert sink.records[-1]["model_reason"] == "the request does not say which desks to count"
    assert "which desks" in result["tool_calls"][0]["arguments"]["reason"]


def test_a_report_call_at_the_review_point_is_accepted_end_to_end(monkeypatch):
    adapter, transport = _adapter(monkeypatch, _reply("survey_submit_report", {}))
    result = LlmProposer(adapter, _Sink()).decide(REQUEST, REVIEW_CONTEXT)
    assert result["tool_calls"][0]["tool_name"] == "survey.submit_report"
    declared = {t["function"]["name"] for t in transport.calls[0]["payload"]["tools"]}
    assert "survey_submit_report" in declared


def test_pre_parsed_arguments_are_malformed_not_a_crash():
    raw = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "survey_propose_sweep", "arguments": {"option_id": "X"}}}]}}]}
    assert to_document(raw)["status"] == "MALFORMED"


def test_to_document_shapes():
    assert to_document({})["status"] == "MALFORMED"
    assert to_document(_reply("survey_submit_report", {})) == {
        "status": "TOOL_CALLS", "tool_calls": [{"tool_name": "survey.submit_report", "arguments": {}}]}
    bad_json = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "survey_propose_sweep", "arguments": "{not json"}}]}}]}
    assert to_document(bad_json)["status"] == "MALFORMED"
