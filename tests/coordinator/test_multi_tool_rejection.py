"""B8: the V0 runner rejects a multi-call response before executing any call."""
from __future__ import annotations

from sentinel.coordinator.agent_loop import AgentLoop

RUN_ID = "11111111-1111-4111-8111-111111111111"
AGENT_RUN_ID = "66666666-6666-4666-8666-666666666666"


def _call(name):
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "agent_tool_call",
        "tool_name": name,
        "arguments": {},
    }


class RecordingPort:
    def __init__(self):
        self.submissions = []

    def read_state(self, run_id):
        return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN"}

    def read_events(self, *args):
        return []

    def submit(self, command):
        self.submissions.append(command)
        return {}


def test_runner_rejects_two_call_response_atomically_with_structured_feedback():
    port = RecordingPort()

    def model(request):
        return {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_result",
            "agent_run_id": request["agent_run_id"],
            "status": "TOOL_CALLS",
            "tool_calls": [_call("state.get_summary"), _call("state.get_recent_events")],
        }

    result = AgentLoop(port, model).run(RUN_ID, AGENT_RUN_ID, "inspect")
    assert result["status"] == "FAILED"
    assert result["error_code"] == "MULTIPLE_TOOL_CALLS"
    assert "summary" in result
    assert port.submissions == [], "no call may execute before the cardinality gate"


def test_runner_rejects_multi_call_response_even_when_first_call_is_read_only():
    port = RecordingPort()

    def model(request):
        return {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_result",
            "agent_run_id": request["agent_run_id"],
            "status": "TOOL_CALLS",
            "tool_calls": [_call("state.get_summary"), _call("finish")],
        }

    result = AgentLoop(port, model).run(RUN_ID, AGENT_RUN_ID, "inspect")
    assert result["status"] == "FAILED"
    assert result["error_code"] == "MULTIPLE_TOOL_CALLS"
    assert port.submissions == []
