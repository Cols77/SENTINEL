"""Strict, secret-safe configuration for the LLM adapter."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from pathlib import Path
from typing import Mapping


LLM_CONFIG_MISSING = "LLM_CONFIG_MISSING"
LLM_CONFIG_INVALID = "LLM_CONFIG_INVALID"
LLM_DEPENDENCY_MISSING = "LLM_DEPENDENCY_MISSING"
LLM_CREDENTIAL_MISSING = "LLM_CREDENTIAL_MISSING"

_REQUIRED_ENV = (
    "SENTINEL_LLM_PROVIDER",
    "SENTINEL_LLM_MODEL",
    "SENTINEL_LLM_TIMEOUT_MS",
    "SENTINEL_LLM_PROMPT_REVISION",
    "SENTINEL_LLM_TOOL_POLICY_REVISION",
)
_API_KEY_ENV = "SENTINEL_LLM_API_KEY_ENV"
_API_KEY_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_DECIMAL = re.compile(r"^[0-9]+$")
_FIELD_MAX_LENGTHS = {
    "SENTINEL_LLM_PROVIDER": 128,
    "SENTINEL_LLM_MODEL": 256,
    "SENTINEL_LLM_PROMPT_REVISION": 128,
    "SENTINEL_LLM_TOOL_POLICY_REVISION": 128,
}


class AdapterConfigError(ValueError):
    """An adapter-local configuration, dependency, or credential failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LLMConfig:
    """Validated configuration; credential material is deliberately absent."""

    config_version: str
    provider: str
    model: str
    timeout_ms: int
    prompt_revision: str
    tool_policy_revision: str
    api_key_env: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "config_version": self.config_version,
            "provider": self.provider,
            "model": self.model,
            "timeout_ms": self.timeout_ms,
            "prompt_revision": self.prompt_revision,
            "tool_policy_revision": self.tool_policy_revision,
        }
        if self.api_key_env is not None:
            result["api_key_env"] = self.api_key_env
        return result

    def resolve_api_key(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Resolve the named credential only at provider invocation time."""
        if self.api_key_env is None:
            return None
        source = os.environ if environ is None else environ
        value = source.get(self.api_key_env)
        if value is None or value == "":
            raise AdapterConfigError(
                LLM_CREDENTIAL_MISSING,
                f"credential environment variable {self.api_key_env} is not set",
            )
        return value


# A short alias keeps adapter-local callers readable without creating another
# configuration shape.
AdapterConfig = LLMConfig


def _missing(variable: str) -> AdapterConfigError:
    return AdapterConfigError(LLM_CONFIG_MISSING, f"missing {variable}")


def _invalid(variable: str, reason: str = "invalid value") -> AdapterConfigError:
    return AdapterConfigError(LLM_CONFIG_INVALID, f"{variable}: {reason}")


def _required_value(
    environ: Mapping[str, str], variable: str, *, max_length: int | None = None
) -> str:
    if variable not in environ or environ[variable] is None:
        raise _missing(variable)
    value = environ[variable]
    if not isinstance(value, str):
        raise _invalid(variable)
    if not value.strip():
        raise _invalid(variable, "must not be blank")
    if max_length is not None and len(value) > max_length:
        raise _invalid(variable, f"must be at most {max_length} characters")
    return value


def load_config(environ: Mapping[str, str] | None = None) -> LLMConfig:
    """Load and validate the documented environment configuration.

    The supplied mapping is used only while loading.  The returned object keeps
    the API-key environment-variable *name*, never the value.

    When ``environ`` is None the in-process path auto-populates ``os.environ``
    from the nearest ``.env`` file first (existing variables win).  When a
    mapping is injected it is strictly authoritative: the ``.env`` file is
    NOT consulted and ``os.environ`` is never touched.
    """
    if environ is None:
        from sentinel.env_file import load_env_file

        load_env_file()
        source: Mapping[str, str] = os.environ
    else:
        source = dict(environ)
    values = {
        variable: _required_value(
            source, variable, max_length=_FIELD_MAX_LENGTHS.get(variable)
        )
        for variable in _REQUIRED_ENV
    }

    timeout_text = values["SENTINEL_LLM_TIMEOUT_MS"]
    if not _DECIMAL.fullmatch(timeout_text):
        raise _invalid("SENTINEL_LLM_TIMEOUT_MS", "must be an integer")
    timeout_ms = int(timeout_text)
    if not 1 <= timeout_ms <= 300_000:
        raise _invalid("SENTINEL_LLM_TIMEOUT_MS", "must be between 1 and 300000")

    api_key_env = source.get(_API_KEY_ENV)
    if api_key_env is not None:
        if not isinstance(api_key_env, str) or not _API_KEY_NAME.fullmatch(api_key_env):
            raise _invalid(_API_KEY_ENV, "must be an uppercase environment-variable name")

    return LLMConfig(
        config_version="llm-adapter-config.v1",
        provider=values["SENTINEL_LLM_PROVIDER"],
        model=values["SENTINEL_LLM_MODEL"],
        timeout_ms=timeout_ms,
        prompt_revision=values["SENTINEL_LLM_PROMPT_REVISION"],
        tool_policy_revision=values["SENTINEL_LLM_TOOL_POLICY_REVISION"],
        api_key_env=api_key_env,
    )


__all__ = [
    "AdapterConfig",
    "AdapterConfigError",
    "LLMConfig",
    "LLM_CONFIG_INVALID",
    "LLM_CONFIG_MISSING",
    "LLM_CREDENTIAL_MISSING",
    "LLM_DEPENDENCY_MISSING",
    "load_config",
]
