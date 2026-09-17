from sentinel.coordinator.agent_loop import AgentLoop

RUN_ID = "11111111-1111-4111-8111-111111111111"


def test_agent_loop_is_bounded_and_returns_typed_safe_result():
    class Port:
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 2, "run_status": "RUNNING", "agent_phase": "PLAN"}
        def read_events(self, *args):
            return []
        def submit(self, command):
            raise AssertionError("finish is not a coordinator command")

    calls = []
    def model(request):
        calls.append(request)
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS", "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": "finish", "arguments": {"status": "DONE", "summary": "safe completion"}}]}

    result = AgentLoop(Port(), model, max_turns=8).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")
    assert result["status"] == "DONE"
    assert result["summary"] == "safe completion"
    assert len(calls) == 1


def test_agent_loop_rejects_multiple_tool_calls_atomically():
    class Port:
        submissions = []
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN"}
        def read_events(self, *args): return []
        def submit(self, command): self.submissions.append(command); return {}

    port = Port()
    def model(request):
        call = {"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": "state.get_summary", "arguments": {}}
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS", "tool_calls": [call, call]}

    result = AgentLoop(port, model).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")
    assert result["status"] == "FAILED"
    assert result["error_code"] == "MULTIPLE_TOOL_CALLS"
    assert port.submissions == []


def test_agent_loop_deadline_stops_before_model_turn():
    class Port:
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN"}
        def read_events(self, *args):
            return []

    calls = []
    result = AgentLoop(Port(), lambda request: calls.append(request)).run(
        RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect", deadline="2000-01-01T00:00:00Z"
    )

    assert result["status"] == "FAILED"
    assert result["error_code"] == "DEADLINE_EXPIRED"
    assert calls == []


def test_agent_loop_stops_on_emergency_latch_between_turns():
    class Port:
        latched = False
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN", "emergency_latched": self.latched}
        def read_events(self, *args):
            return []

    port = Port()
    calls = []
    def model(request):
        calls.append(request)
        port.latched = True
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS", "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": "state.get_summary", "arguments": {}}]}

    result = AgentLoop(port, model, max_turns=2).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")

    assert result["status"] == "EMERGENCY"
    assert result["error_code"] == "EMERGENCY_LATCHED"
    assert len(calls) == 1


def test_agent_loop_rejects_unknown_tool_without_submitting_mutation():
    class Port:
        submissions = []
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN"}
        def read_events(self, *args): return []
        def submit(self, command): self.submissions.append(command); return {}

    port = Port()
    def model(request):
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS", "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": "unknown.mutation", "arguments": {}}]}

    result = AgentLoop(port, model).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")
    assert result["status"] == "FAILED"
    assert result["error_code"] == "INVALID_AGENT_RESULT"
    assert port.submissions == []
