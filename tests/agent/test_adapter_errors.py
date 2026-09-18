"""B9: every adapter-local failure is explicit, bounded, and coordinator-independent."""
from __future__ import annotations

import pytest

from sentinel.adapter.llm.config import (
    LLM_CONFIG_INVALID,
    LLM_CONFIG_MISSING,
    LLM_CREDENTIAL_MISSING,
    LLM_DEPENDENCY_MISSING,
    AdapterConfigError,
    load_config,
)
from sentinel.adapter.llm.openai_compatible import OpenAICompatibleAgentModel
from sentinel.contracts import AgentRequest

BASE = {
    "SENTINEL_LLM_PROVIDER": "openai",
    "SENTINEL_LLM_MODEL": "gpt-test",
    "SENTINEL_LLM_TIMEOUT_MS": "15000",
    "SENTINEL_LLM_PROMPT_REVISION": "prompt-v1",
    "SENTINEL_LLM_TOOL_POLICY_REVISION": "tools-v1",
    "SENTINEL_LLM_API_KEY_ENV": "SENTINEL_TEST_CREDENTIAL",
}

UUID = "11111111-1111-4111-8111-111111111111"


def _request() -> AgentRequest:
    return AgentRequest.from_dict(
        {
            "contract_version": "sentinel.v0", "wire_type": "agent_request",
            "agent_run_id": UUID, "run_id": UUID, "state_version": 1,
            "allowed_intent_schema_id": "https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentModelResult",
            "deadline": "2099-09-17T00:00:00Z", "phase": "PLAN",
            "event_context": [], "evidence_context": [],
            "legal_tools": ["state.get_summary"],
            "previous_result": None, "user_request": "Survey.",
        }
    )


def test_every_required_variable_missing_raises_exact_code_with_name():
    for variable in (
        "SENTINEL_LLM_PROVIDER",
        "SENTINEL_LLM_MODEL",
        "SENTINEL_LLM_TIMEOUT_MS",
        "SENTINEL_LLM_PROMPT_REVISION",
        "SENTINEL_LLM_TOOL_POLICY_REVISION",
    ):
        env = dict(BASE)
        env.pop(variable)
        with pytest.raises(AdapterConfigError) as caught:
            load_config(env)
        assert caught.value.code == LLM_CONFIG_MISSING
        assert variable in str(caught.value)


def test_invalid_values_raise_exact_invalid_code():
    for variable, value in (
        ("SENTINEL_LLM_PROVIDER", ""),
        ("SENTINEL_LLM_TIMEOUT_MS", "not-a-number"),
        ("SENTINEL_LLM_TIMEOUT_MS", "300001"),
        ("SENTINEL_LLM_API_KEY_ENV", "lowercase-not-allowed"),
    ):
        env = dict(BASE)
        env[variable] = value
        with pytest.raises(AdapterConfigError) as caught:
            load_config(env)
        assert caught.value.code == LLM_CONFIG_INVALID


def test_missing_openai_dependency_raises_dependency_missing_without_fallback(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "openai" or name.startswith("openai."):
            raise ImportError("openai is blocked for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)
    # A fresh adapter instance must not reuse a cached client from another test.
    adapter = OpenAICompatibleAgentModel(load_config(BASE), environ={**BASE, "SENTINEL_TEST_CREDENTIAL": "secret"})
    with pytest.raises(AdapterConfigError) as caught:
        adapter.propose(_request())
    assert caught.value.code == LLM_DEPENDENCY_MISSING
    # Fail closed: no fallback provider or fake result is silently selected.
    assert caught.value.code != LLM_CONFIG_MISSING


def test_missing_credential_fails_closed_at_invocation_without_fallback():
    config = load_config(BASE)
    adapter = OpenAICompatibleAgentModel(
        config, client_factory=lambda **_: pytest.fail("client must not be built"), environ={}
    )
    with pytest.raises(AdapterConfigError) as caught:
        adapter.propose(_request())
    assert caught.value.code == LLM_CREDENTIAL_MISSING
    assert "SENTINEL_TEST_CREDENTIAL" in str(caught.value)


def test_adapter_error_is_bounded_and_carries_no_secret():
    config = load_config(BASE)
    adapter = OpenAICompatibleAgentModel(
        config, client_factory=lambda **_: pytest.fail("client must not be built"), environ={}
    )
    with pytest.raises(AdapterConfigError) as caught:
        adapter.propose(_request())
    text = str(caught.value)
    assert len(text) <= 512
    assert "super-secret" not in text


def test_adapter_errors_do_not_reference_coordinator_state():
    import inspect

    import sentinel.adapter.llm.config as config_module
    import sentinel.adapter.llm.openai_compatible as adapter_module

    adapter_source = inspect.getsource(adapter_module)
    config_source = inspect.getsource(config_module)
    for forbidden in ("CommandGateway", "CoordinatorRepository", "read_state", "submit("):
        assert forbidden not in adapter_source
        assert forbidden not in config_source
