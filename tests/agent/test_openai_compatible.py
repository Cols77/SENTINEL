from dataclasses import replace

from sentinel.adapter.llm.config import load_config
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.contracts import AgentRequest


UUID = "11111111-1111-4111-8111-111111111111"
ENV = {
    "SENTINEL_LLM_PROVIDER": "openrouter",
    "SENTINEL_LLM_MODEL": "model-v1",
    "SENTINEL_LLM_TIMEOUT_MS": "12000",
    "SENTINEL_LLM_PROMPT_REVISION": "prompt-v1",
    "SENTINEL_LLM_TOOL_POLICY_REVISION": "tools-v1",
    "SENTINEL_LLM_API_KEY_ENV": "TEST_API_KEY",
    "TEST_API_KEY": "super-secret",
}
REQUEST = AgentRequest.from_dict(
    {
        "contract_version": "sentinel.v0", "wire_type": "agent_request",
        "agent_run_id": UUID, "run_id": UUID, "state_version": 1,
        "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
        "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
        "event_context": [], "evidence_context": [],
        "legal_tools": ["state.get_summary", "inspection.propose_plan"],
        "previous_result": None, "user_request": "Survey.",
    }
)


class Completions:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


class Client:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


def valid_response():
    return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {"name": "state.get_summary", "arguments": "{}"}}]}}]}


def test_adapter_lazily_constructs_injected_client_and_pins_call_arguments():
    completions = Completions(valid_response())
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return Client(completions)

    adapter = OpenAICompatibleAgentModel(load_config(ENV), client_factory=factory, environ=ENV)
    result = adapter.propose(REQUEST)
    assert captured == {"api_key": "super-secret", "base_url": "https://openrouter.ai/api/v1"}
    assert completions.kwargs["model"] == "model-v1"
    assert completions.kwargs["timeout"] == 12.0
    assert completions.kwargs["tool_choice"] == "auto"
    assert completions.kwargs["messages"] == adapter.last_request["messages"]
    assert completions.kwargs["tools"] == adapter.last_request["tools"]
    # V0 accepts exactly one tool call per turn: the adapter must request it.
    assert completions.kwargs["parallel_tool_calls"] is False
    assert result.status == "TOOL_CALLS"
    assert "super-secret" not in repr(result)


def test_malformed_provider_output_is_malformed_without_submission():
    completions = Completions({"choices": [{"message": {"content": "prose"}}]})
    adapter = OpenAICompatibleAgentModel(load_config(ENV), client_factory=lambda **_: Client(completions), environ=ENV)
    assert adapter.propose(REQUEST).status == "MALFORMED"


def test_non_mapping_provider_output_is_malformed_without_submission():
    completions = Completions(object())
    adapter = OpenAICompatibleAgentModel(
        load_config(ENV), client_factory=lambda **_: Client(completions), environ=ENV
    )
    assert adapter.propose(REQUEST).status == "MALFORMED"


def test_provider_failure_is_unavailable_with_bounded_non_secret_diagnostic():
    completions = Completions(error=RuntimeError("request failed with super-secret"))
    adapter = OpenAICompatibleAgentModel(load_config(ENV), client_factory=lambda **_: Client(completions), environ=ENV)
    result = adapter.propose(REQUEST)
    assert result.status == "UNAVAILABLE"
    assert result.diagnostic and len(result.diagnostic) <= 512
    assert "super-secret" not in result.diagnostic


def test_transport_observation_reports_only_bounded_identity_and_usage():
    observations = []

    class Response:
        def model_dump(self):
            return {
                "id": "provider-response-id",
                "model": "z-ai/glm-5.3",
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "bad_bool": True,
                    "negative": -1,
                },
                "choices": valid_response()["choices"],
                "secret": "super-secret",
            }

    completions = Completions(Response())
    adapter = OpenAICompatibleAgentModel(
        load_config(ENV),
        client_factory=lambda **_: Client(completions),
        environ=ENV,
        observation_callback=observations.append,
    )

    assert adapter.propose(REQUEST).status == "TOOL_CALLS"
    assert observations == [{
        "model": "z-ai/glm-5.3",
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }]
    serialized = repr(observations)
    assert "super-secret" not in serialized
    assert "choices" not in serialized
    assert "messages" not in serialized


def test_transport_observation_preserves_missing_or_invalid_usage_as_null():
    observations = []
    response = {"model": "m" * 1000, "usage": {"prompt_tokens": True, "completion_tokens": -2},
                **valid_response()}
    adapter = OpenAICompatibleAgentModel(
        load_config(ENV),
        client_factory=lambda **_: Client(Completions(response)),
        environ=ENV,
        observation_callback=observations.append,
    )

    adapter.propose(REQUEST)

    assert len(observations[0]["model"]) <= 256
    assert observations[0]["usage"] == {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
    }
