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


def test_non_tool_call_agent_result_round_trips_without_tool_calls():
    result = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_result",
        "agent_run_id": UUID,
        "status": "INTENT",
        "intent": "INSPECT_SECTION",
        "catalogue_plan_id": "CAT_ALLEY",
    }

    assert validate_document(result) == []

    wrapped = AgentModelResult.from_dict(result)
    round_tripped = wrapped.to_dict()

    assert round_tripped == result
    assert "tool_calls" not in round_tripped
    assert validate_document(round_tripped) == []


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


_TYPED_TOOL_ARGUMENTS = {
    "state.get_summary": {},
    "state.get_recent_events": {"after_sequence": 4, "limit": 8},
    "inspection.propose_plan": {
        "catalogue_plan_id": "CAT_ALLEY",
        "reason": "bounded route",
    },
    "task.create": {
        "perception_result_id": UUID,
        "section_id": "LEFT_ALLEY_MIDDLE",
        "evidence_artifact_id": UUID,
    },
    "patroller.rank_for_task": {"task_id": UUID},
    "assignment.propose": {
        "task_id": UUID,
        "patroller_id": "PATROLLER_1",
        "reason": "rank-1 candidate",
    },
    "wait_for_event": {
        "event_types": ["PLAN_APPROVED"],
        "reason": "operator approval",
    },
    "finish": {"status": "DONE", "summary": "complete"},
}


def _tool_call(tool_name, arguments):
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_call",
        "tool_name": tool_name,
        "arguments": arguments,
    }


@pytest.mark.parametrize(("tool_name", "arguments"), _TYPED_TOOL_ARGUMENTS.items())
def test_each_allowed_tool_name_accepts_only_its_typed_arguments(tool_name, arguments):
    assert validate_document(_tool_call(tool_name, arguments)) == []


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (tool_name, next_arguments)
        for tool_name, next_arguments in zip(
            _TYPED_TOOL_ARGUMENTS,
            [*_TYPED_TOOL_ARGUMENTS.values()][1:] + [next(iter(_TYPED_TOOL_ARGUMENTS.values()))],
        )
    ],
)
def test_each_allowed_tool_name_rejects_another_tool_arguments(tool_name, arguments):
    with pytest.raises(ContractValidationError):
        validate_document(_tool_call(tool_name, arguments))


@pytest.mark.parametrize(
    ("status", "extra"),
    [
        (
            "INTENT",
            {"intent": "INSPECT_SECTION", "catalogue_plan_id": "CAT_ALLEY"},
        ),
        ("NO_ACTION", {"intent": "NO_ACTION_REQUIRED"}),
        ("MALFORMED", {"intent": None}),
        ("UNAVAILABLE", {"intent": None}),
    ],
)
def test_non_tool_call_statuses_reject_tool_calls(status, extra):
    result = {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_result",
        "agent_run_id": UUID,
        "status": status,
        "tool_calls": [_tool_call("state.get_summary", {})],
        **extra,
    }

    with pytest.raises(ContractValidationError):
        validate_document(result)


def _command(command_type, payload, *, actor_type="AGENT"):
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "command_envelope",
        "command_id": UUID,
        "command_type": command_type,
        "run_id": UUID,
        "actor_type": actor_type,
        "actor_id": "agent-1",
        "correlation_id": UUID,
        "idempotency_key": "command-123",
        "issued_at": TS,
        "payload": payload,
    }


def test_intent_command_persists_plan_identity_only_for_successful_inspection():
    assert validate_document(
        _command(
            "AGENT_INTENT_PROPOSE",
            {"intent": "INSPECT_SECTION", "catalogue_plan_id": "CAT_ALLEY"},
        )
    ) == []
    with pytest.raises(ContractValidationError):
        validate_document(_command("AGENT_INTENT_PROPOSE", {"intent": "INSPECT_SECTION"}))
    assert validate_document(
        _command("AGENT_INTENT_PROPOSE", {"intent": "NO_ACTION_REQUIRED"})
    ) == []


def test_coordinator_ingress_commands_are_closed_and_typed():
    report = {
        "report_id": UUID,
        "run_id": UUID,
        "plan_id": UUID,
        "plan_sha256": "a" * 64,
        "passed": True,
        "checks": [
            {"name": name, "passed": True, "checked_at": TS}
            for name in (
                "WIFI_INTERFACE", "DRONE_LINK", "BATTERY_TELEMETRY", "LIVE_VIDEO",
                "LAYOUT_MARKERS", "BOUNDED_AREA", "HEIGHT_ENVELOPE", "EMERGENCY_LATCH",
            )
        ],
        "checked_at": TS,
    }
    assert validate_document(
        _command("PREFLIGHT_INGEST", {"preflight_report": report}, actor_type="WORKER")
    ) == []
    assert validate_document(
        _command(
            "ASSIGNMENT_PROPOSE",
            {"task_id": UUID, "patroller_id": "PATROLLER_1", "reason": "rank-1 candidate"},
        )
    ) == []
    with pytest.raises(ContractValidationError):
        validate_document(
            _command("ASSIGNMENT_PROPOSE", {"task_id": UUID, "patroller_id": "PATROLLER_1"})
        )


def _result_envelope(**overrides):
    result = {
        "contract_version": "sentinel.v0",
        "wire_type": "result_envelope",
        "command_id": UUID,
        "run_id": UUID,
        "status": "COMPLETED",
        "state_version": 5,
        "recorded_at": TS,
        "payload": {},
    }
    result.update(overrides)
    return result


def test_result_envelope_accepts_shared_typed_fields_and_round_trips():
    result = _result_envelope(
        reason="assignment accepted",
        correlation_id=UUID,
    )

    assert validate_document(result) == []


@pytest.mark.parametrize(
    "field,value",
    [("reason", 42), ("reason", "x" * 513), ("correlation_id", "not-a-uuid")],
)
def test_result_envelope_rejects_malformed_shared_typed_fields(field, value):
    with pytest.raises(ContractValidationError):
        validate_document(_result_envelope(**{field: value}))
