"""Bounded semantic judgment port (Jev experiment, shadow-only).

Policy code consumes judge OUTPUT as evidence; it never hands the judge
authority over mission-state transitions or flight actions.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


class BoundedJudge(Protocol):
    def boolean(self, state: dict, instruction: str, *, name: str) -> dict[str, Any]: ...
    def choice(self, state: dict, instruction: str, *, options: Sequence[str], name: str) -> dict[str, Any]: ...


class StaticJudge:
    """Deterministic test double; also the safe default judge."""

    def __init__(self, booleans: dict[str, tuple[bool, float]] | None = None,
                 choices: dict[str, tuple[str, dict[str, float]]] | None = None) -> None:
        self._booleans = booleans or {}
        self._choices = choices or {}

    def boolean(self, state: dict, instruction: str, *, name: str) -> dict[str, Any]:
        value, probability = self._booleans[name]
        return {"value": value, "probability": probability}

    def choice(self, state: dict, instruction: str, *, options: Sequence[str], name: str) -> dict[str, Any]:
        value, probabilities = self._choices[name]
        if set(probabilities) != set(options):
            raise ValueError(f"choice '{name}' probabilities {sorted(probabilities)} != options {sorted(options)}")
        return {"value": value, "probabilities": probabilities}


class JudgeUnavailable(RuntimeError):
    """Raised/recorded when a remote judge cannot produce a validated answer."""


class JevJudge:
    """Adapter for typesafe-ai/jev via the Vercel AI Gateway (OpenAI-compatible).

    Fail-closed: any transport/validation failure raises JudgeUnavailable and the
    caller records it; the demo path never depends on this adapter.
    """

    DEFAULT_MODEL = "typesafe-ai/jev"
    MODEL_ENV = "SENTINEL_SYSTEM_ONE_MODEL"

    def __init__(self, api_key_env: str, base_url: str = "https://ai-gateway.vercel.sh",
                 model: str | None = None,
                 timeout_s: float = 5.0, transport=None) -> None:
        if not api_key_env:
            raise ValueError("api_key_env must name an environment variable (never embed the key)")
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model or os.environ.get(self.MODEL_ENV, self.DEFAULT_MODEL)
        self._timeout_s = timeout_s
        self._transport = transport  # injected callable for tests; real one built lazily

    def _post(self, payload: dict) -> dict:
        import httpx
        key = os.environ.get(self._api_key_env)
        if not key:
            raise JudgeUnavailable(f"env var {self._api_key_env} not set")
        transport = self._transport
        if transport is None:
            resp = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload, timeout=self._timeout_s,
            )
            resp.raise_for_status()
            return resp.json()
        return transport(payload)

    def boolean(self, state: dict, instruction: str, *, name: str) -> dict[str, Any]:
        raw = self._post(_chat_payload(self._model, state, [
            {"name": name, "type": "boolean", "instructions": instruction}]))
        answer = _extract(raw, name, kind="boolean")
        prob = float(answer["probability"])
        if not 0.0 <= prob <= 1.0:
            raise JudgeUnavailable(f"{name}: probability out of range: {prob}")
        return {"value": bool(answer["value"]), "probability": prob}

    def choice(self, state: dict, instruction: str, *, options: Sequence[str], name: str) -> dict[str, Any]:
        raw = self._post(_chat_payload(self._model, state, [
            {"name": name, "type": "choice", "options": list(options), "instructions": instruction}]))
        answer = _extract(raw, name, kind="choice")
        value = answer["value"]
        probs = {k: float(v) for k, v in answer["probabilities"].items()}
        if value not in options:
            raise JudgeUnavailable(f"{name}: value {value!r} not in options")
        if set(probs) != set(options):
            raise JudgeUnavailable(f"{name}: probability keys != options")
        return {"value": value, "probabilities": probs}


def _chat_payload(model: str, state: dict, questions: list[dict]) -> dict:
    import json
    system = (
        "You are a bounded judgment model. Answer ONLY with a JSON object "
        "mapping each question name to {value, probability(s)}. No prose."
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"state": state, "questions": questions})},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


def _extract(raw: dict, name: str, kind: str) -> dict:
    try:
        content = raw["choices"][0]["message"]["content"]
        parsed = json.loads(content)  # type: ignore[name-defined]
        answer = parsed[name]
    except Exception as exc:  # untrusted upstream -> one exception class
        raise JudgeUnavailable(f"unparseable judge response for {name}: {exc}") from exc
    if kind == "choice" and "probabilities" not in answer:
        raise JudgeUnavailable(f"{name}: missing probabilities")
    return answer


def timed(judge_call) -> tuple[dict, int]:
    """Wrap a judge call, returning (result, latency_ms)."""
    start = time.perf_counter()
    result = judge_call()
    return result, int((time.perf_counter() - start) * 1000)
