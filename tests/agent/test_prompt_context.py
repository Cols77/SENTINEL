import json
from dataclasses import replace

from sentinel.adapter.llm.config import load_config
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.adapter.llm.parser import wire_tool_name
from sentinel.adapter.llm.prompt import build_chat_request
from sentinel.contracts import AgentRequest
from sentinel.coordinator.decision_state import DecisionState
from sentinel.coordinator.legal_tools import Candidate, LegalSet, SELECT_PLAN
from sentinel.coordinator.policy import PolicyContext


UUID = "11111111-1111-4111-8111-111111111111"
ENV = {
    "SENTINEL_LLM_PROVIDER": "openrouter",
    "SENTINEL_LLM_MODEL": "model-v1",
    "SENTINEL_LLM_TIMEOUT_MS": "12000",
    "SENTINEL_LLM_PROMPT_REVISION": "prompt-v1",
    "SENTINEL_LLM_TOOL_POLICY_REVISION": "tools-v1",
    "SENTINEL_LLM_API_KEY_ENV": "TEST_API_KEY",
    "TEST_API_KEY": "secret",
}
REQUEST = AgentRequest.from_dict(
    {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_request",
        "agent_run_id": UUID,
        "run_id": UUID,
        "state_version": 7,
        "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
        "deadline": "2099-09-17T00:00:00Z",
        "phase": "PLAN",
        "event_context": [{"sequence_no": 6, "event_type": "RUN_CREATED", "summary": "active"}],
        "evidence_context": [{"artifact_id": UUID, "source_kind": "SIMULATED", "status": "AVAILABLE", "summary": "ref"}],
        "legal_tools": ["state.get_summary", "inspection.propose_plan", "wait_for_event"],
        "previous_result": {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_tool_result",
            "tool_name": "inspection.propose_plan",
            "status": "REJECTED",
            "error_code": "PLAN_REJECTED",
            "state_version": 6,
            "event_cursor": 6,
            "legal_tools": ["state.get_summary", "inspection.propose_plan", "wait_for_event"],
            "safe_summary": "operator rejected the proposal",
            "result": {"kind": "empty"},
        },
        "user_request": "Survey the route.",
    }
)


