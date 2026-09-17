"""OpenAI-compatible Chat Completions adapter with injected client seams."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any, Callable

from sentinel.contracts import AgentModelResult, AgentRequest

from .config import (
    AdapterConfigError,
    LLMConfig,
    LLM_CONFIG_INVALID,
    LLM_DEPENDENCY_MISSING,
)
from .parser import parse_chat_completion
from .prompt import build_chat_request


ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class OpenAICompatibleAgentModel:
    """An injected, one-turn Chat Completions implementation of AgentModelPort."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
        base_url: str | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        if not isinstance(config, LLMConfig):
            raise TypeError("config must be an LLMConfig")
        if base_url is not None and not base_url.strip():
            raise AdapterConfigError(LLM_CONFIG_INVALID, "base_url must not be blank")
        if base_url is None and config.provider not in ENDPOINTS:
            raise AdapterConfigError(LLM_CONFIG_INVALID, f"unsupported provider {config.provider}")
        self.config = config
        self.base_url = base_url or ENDPOINTS[config.provider]
        self._client_factory = client_factory
        self._environ = environ
        self._client: Any | None = None
        self.last_request: dict[str, Any] | None = None

    def _default_client(self, api_key: str | None) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AdapterConfigError(
                LLM_DEPENDENCY_MISSING,
                "optional llm dependency openai is not installed",
            ) from exc
        # An explicit empty key prevents the SDK from consulting an unrelated
        # ambient OPENAI_API_KEY when a local compatible endpoint is selected.
        return OpenAI(api_key=api_key if api_key is not None else "", base_url=self.base_url)

    def _get_client(self, api_key: str | None) -> Any:
        if self._client is None:
            if self._client_factory is None:
                self._client = self._default_client(api_key)
            else:
                self._client = self._client_factory(
                    api_key=api_key,
                    base_url=self.base_url,
                )
        return self._client

    @staticmethod
    def _response_mapping(response: Any) -> Mapping[str, Any] | None:
        if isinstance(response, Mapping):
            return response
        for method_name in ("model_dump", "to_dict"):
            method = getattr(response, method_name, None)
            if callable(method):
                converted = method()
                if isinstance(converted, Mapping):
                    return converted
        return None

    @staticmethod
    def _unavailable(agent_run_id: str, exc: Exception) -> AgentModelResult:
        # Never include exception text: provider errors can echo request data or
        # credentials. Type names are bounded identifiers, not secret material.
        name = type(exc).__name__[:96]
        return AgentModelResult.from_dict(
            {
                "contract_version": "sentinel.v0",
                "wire_type": "agent_result",
                "agent_run_id": agent_run_id,
                "status": "UNAVAILABLE",
                "tool_calls": [],
                "diagnostic": f"PROVIDER_ERROR:{name}"[:512],
            }
        )

    def propose(
        self,
        request: AgentRequest,
        read_tools: Any = None,
    ) -> AgentModelResult:
        """Call the provider once; tool calls are parsed, never executed here."""
        if not isinstance(request, AgentRequest):
            raise TypeError("request must be an AgentRequest")
        payload = build_chat_request(
            request,
            prompt_revision=self.config.prompt_revision,
            tool_policy_revision=self.config.tool_policy_revision,
        )
        self.last_request = payload
        api_key = self.config.resolve_api_key(
            self._environ if self._environ is not None else os.environ
        )
        try:
            client = self._get_client(api_key)
            response = client.chat.completions.create(
                model=self.config.model,
                timeout=self.config.timeout_ms / 1000.0,
                messages=payload["messages"],
                tools=payload["tools"],
                tool_choice=payload["tool_choice"],
            )
        except AdapterConfigError:
            raise
        except Exception as exc:
            # This maps network, timeout, provider and injected seam failures to
            # a bounded unavailable result instead of inventing an intent.
            result = self._unavailable(request.agent_run_id, exc)
            return result

        mapped = self._response_mapping(response)
        if mapped is None:
            return AgentModelResult.from_dict(
                {
                    "contract_version": "sentinel.v0",
                    "wire_type": "agent_result",
                    "agent_run_id": request.agent_run_id,
                    "status": "MALFORMED",
                    "tool_calls": [],
                    "diagnostic": "MALFORMED_RESPONSE",
                }
            )
        return parse_chat_completion(mapped, agent_run_id=request.agent_run_id)


__all__ = ["ENDPOINTS", "OpenAICompatibleAgentModel"]
