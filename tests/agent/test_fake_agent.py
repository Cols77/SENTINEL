import pytest

from sentinel.adapter.llm.fake import DEFAULT_SCRIPT, ScriptedFakeAgentModel
from sentinel.contracts import AgentModelResult, AgentRequest


UUID = "11111111-1111-4111-8111-111111111111"

def request(previous_result=None):
    return AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0", "wire_type": "agent_request",
            "agent_run_id": UUID, "run_id": UUID, "state_version": 1,
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
            "event_context": [], "evidence_context": [],
            "legal_tools": ["state.get_summary", "state.get_recent_events", "inspection.propose_plan", "task.create", "patroller.rank_for_task", "assignment.propose", "wait_for_event", "finish"],
            "previous_result": previous_result, "user_request": "Survey.",
        }
    )


def test_simulated_default_script_returns_required_sequence_without_credentials():
    fake = ScriptedFakeAgentModel()
    names = [fake.propose(request()).tool_calls[0].tool_name for _ in DEFAULT_SCRIPT]
    assert names == [
        "state.get_summary", "state.get_recent_events", "inspection.propose_plan",
        "task.create", "patroller.rank_for_task", "assignment.propose", "finish",
    ]
    assert fake.source_kind == "SIMULATED"


def test_rejection_feedback_selects_wait_branch_instead_of_repeating_action():
    rejected = {
        "contract_version": "sentinel.v0", "wire_type": "agent_tool_result",
        "tool_name": "inspection.propose_plan", "status": "REJECTED",
        "error_code": "PLAN_REJECTED", "state_version": 2, "event_cursor": 2,
        "legal_tools": ["inspection.propose_plan", "wait_for_event"],
        "safe_summary": "operator rejected the plan", "result": {"kind": "empty"},
    }
    result = ScriptedFakeAgentModel().propose(request(rejected))
    assert result.tool_calls[0].tool_name == "wait_for_event"
    assert result.tool_calls[0].arguments["event_types"] == ["PLAN_REJECTED"]


@pytest.mark.parametrize(
    ("script", "expected_status"),
    [
        ([], "NO_ACTION"),
        ([{"tool_name": "not-a-legal-tool", "arguments": {}}], "MALFORMED"),
    ],
)
def test_non_tool_call_results_omit_tool_calls_before_schema_validation(
    monkeypatch, script, expected_status
):
    documents = []
    original_from_dict = AgentModelResult.from_dict

    def validating_from_dict(document):
        documents.append(document)
        return original_from_dict(document)

    monkeypatch.setattr(AgentModelResult, "from_dict", validating_from_dict)
    result = ScriptedFakeAgentModel(script=script).propose(request())

    assert result.status == expected_status
    assert len(documents) == 1
    assert "tool_calls" not in documents[0]