def _select_plan_context() -> PolicyContext:
    state = DecisionState(
        run_id=UUID,
        state_version=7,
        run_status="ACTIVE",
        agent_phase="PLAN",
        emergency_latched=False,
        request_text="Survey the route.",
        plan_id=None,
        plan_mode=None,
        plan_station_ids=(),
        plan_approved=False,
        detection=None,
        open_task_station_ids=(),
        task_id=None,
        task_status=None,
    )
    legal = LegalSet(
        SELECT_PLAN,
        (
            Candidate(
                "inspection.propose_plan",
                {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"},
                "PLAN_ROUTE_ONLY_2_ZONE",
                "inspect both route stations",
            ),
            Candidate(
                "finish",
                {"status": "NEEDS_REVIEW", "summary": "no plan fits"},
                "escalate",
                "ask the operator to choose",
            ),
        ),
    )
    return PolicyContext(state, legal)


def test_prompt_request_has_exact_static_tools_and_bounded_dynamic_feedback():
    payload = build_chat_request(REQUEST, prompt_revision="prompt-v1", tool_policy_revision="tools-v1")
    assert [item["function"]["name"] for item in payload["tools"]] == [
        wire_tool_name(name) for name in REQUEST.legal_tools
    ]
    exported = json.dumps(payload["tools"], sort_keys=True)
    for forbidden in ("dispatch_drone", "raw_sdk", "sqlite", "filesystem", "credential", "emergency_clear", "operator_decision"):
        assert forbidden not in exported.lower()
    context = json.loads(payload["messages"][1]["content"])
    assert context["state_version"] == 7
    assert context["phase"] == "PLAN"
    assert context["legal_tools"] == REQUEST.legal_tools
    assert context["previous_result"]["error_code"] == "PLAN_REJECTED"
    assert context["previous_result"]["event_cursor"] == 6
    assert context["event_cursor"] == 6
    assert context["evidence_context"][0]["artifact_id"] == UUID
    assert "raw video" not in exported.lower()
    assert "one tool call" in payload["messages"][0]["content"].lower()


def test_system_prompt_states_decision_protocol():
    payload = build_chat_request(
        REQUEST, prompt_revision="prompt-v2", tool_policy_revision="tools-v1",
        context=_select_plan_context(),
    )
    prompt = payload["messages"][0]["content"].lower()
    # (1) decision-point executor framing: advance the workflow with one state-appropriate action.
    assert "decision-point executor" in prompt
    assert "advance the workflow" in prompt
    assert "state-appropriate" in prompt
    # (2) inspection tools are for verification/reconciliation, not exploration.
    assert "state.get_summary" in prompt
    assert "state.get_recent_events" in prompt
    assert "not for exploration" in prompt
    assert "sufficient to act" in prompt
    # (3) act on context: single matching legal domain tool gets called with the identifier in context.
    assert "exactly one actionable domain tool" in prompt
    assert "identifier already present in the context" in prompt
    assert "instead of re-inspecting state" in prompt
    # (4) finish/escalation semantics require complete constraint satisfaction.
    assert "terminal action" in prompt
    assert "fully satisfy every explicit operator constraint" in prompt
    assert "partial, approximate, or merely closest available coverage" in prompt
    assert "finish with status needs_review" in prompt
    # (6) invariants: one tool call, legal tools only, facts only, no invented evidence/identifiers.
    assert "exactly one tool call per response" in prompt
    assert "only a legal tool" in prompt
    assert "context as facts" in prompt
    assert "never invent evidence or identifiers" in prompt


def test_system_prompt_states_escalation_semantics():
    payload = build_chat_request(
        REQUEST, prompt_revision="prompt-v2", tool_policy_revision="tools-v1",
        context=_select_plan_context(),
    )
    prompt = payload["messages"][0]["content"].lower()
    assert "fully satisfy every explicit operator constraint" in prompt
    assert "partial, approximate, or merely closest available coverage" in prompt
    assert "finish with status needs_review" in prompt


def test_provider_tools_are_exactly_filtered_in_requested_order():
    plan_and_finish = replace(
        REQUEST, legal_tools=["inspection.propose_plan", "finish"]
    )
    finish_only = replace(REQUEST, legal_tools=["finish"])

    first = build_chat_request(
        plan_and_finish, prompt_revision="prompt-v1", tool_policy_revision="tools-v1"
    )
    second = build_chat_request(
        finish_only, prompt_revision="prompt-v1", tool_policy_revision="tools-v1"
    )

    assert [item["function"]["name"] for item in first["tools"]] == [
        wire_tool_name("inspection.propose_plan"), wire_tool_name("finish"),
    ]
    assert [item["function"]["name"] for item in second["tools"]] == [
        wire_tool_name("finish"),
    ]


def test_reason_argument_is_advertised_for_task_create_and_rank():
    request = replace(REQUEST, legal_tools=["task.create", "patroller.rank_for_task"])
    payload = build_chat_request(request, prompt_revision="prompt-v1", tool_policy_revision="tools-v1")
    schemas = {item["function"]["name"]: item["function"]["parameters"] for item in payload["tools"]}

    for tool in ("task.create", "patroller.rank_for_task"):
        reason = schemas[wire_tool_name(tool)]["properties"]["reason"]
        assert reason == {"type": "string", "maxLength": 512}, (tool, reason)
        assert "reason" not in schemas[wire_tool_name(tool)]["required"]


def test_system_prompt_does_not_encode_scenario_specific_answers():
    payload = build_chat_request(REQUEST, prompt_revision="prompt-v2", tool_policy_revision="tools-v1")
    prompt = payload["messages"][0]["content"]
    lowered = prompt.lower()
    for scenario_token in ("survey", "catalogue", "plan id", "patroller", "route", "drone"):
        assert scenario_token not in lowered, scenario_token


def test_prompt_revision_flows_from_config_into_payload_unchanged():
    payload = build_chat_request(REQUEST, prompt_revision="prompt-v2-from-config", tool_policy_revision="tools-v9")
    context = json.loads(payload["messages"][1]["content"])
    assert context["config"]["prompt_revision"] == "prompt-v2-from-config"
    assert context["config"]["tool_policy_revision"] == "tools-v9"


def test_prompt_build_is_deterministic_and_does_not_mutate_request():
    first = build_chat_request(REQUEST, prompt_revision="prompt-v1", tool_policy_revision="tools-v1")
    second = build_chat_request(REQUEST, prompt_revision="prompt-v1", tool_policy_revision="tools-v1")
    assert first == second
    assert first["tool_choice"] == "auto"


def test_prompt_context_text_fields_are_bounded_to_default_limit():
    oversized = "x" * 513
    request = replace(
        REQUEST,
        event_context=[{"sequence_no": 8, "event_type": "RUN_CREATED", "summary": oversized}],
        evidence_context=[
            {
                "artifact_id": UUID,
                "source_kind": "SIMULATED",
                "status": "AVAILABLE",
                "summary": oversized,
            }
        ],
    )

    payload = build_chat_request(request, prompt_revision="prompt-v1", tool_policy_revision="tools-v1")
    context = json.loads(payload["messages"][1]["content"])

    assert context["event_context"][0]["summary"] == oversized[:512]
    assert context["evidence_context"][0]["summary"] == oversized[:512]


def test_adapter_forwards_optional_policy_context_to_prompt():
    policy_context = _select_plan_context()
    state = policy_context.state
    legal = policy_context.legal

    class Completions:
        def create(self, **_kwargs):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {"name": "state.get_summary", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }

    class Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": Completions()})()

    adapter = OpenAICompatibleAgentModel(
        load_config(ENV), client_factory=lambda **_: Client(), environ=ENV
    )

    adapter.propose(REQUEST, policy_context)
    assert adapter.last_request is not None
    rendered = json.loads(adapter.last_request["messages"][-1]["content"])
    assert rendered["decision_point"] == SELECT_PLAN
    assert rendered["decision_prompt_revision"] == "select_plan.complete_constraints.v4"
    assert (
        "fully satisfy every explicit operator constraint"
        in rendered["decision_instruction"]
    )
    assert "closest" in rendered["decision_instruction"]
    assert rendered["decision_packet"] == state.to_packet()
    assert [item["label"] for item in rendered["options"]] == list(legal.labels)

    adapter.propose(REQUEST)
    assert adapter.last_request is not None
    plain = json.loads(adapter.last_request["messages"][-1]["content"])
    for key in ("decision_point", "decision_packet", "options"):
        assert key not in plain
