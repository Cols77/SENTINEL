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


def test_agent_loop_retry_after_schema_invalid_result_within_budget():
    """A schema-invalid model result gets bounded corrective feedback: the next
    turn's request carries a REJECTED previous_result, and the run can still
    finish without new contract fields."""
    class Port:
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN"}
        def read_events(self, *args): return []
        def submit(self, command): return {}

    requests = []
    def model(request):
        requests.append(request)
        if len(requests) == 1:
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "status": "TOOL_CALLS", "tool_calls": []}  # missing agent_run_id: schema-invalid
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS", "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": "finish", "arguments": {"status": "DONE", "summary": "recovered"}}]}

    result = AgentLoop(Port(), model, max_turns=4).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")
    assert result["status"] == "DONE"
    assert result["summary"] == "recovered"
    feedback = requests[1]["previous_result"]
    assert feedback["status"] == "REJECTED"
    assert feedback["error_code"] == "INVALID_AGENT_RESULT"
    assert feedback["safe_summary"]


def test_agent_loop_fails_after_two_corrective_retries():
    """Beyond the 2-retry corrective budget, an invalid result is terminal."""
    class Port:
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN"}
        def read_events(self, *args): return []
        def submit(self, command): return {}

    def model(request):
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "status": "TOOL_CALLS", "tool_calls": []}

    result = AgentLoop(Port(), model, max_turns=8).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")
    assert result["status"] == "FAILED"
    assert result["error_code"] == "INVALID_AGENT_RESULT"


def test_agent_loop_retry_after_agent_run_id_mismatch_within_budget():
    """An agent_run_id mismatch also earns bounded corrective feedback."""
    class Port:
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 0, "run_status": "RUNNING", "agent_phase": "PLAN"}
        def read_events(self, *args): return []
        def submit(self, command): return {}

    requests = []
    def model(request):
        requests.append(request)
        if len(requests) == 1:
            return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": "99999999-9999-4999-8999-999999999999", "status": "TOOL_CALLS", "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": "state.get_summary", "arguments": {}}]}
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS", "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": "finish", "arguments": {"status": "DONE", "summary": "recovered"}}]}

    result = AgentLoop(Port(), model, max_turns=4).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")
    assert result["status"] == "DONE"
    assert requests[1]["previous_result"]["status"] == "REJECTED"


def test_agent_loop_stops_at_approval_without_model_call():
    class Port:
        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": 1, "run_status": "WAITING_FOR_APPROVAL", "agent_phase": "PLAN"}
        def read_events(self, run_id, after, limit):
            assert after == 0
            return [{"sequence_no": 7, "event_type": "PLAN_PROPOSED"}]

    calls = []
    result = AgentLoop(Port(), lambda request: calls.append(request)).run(
        RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect"
    )
    assert result["status"] == "ACCEPTED"
    assert result["error_code"] == "WAITING_FOR_APPROVAL"
    assert result["event_cursor"] == 7
    assert result["state_version"] == 1
    assert calls == []


def test_agent_loop_binds_typed_feedback_to_next_request():
    class Port:
        state_version = 0
        submissions = []

        def read_state(self, run_id):
            return {"run_id": run_id, "state_version": self.state_version, "run_status": "RUNNING", "agent_phase": "PLAN"}

        def read_events(self, run_id, after, limit):
            return [{"sequence_no": 4, "event_type": "TASK_CREATED"}] if self.state_version else []

        def submit(self, command):
            self.submissions.append(command)
            self.state_version = 1
            return {"status": "COMPLETED"}

    port = Port()
    requests = []

    def model(request):
        requests.append(request)
        if len(requests) == 1:
            tool = "task.create"
            arguments = {"perception_result_id": "22222222-2222-4222-8222-222222222222", "section_id": "LEFT_ALLEY_MIDDLE", "evidence_artifact_id": "33333333-3333-4333-8333-333333333333"}
        else:
            tool = "finish"
            arguments = {"status": "DONE", "summary": "complete"}
        return {"contract_version": "sentinel.v0", "wire_type": "agent_result", "agent_run_id": request["agent_run_id"], "status": "TOOL_CALLS", "tool_calls": [{"contract_version": "sentinel.v0", "wire_type": "agent_tool_call", "tool_name": tool, "arguments": arguments}]}

    result = AgentLoop(port, model, max_turns=2).run(RUN_ID, "66666666-6666-4666-8666-666666666666", "inspect")

    assert result["status"] == "DONE"
    assert requests[1]["state_version"] == 1
    assert requests[1]["event_context"] == [{"sequence_no": 4, "event_type": "TASK_CREATED", "summary": "TASK_CREATED"}]
    assert requests[1]["previous_result"]["status"] == "ACCEPTED"
    assert requests[1]["previous_result"]["result"] == {"kind": "task"}
