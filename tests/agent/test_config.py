import pytest

from sentinel.adapter.llm.config import (
    LLM_CONFIG_INVALID,
    LLM_CONFIG_MISSING,
    LLM_CREDENTIAL_MISSING,
    AdapterConfigError,
    load_config,
)


BASE = {
    "SENTINEL_LLM_PROVIDER": "openai",
    "SENTINEL_LLM_MODEL": "gpt-test",
    "SENTINEL_LLM_TIMEOUT_MS": "15000",
    "SENTINEL_LLM_PROMPT_REVISION": "prompt-v1",
    "SENTINEL_LLM_TOOL_POLICY_REVISION": "tools-v1",
    "SENTINEL_LLM_API_KEY_ENV": "OPENAI_API_KEY",
}


def test_missing_required_variable_has_exact_code_and_name():
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


def test_blank_non_integer_and_out_of_range_values_are_invalid():
    for variable, value in (
        ("SENTINEL_LLM_PROVIDER", ""),
        ("SENTINEL_LLM_MODEL", " "),
        ("SENTINEL_LLM_TIMEOUT_MS", "nope"),
        ("SENTINEL_LLM_TIMEOUT_MS", "0"),
        ("SENTINEL_LLM_TIMEOUT_MS", "300001"),
        ("SENTINEL_LLM_PROMPT_REVISION", ""),
        ("SENTINEL_LLM_TOOL_POLICY_REVISION", ""),
        ("SENTINEL_LLM_API_KEY_ENV", "bad-name"),
    ):
        env = dict(BASE)
        env[variable] = value
        with pytest.raises(AdapterConfigError) as caught:
            load_config(env)
        assert caught.value.code == LLM_CONFIG_INVALID


def test_config_retains_only_credential_environment_name():
    config = load_config(BASE)
    assert config.api_key_env == "OPENAI_API_KEY"
    assert "OPENAI_API_KEY" in repr(config)
    assert "super-secret" not in repr(config)
    assert "api_key" not in config.to_dict()
    assert config.to_dict()["api_key_env"] == "OPENAI_API_KEY"


def test_missing_referenced_credential_is_detected_only_on_resolution():
    config = load_config(BASE)
    with pytest.raises(AdapterConfigError) as caught:
        config.resolve_api_key({})
    assert caught.value.code == LLM_CREDENTIAL_MISSING
    assert "OPENAI_API_KEY" in str(caught.value)
    assert "super-secret" not in str(caught.value)


def test_no_credential_reference_is_allowed_for_local_compatible_provider():
    env = dict(BASE)
    env.pop("SENTINEL_LLM_API_KEY_ENV")
    assert load_config(env).resolve_api_key({}) is None


@pytest.mark.parametrize(
    ("variable", "limit"),
    [
        ("SENTINEL_LLM_PROVIDER", 128),
        ("SENTINEL_LLM_MODEL", 256),
        ("SENTINEL_LLM_PROMPT_REVISION", 128),
        ("SENTINEL_LLM_TOOL_POLICY_REVISION", 128),
    ],
)
def test_text_configuration_values_over_contract_max_length_are_invalid(variable, limit):
    env = dict(BASE)
    env[variable] = "x" * (limit + 1)
    with pytest.raises(AdapterConfigError) as caught:
        load_config(env)
    assert caught.value.code == LLM_CONFIG_INVALID
    assert variable in str(caught.value)
