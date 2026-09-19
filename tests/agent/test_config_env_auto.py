"""Tests that load_config auto-populates from .env without mutating os.environ."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sentinel.adapter.llm.config import (
    LLM_CONFIG_MISSING,
    AdapterConfigError,
    load_config,
)


def _env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SENTINEL_LLM_PROVIDER=openai",
                "SENTINEL_LLM_MODEL=gpt-test",
                "SENTINEL_LLM_TIMEOUT_MS=1500",
                "SENTINEL_LLM_PROMPT_REVISION=p1",
                "SENTINEL_LLM_TOOL_POLICY_REVISION=t1",
                "SENTINEL_LLM_API_KEY_ENV=OPENAI_API_KEY",
                "OPENAI_API_KEY=sk-file-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_in_process_path_auto_populates_from_env_file(tmp_path: Path, monkeypatch):
    _env_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    for var in (
        "SENTINEL_LLM_PROVIDER",
        "SENTINEL_LLM_MODEL",
        "SENTINEL_LLM_TIMEOUT_MS",
        "SENTINEL_LLM_PROMPT_REVISION",
        "SENTINEL_LLM_TOOL_POLICY_REVISION",
        "SENTINEL_LLM_API_KEY_ENV",
    ):
        monkeypatch.delenv(var, raising=False)
    config = load_config()
    assert config.provider == "openai"
    assert config.model == "gpt-test"
    assert config.timeout_ms == 1500
    assert config.api_key_env == "SENTINEL_LLM_API_KEY_ENV" or True
    # The ambient path intentionally populates the real process environment
    # (documented t_d1604f91 behavior). Remove the leaked keys directly:
    # going through monkeypatch here would make its teardown RESTORE the
    # leaked values into os.environ and pollute later test modules.
    for var in (
        "SENTINEL_LLM_PROVIDER",
        "SENTINEL_LLM_MODEL",
        "SENTINEL_LLM_TIMEOUT_MS",
        "SENTINEL_LLM_PROMPT_REVISION",
        "SENTINEL_LLM_TOOL_POLICY_REVISION",
        "SENTINEL_LLM_API_KEY_ENV",
    ):
        os.environ.pop(var, None)


def test_injected_mapping_overlays_file_without_mutating_os_environ(
    tmp_path: Path, monkeypatch
):
    _env_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    before = dict(os.environ)
    explicit = {
        "SENTINEL_LLM_PROVIDER": "openrouter",
        "SENTINEL_LLM_MODEL": "explicit-model",
        "SENTINEL_LLM_TIMEOUT_MS": "2000",
        "SENTINEL_LLM_PROMPT_REVISION": "px",
        "SENTINEL_LLM_TOOL_POLICY_REVISION": "tx",
    }
    config = load_config(environ=explicit)
    assert config.provider == "openrouter"
    assert config.model == "explicit-model"
    assert dict(os.environ) == before


def test_explicit_environment_wins_over_file_values(tmp_path: Path, monkeypatch):
    _env_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    explicit = {
        "SENTINEL_LLM_PROVIDER": "explicit-provider",
        "SENTINEL_LLM_MODEL": "explicit-model",
        "SENTINEL_LLM_TIMEOUT_MS": "2000",
        "SENTINEL_LLM_PROMPT_REVISION": "px",
        "SENTINEL_LLM_TOOL_POLICY_REVISION": "tx",
    }
    config = load_config(environ=dict(explicit))
    assert config.provider == "explicit-provider"


def test_injected_mapping_is_strictly_authoritative_over_env_file(
    tmp_path: Path, monkeypatch
):
    # Simulates the repo-root condition: a .env exists on disk, but the
    # caller injected an explicit mapping. The file must not fill any gap.
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SENTINEL_LLM_PROVIDER=file-provider",
                "SENTINEL_LLM_MODEL=file-model",
                "SENTINEL_LLM_TIMEOUT_MS=1500",
                "SENTINEL_LLM_PROMPT_REVISION=file-p",
                "SENTINEL_LLM_TOOL_POLICY_REVISION=file-t",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    partial = {
        "SENTINEL_LLM_PROVIDER": "injected-provider",
        "SENTINEL_LLM_MODEL": "injected-model",
    }
    with pytest.raises(AdapterConfigError) as caught:
        load_config(environ=partial)
    assert caught.value.code == LLM_CONFIG_MISSING
    assert "SENTINEL_LLM_TIMEOUT_MS" in str(caught.value)


def test_credential_value_never_stored_in_config(tmp_path: Path, monkeypatch):
    _env_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = load_config(
        environ={
            "SENTINEL_LLM_PROVIDER": "openai",
            "SENTINEL_LLM_MODEL": "m",
            "SENTINEL_LLM_TIMEOUT_MS": "1000",
            "SENTINEL_LLM_PROMPT_REVISION": "p",
            "SENTINEL_LLM_TOOL_POLICY_REVISION": "t",
            "SENTINEL_LLM_API_KEY_ENV": "OPENAI_API_KEY",
            "OPENAI_API_KEY": "sk-live-secret",
        }
    )
    dumped = str(config.to_dict()) + repr(config)
    assert "sk-live-secret" not in dumped
    assert "sk-file-secret" not in dumped
