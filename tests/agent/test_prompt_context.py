import json
from dataclasses import replace

from sentinel.adapter.llm.parser import APPROVED_TOOL_NAMES, wire_tool_name
from sentinel.adapter.llm.prompt import build_chat_request
from sentinel.contracts import AgentRequest


UUID = "11111111-1111-4111-8111-111111111111"
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


def test_prompt_request_has_exact_static_tools_and_bounded_dynamic_feedback():
    payload = build_chat_request(REQUEST, prompt_revision="prompt-v1", tool_policy_revision="tools-v1")
    assert [item["function"]["name"] for item in payload["tools"]] == [wire_tool_name(name) for name in APPROVED_TOOL_NAMES]
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
