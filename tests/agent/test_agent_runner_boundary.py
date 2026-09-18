"""B7: prove the adapter/runner integration boundary.

The fake model and the OpenAI-compatible adapter share the same provider-neutral
``AgentModelPort`` shape, and only the coordinator executes typed actions.
"""

from __future__ import annotations

import inspect

from sentinel.adapter.llm import AgentModelPort
from sentinel.adapter.llm.config import load_config
from sentinel.adapter.llm.fake import ScriptedCall, ScriptedFakeAgentModel
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.contracts import AgentRequest

RUN_ID = "11111111-1111-4111-8111-111111111111"
AGENT_RUN_ID = "22222222-2222-4222-8222-222222222222"

ENV = {
    "SENTINEL_LLM_PROVIDER": "openai",
    "SENTINEL_LLM_MODEL": "gpt-test",
    "SENTINEL_LLM_TIMEOUT_MS": "15000",
    "SENTINEL_LLM_PROMPT_REVISION": "prompt-v1",
    "SENTINEL_LLM_TOOL_POLICY_REVISION": "tools-v1",
}


def _request() -> AgentRequest:
    return AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0", "wire_type": "agent_request",
            "agent_run_id": AGENT_RUN_ID, "run_id": RUN_ID, "state_version": 1,
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
            "event_context": [], "evidence_context": [],
            "legal_tools": ["state.get_summary"],
            "previous_result": None, "user_request": "Survey.",
        }
    )


def test_fake_and_openai_adapter_share_the_agent_model_port_shape():
    fake = ScriptedFakeAgentModel(script=[ScriptedCall("state.get_summary", {})])
    adapter = OpenAICompatibleAgentModel(
        load_config(ENV), client_factory=lambda **_: object(), environ=ENV
    )
    assert isinstance(fake, AgentModelPort)
    assert isinstance(adapter, AgentModelPort)
    fake_signature = inspect.signature(fake.propose)
    adapter_signature = inspect.signature(adapter.propose)
    assert list(fake_signature.parameters) == list(adapter_signature.parameters)


def test_fake_model_turns_only_produce_coordinator_executed_commands(tmp_path):
    """The scripted fake drives the loop; every mutation crosses CoordinatorPort.submit()."""
    from sentinel.contracts import AgentRequest
    from tests.coordinator.harness import CoordinatorBoundary, close, envelope, open_harness

    repo, gateway = open_harness(tmp_path)
    try:
        gateway.submit(
            envelope("RUN_CREATE", {"request_text": "inspect one station"}, sequence=1, actor_type="OPERATOR")
        )
        submitted = []
        boundary = CoordinatorBoundary(repo, gateway)
        original_submit = boundary.submit

        def recording_submit(command):
            submitted.append(command)
            return original_submit(command)

        boundary.submit = recording_submit

        class TypedFake:
            """The scripted fake speaks the typed AgentRequest contract; the loop
            hands it a mapping, so this shim reconstructs the wrapper and returns
            the typed result document unchanged."""

            def __init__(self, model):
                self._model = model

            def propose(self, request, read_tools=None):
                result = self._model.propose(AgentRequest.from_dict(request), read_tools)
                return result.to_dict() if hasattr(result, "to_dict") else result

        model = TypedFake(
            ScriptedFakeAgentModel(
                script=[
                    ScriptedCall("state.get_summary", {}),
                    ScriptedCall(
                        "inspection.propose_plan",
                        {"catalogue_plan_id": "PLAN_ROUTE_ONLY_2_ZONE"},
                    ),
                ]
            )
        )
        result = AgentLoop(boundary, model.propose, max_turns=3).run(
            RUN_ID, AGENT_RUN_ID, "inspect one station"
        )
        assert result["status"] == "ACCEPTED"
        assert result["error_code"] == "WAITING_FOR_APPROVAL"
        assert submitted, "the scripted fake produced no coordinator commands"
        assert all(
            command.get("wire_type") == "command_envelope" for command in submitted
        )
        assert any(
            command["command_type"] == "AGENT_INTENT_PROPOSE" for command in submitted
        )
    finally:
        close(repo)


def test_openai_adapter_returns_without_touching_coordinator_repository():
    """The adapter converts the provider response without seeing repository state."""

    class ForbiddenRepository:
        def __getattr__(self, name):  # pragma: no cover - proves no access happened
            raise AssertionError(f"adapter touched repository attribute {name}")

    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "state.get_summary",
                                        "arguments": "{}",
                                    },
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
    adapter._environ = {**ENV, "__forbidden__": ForbiddenRepository()}  # never read
    result = adapter.propose(_request())
    assert result.status == "TOOL_CALLS"
    assert result.tool_calls[0].tool_name == "state.get_summary"
    assert "model" in captured and "tools" in captured


def test_adapters_do_not_import_sqlite_worker_or_sdk_at_module_level():
    import sentinel.adapter.llm.fake as fake_module
    import sentinel.adapter.llm.openai_compatible as adapter_module

    adapter_source = inspect.getsource(adapter_module)
    assert "sqlite" not in adapter_source
    assert "CoordinatorRepository" not in adapter_source
    assert "WorkerPort" not in adapter_source
    fake_source = inspect.getsource(fake_module)
    assert "sqlite" not in fake_source
    assert "openai" not in fake_source
