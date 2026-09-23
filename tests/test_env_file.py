"""Tests for the dependency-free .env file loader (t_d1604f91)."""

from __future__ import annotations

from pathlib import Path

from sentinel.env_file import find_env_file, load_env_file


def _write_env(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_parses_comments_and_quoted_values(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in (
        "SENTINEL_LLM_PROVIDER",
        "SENTINEL_LLM_MODEL",
        "SENTINEL_LLM_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    _write_env(
        tmp_path / ".env",
        "# comment line\n"
        "\n"
        "SENTINEL_LLM_PROVIDER=openai  # trailing comment\n"
        "SENTINEL_LLM_MODEL=\"quoted-model\"\n"
        "SENTINEL_LLM_TIMEOUT_MS='2500'\n",
    )
    loaded = load_env_file(search_dir=tmp_path)
    assert loaded == {
        "SENTINEL_LLM_PROVIDER": "openai",
        "SENTINEL_LLM_MODEL": "quoted-model",
        "SENTINEL_LLM_TIMEOUT_MS": "2500",
    }


def test_searches_cwd_upward(tmp_path: Path, monkeypatch):
    parent = tmp_path
    child = parent / "sub" / "deep"
    child.mkdir(parents=True)
    _write_env(parent / ".env", "SENTINEL_LLM_PROVIDER=root\n")
    monkeypatch.chdir(child)
    assert find_env_file(start_dir=child) == parent / ".env"


def test_populates_only_unset_variables(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_env(
        tmp_path / ".env",
        "SENTINEL_TEST_FILE_ONLY=file\nSENTINEL_TEST_ENV_WINS=env-wins\n",
    )
    monkeypatch.setenv("SENTINEL_TEST_ENV_WINS", "env-wins")
    import os

    monkeypatch.delenv("SENTINEL_TEST_FILE_ONLY", raising=False)
    loaded = load_env_file(search_dir=tmp_path, environ=dict(os.environ))
    assert loaded == {"SENTINEL_TEST_FILE_ONLY": "file"}
    assert os.environ["SENTINEL_TEST_ENV_WINS"] == "env-wins"


def test_missing_file_returns_empty_and_does_not_raise(tmp_path: Path):
    loaded = load_env_file(search_dir=tmp_path)
    assert loaded == {}
    assert find_env_file(start_dir=tmp_path) is None


def test_explicit_environment_wins_over_file(tmp_path: Path):
    _write_env(tmp_path / ".env", "SENTINEL_LLM_PROVIDER=file\n")
    loaded = load_env_file(
        search_dir=tmp_path, environ={"SENTINEL_LLM_PROVIDER": "explicit"}
    )
    assert loaded == {}


def test_values_never_logged(tmp_path: Path, caplog):
    import logging

    _write_env(tmp_path / ".env", "OPENAI_API_KEY=sk-secret-value\n")
    with caplog.at_level(logging.DEBUG):
        load_env_file(search_dir=tmp_path)
    assert all("sk-secret-value" not in r.getMessage() for r in caplog.records)
