import copy

import pytest

from sentinel.contracts import (
    AgentModelResult,
    AgentRequest,
    AgentToolCall,
    AgentToolResult,
    ContractValidationError,
    validate_document,
)


UUID = "11111111-1111-4111-8111-111111111111"
TS = "2026-09-17T00:00:00Z"
AGENT_RESULT_SCHEMA = "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult"


def _tool_result():
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_result",
        "tool_name": "state.get_summary",
        "status": "ACCEPTED",
        "error_code": None,
        "state_version": 4,
        "event_cursor": 4,
        "legal_tools": ["state.get_summary", "inspection.propose_plan", "wait_for_event"],
        "safe_summary": "Run is ready for a catalogue-bound plan proposal.",
        "result": {
            "kind": "state_summary",
            "run_status": "RUNNING",
            "phase": "PLAN",
            "state_version": 4,
        },
    }


def _request():
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_request",
        "agent_run_id": UUID,
        "run_id": UUID,
        "state_version": 4,
        "allowed_intent_schema_id": AGENT_RESULT_SCHEMA,
        "deadline": TS,
        "phase": "PLAN",
        "event_context": [
            {"sequence_no": 4, "event_type": "RUN_CREATED", "summary": "run is active"}
        ],
        "evidence_context": [
            {
                "artifact_id": UUID,
                "source_kind": "SIMULATED",
                "status": "AVAILABLE",
                "summary": "catalogue evidence is available",
            }
        ],
        "legal_tools": ["state.get_summary", "inspection.propose_plan", "wait_for_event"],
        "previous_result": _tool_result(),
        "user_request": "Survey the registered indoor route.",
    }


def test_typed_agent_request_and_tool_result_round_trip_through_public_root():
    request = AgentRequest.from_dict(_request())
    result = AgentToolResult.from_dict(_tool_result())

    assert request.to_dict() == _request()
    assert result.to_dict() == _tool_result()
    assert validate_document(request.to_dict()) == []
    assert validate_document(result.to_dict()) == []


def test_ordered_tool_calls_are_closed_and_v0_limited_to_one():
    call = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_call",
        "tool_name": "inspection.propose_plan",
        "arguments": {"catalogue_plan_id": "CAT_ALLEY", "reason": "bounded route"},
    }
    result = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_result",
        "agent_run_id": UUID,
        "status": "TOOL_CALLS",
        "tool_calls": [call],
    }

    wrapped = AgentModelResult.from_dict(result)
    assert wrapped.to_dict() == result
    assert validate_document(wrapped.to_dict()) == []

    two_calls = copy.deepcopy(result)
    two_calls["tool_calls"].append(
        {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_tool_call",
            "tool_name": "wait_for_event",
            "arguments": {"event_types": ["PLAN_APPROVED"], "reason": "operator approval"},
        }
    )
    with pytest.raises(ContractValidationError):
        validate_document(two_calls)

    extra_argument = copy.deepcopy(call)
    extra_argument["arguments"]["raw_command"] = "OPERATOR_DECIDE"
    with pytest.raises(ContractValidationError):
        validate_document({**result, "tool_calls": [extra_argument]})


def test_named_proposal_and_action_tools_have_typed_arguments():
    calls = [
        ("inspection.propose_plan", {"catalogue_plan_id": "CAT_ALLEY"}),
        (
            "task.create",
            {
                "perception_result_id": UUID,
                "section_id": "LEFT_ALLEY_MIDDLE",
                "evidence_artifact_id": UUID,
            },
        ),
        ("patroller.rank_for_task", {"task_id": UUID}),
        (
            "assignment.propose",
            {"task_id": UUID, "patroller_id": "PATROLLER_1", "reason": "rank-1 candidate"},
        ),
    ]
    for tool_name, arguments in calls:
        document = {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_tool_call",
            "tool_name": tool_name,
            "arguments": arguments,
        }
        assert validate_document(document) == []

    invalid = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_call",
        "tool_name": "assignment.propose",
        "arguments": {"task_id": UUID, "patroller_id": "patroller-1", "command": {}},
    }
    with pytest.raises(ContractValidationError):
        validate_document(invalid)


def test_public_wrappers_are_typed_and_reject_unknown_tool_names():
    call = AgentToolCall.from_dict(
        {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_tool_call",
            "tool_name": "finish",
            "arguments": {"status": "DONE", "summary": "complete"},
        }
    )
    assert call.to_dict()["tool_name"] == "finish"

    with pytest.raises(ContractValidationError):
        validate_document(
            {
                "contract_version": "sentinel.v0",
                "wire_type": "agent_tool_call",
                "tool_name": "operator.decide",
                "arguments": {},
            }
        )
