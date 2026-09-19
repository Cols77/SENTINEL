"""D8/D9: bounded adapter-side history, and prompt rendering of the decision
packet + described legal options for a ContextualPolicy turn."""
from __future__ import annotations

import json
import re

from sentinel.adapter.llm.config import load_config
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.adapter.llm.parser import wire_tool_name
from sentinel.adapter.llm.prompt import build_chat_request
from sentinel.contracts import AgentRequest
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.legal_tools import SELECT_PLAN, Candidate, LegalSet
from sentinel.coordinator.policy import PolicyContext

UUID = "11111111-1111-4111-8111-111111111111"
AGENT_RUN_ID = "22222222-2222-4222-8222-222222222222"

ENV = {
    "SENTINEL_LLM_PROVIDER": "openrouter",
    "SENTINEL_LLM_MODEL": "model-v1",
    "SENTINEL_LLM_TIMEOUT_MS": "12000",
    "SENTINEL_LLM_PROMPT_REVISION": "prompt-v1",
    "SENTINEL_LLM_TOOL_POLICY_REVISION": "tools-v1",
}


def _request(*, previous_result=None) -> AgentRequest:
    return AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0", "wire_type": "agent_request",
            "agent_run_id": AGENT_RUN_ID, "run_id": UUID, "state_version": 1,
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
            "event_context": [], "evidence_context": [],
            "legal_tools": ["state.get_summary", "wait_for_event", "finish"],
            "previous_result": previous_result, "user_request": "Survey.",
        }
    )


def _tool_result(tool_name: str, sequence: int) -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "agent_tool_result",
        "tool_name": tool_name, "status": "ACCEPTED", "error_code": None,
        "state_version": sequence, "event_cursor": sequence,
        "legal_tools": ["state.get_summary"], "safe_summary": f"turn {sequence} accepted",
        "result": {"kind": "empty"},
    }


def _response(tool_name: str, arguments: dict) -> dict:
    return {"choices": [{"message": {"tool_calls": [
        {"type": "function", "function": {"name": wire_tool_name(tool_name), "arguments": json.dumps(arguments)}}
    ]}}]}


class ScriptedCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]


class Client:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


def _three_turn_script():
    return ScriptedCompletions([
        _response("state.get_summary", {}),
        _response("wait_for_event", {"reason": "turn2", "event_types": ["RUN_CREATED"]}),
        _response("finish", {"status": "DONE", "summary": "done"}),
    ])


def test_adapter_replays_bounded_history_of_prior_tool_calls_and_results():
    """(c) on turn 3, the messages list contains turns 1-2's tool calls and results."""
    completions = _three_turn_script()
    adapter = OpenAICompatibleAgentModel(load_config(ENV), client_factory=lambda **_: Client(completions), environ=ENV)

    call1 = adapter.propose(_request()).tool_calls[0]
    call2 = adapter.propose(_request(previous_result=_tool_result(call1.tool_name, 1))).tool_calls[0]
    adapter.propose(_request(previous_result=_tool_result(call2.tool_name, 2)))

    rendered = json.dumps(completions.calls[2]["messages"])
    assert wire_tool_name("state.get_summary") in rendered  # turn 1's call
    assert "turn 1 accepted" in rendered
    assert wire_tool_name("wait_for_event") in rendered  # turn 2's call
    assert "turn 2 accepted" in rendered


def test_history_turns_bounds_how_many_pairs_are_replayed():
    """(c) with history_turns=1, only turn 2 is present."""
    completions = _three_turn_script()
    adapter = OpenAICompatibleAgentModel(
        load_config(ENV), client_factory=lambda **_: Client(completions), environ=ENV, history_turns=1,
    )

    call1 = adapter.propose(_request()).tool_calls[0]
    call2 = adapter.propose(_request(previous_result=_tool_result(call1.tool_name, 1))).tool_calls[0]
    adapter.propose(_request(previous_result=_tool_result(call2.tool_name, 2)))

    rendered = json.dumps(completions.calls[2]["messages"])
    assert wire_tool_name("state.get_summary") not in rendered  # turn 1 dropped
    assert "turn 1 accepted" not in rendered
    assert wire_tool_name("wait_for_event") in rendered  # turn 2 kept
    assert "turn 2 accepted" in rendered


