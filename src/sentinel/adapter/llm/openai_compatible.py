"""OpenAI-compatible Chat Completions adapter with injected client seams."""

from __future__ import annotations

from collections.abc import Mapping
import json
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

# D8: bounded adapter-side history. Kept only in memory, per agent_run_id, so a
# multi-turn provider conversation can see its own prior actions and their
# gateway results without the coordinator's wire schema growing a history
# field. Both caps are enforced on every append.
_DEFAULT_HISTORY_TURNS = 4
_HISTORY_CHAR_BUDGET = 12_000


class OpenAICompatibleAgentModel:
    """An injected, one-turn Chat Completions implementation of AgentModelPort."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
        base_url: str | None = None,
        environ: Mapping[str, str] | None = None,
        history_turns: int = _DEFAULT_HISTORY_TURNS,
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
        self.history_turns = max(1, history_turns)
        # agent_run_id -> [(AgentToolCall, agent_tool_result dict | None), ...]
        self._history: dict[str, list[tuple[Any, Any]]] = {}
        # agent_run_id -> the AgentToolCall issued last turn, awaiting the
        # gateway result that will arrive as the *next* request's previous_result.
        self._pending: dict[str, Any] = {}

    def _record_history(self, agent_run_id: str, call: Any, result: Any) -> None:
        entries = self._history.setdefault(agent_run_id, [])
        entries.append((call, result))
        del entries[: max(0, len(entries) - self.history_turns)]
        while len(entries) > 1 and len(json.dumps(entries, default=str)) > _HISTORY_CHAR_BUDGET:
            entries.pop(0)

    def _forget(self, agent_run_id: str) -> None:
        self._history.pop(agent_run_id, None)
        self._pending.pop(agent_run_id, None)

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
                "diagnostic": f"PROVIDER_ERROR:{name}"[:512],
            }
        )

    def propose(
        self,
        request: AgentRequest,
        read_tools: Any = None,
    ) -> AgentModelResult:
        """Call the provider once; tool calls are parsed, never executed here.

        D8: if `request.previous_result` answers the call this instance made
        last turn for the same agent_run_id, that (call, result) pair joins
        the bounded in-memory history and is replayed as chat turns ahead of
        the current context. History is instance-local, keyed by
        agent_run_id, and is forgotten once the model proposes `finish` --
        the one tool call after which AgentLoop never calls this agent_run_id
        again.
        """
        if not isinstance(request, AgentRequest):
            raise TypeError("request must be an AgentRequest")
        agent_run_id = request.agent_run_id
        pending_call = self._pending.pop(agent_run_id, None)
        if pending_call is not None and request.previous_result is not None:
            self._record_history(agent_run_id, pending_call, request.previous_result)
        payload = build_chat_request(
            request,
            prompt_revision=self.config.prompt_revision,
            tool_policy_revision=self.config.tool_policy_revision,
            history=self._history.get(agent_run_id, ()),
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
                # V0 accepts exactly one tool call per turn; parser and
                # agent_loop one-call checks remain as defense-in-depth.
                parallel_tool_calls=False,
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
                    "diagnostic": "MALFORMED_RESPONSE",
                }
            )
        result = parse_chat_completion(mapped, agent_run_id=request.agent_run_id)
        if result.status == "TOOL_CALLS" and len(result.tool_calls) == 1:
            call = result.tool_calls[0]
            if call.tool_name == "finish":
                self._forget(agent_run_id)
            else:
                self._pending[agent_run_id] = call
        return result


__all__ = ["ENDPOINTS", "OpenAICompatibleAgentModel"]