def test_history_is_scoped_per_agent_run_id():
    """A second, unrelated agent_run_id starts with no history."""
    completions = _three_turn_script()
    adapter = OpenAICompatibleAgentModel(load_config(ENV), client_factory=lambda **_: Client(completions), environ=ENV)

    call1 = adapter.propose(_request()).tool_calls[0]
    adapter.propose(_request(previous_result=_tool_result(call1.tool_name, 1)))

    other_request = AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0", "wire_type": "agent_request",
            "agent_run_id": "33333333-3333-4333-8333-333333333333", "run_id": UUID, "state_version": 1,
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
            "event_context": [], "evidence_context": [],
            "legal_tools": ["finish"], "previous_result": None, "user_request": "Survey.",
        }
    )
    adapter.propose(other_request)
    rendered = json.dumps(completions.calls[2]["messages"])
    assert "turn 1 accepted" not in rendered


def test_prompt_renders_decision_packet_and_option_descriptions_for_select_plan():
    """(d) the prompt for SELECT_PLAN contains both plan descriptions and the
    operator request, and its decision packet + options carry no UUIDs."""
    state = DecisionState(
        run_id=UUID, state_version=1, run_status="ACTIVE", agent_phase="PLAN",
        emergency_latched=False, request_text="inspect one station",
        plan_id=None, plan_mode=None, plan_station_ids=(), plan_approved=False,
        detection=None, open_task_station_ids=(), task_id=None, task_status=None,
    )
    candidates = (
        Candidate("inspection.propose_plan", {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"},
                  "PLAN_ROUTE_ONLY_2_ZONE",
                  "mode=ROUTE_ONLY; stations=2 (LEFT_ALLEY_MIDDLE, RIGHT_ALLEY_MIDDLE); marker verification=not required"),
        Candidate("finish", {"status": "NEEDS_REVIEW", "summary": "no plan fits"}, "escalate",
                  "hand the decision to the operator with the reason"),
    )
    legal = LegalSet(SELECT_PLAN, candidates)
    context = PolicyContext(state, legal)

    request = AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0", "wire_type": "agent_request",
            "agent_run_id": AGENT_RUN_ID, "run_id": UUID, "state_version": 1,
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
            "event_context": [], "evidence_context": [],
            "legal_tools": ["inspection.propose_plan", "finish"],
            "previous_result": None, "user_request": "inspect one station",
        }
    )

    payload = build_chat_request(request, prompt_revision="prompt-v1", tool_policy_revision="tools-v1", context=context)
    rendered_context = json.loads(payload["messages"][-1]["content"])

    assert rendered_context["decision_point"] == SELECT_PLAN
    labels = {option["label"] for option in rendered_context["options"]}
    assert "PLAN_ROUTE_ONLY_2_ZONE" in labels
    descriptions = " ".join(option["description"] for option in rendered_context["options"])
    assert "stations=2" in descriptions
    assert rendered_context["decision_packet"]["operator_request"] == "inspect one station"

    packet_and_options = json.dumps({"packet": rendered_context["decision_packet"], "options": rendered_context["options"]})
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", packet_and_options)


def test_prompt_falls_back_to_the_plain_request_without_a_context():
    """Without a PolicyContext the rendered prompt is unchanged (no decision_packet/options keys)."""
    request = _request()
    payload = build_chat_request(request, prompt_revision="prompt-v1", tool_policy_revision="tools-v1")
    rendered_context = json.loads(payload["messages"][-1]["content"])
    assert "decision_packet" not in rendered_context
    assert "options" not in rendered_context
