"""Bounded semantic judgment port (Jev experiment, shadow-only).

Policy code consumes judge OUTPUT as evidence; it never hands the judge
authority over mission-state transitions or flight actions.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol, Sequence


class BoundedJudge(Protocol):
    def boolean(self, state: dict, instruction: str, *, name: str) -> dict[str, Any]: ...
    def choice(self, state: dict, instruction: str, *, options: Sequence[str], name: str) -> dict[str, Any]: ...
    def evaluate(self, state: dict, questions: list[dict]) -> dict[str, dict[str, Any]]: ...


class StaticJudge:
    """Deterministic test double; also the safe default judge.

    Pre-computed ``answers`` take precedence over the per-primitive rule
    tables (booleans/choices/scores) so a scenario can pin a full batched
    response verbatim.
    """

    def __init__(self,
                 booleans: dict[str, tuple[bool, float]] | None = None,
                 choices: dict[str, tuple[str, dict[str, float]]] | None = None,
                 scores: dict[str, float] | None = None,
                 answers: dict[str, dict[str, Any]] | None = None,
                 boolean_threshold: float = 0.5) -> None:
        self._booleans = booleans or {}
        self._choices = choices or {}
        self._scores = scores or {}
        self._answers = answers or {}
        self.boolean_threshold = boolean_threshold

    def evaluate(self, state: dict, questions: list[dict]) -> dict[str, dict[str, Any]]:
        answers: dict[str, dict[str, Any]] = {}
        for q in questions:
            name, kind = q["name"], q["type"]
            if name in self._answers:
                answers[name] = self._answers[name]
                continue
            if kind == "noul":
                if name not in self._booleans:
                    continue  # deterministic double answers only what is configured
                value, probability = self._booleans[name]
                answers[name] = {"value": value, "probability": probability}
            elif kind == "choice":
                if name not in self._choices:
                    continue
                value, probabilities = self._choices[name]
                if set(probabilities) != set(q["criteria"] or {}):
                    raise ValueError(f"choice '{name}' probabilities {sorted(probabilities)} != criteria {sorted(q['criteria'] or {})}")
                answers[name] = {"value": value, "probabilities": probabilities, "confidence": None}
            elif kind == "score":
                if name not in self._scores:
                    continue
                value = self._scores[name]
                legend = {str(i): desc for i, desc in enumerate(q["criteria"])}
                answers[name] = {"value": value, "legend": legend, "confidence": None}
            else:
                raise ValueError(f"unknown question type: {kind!r}")
        return answers

    def boolean(self, state: dict, instruction: str, *, name: str) -> dict[str, Any]:
        return self.evaluate(state, [{"name": name, "type": "noul", "instruction": instruction, "criteria": None}])[name]

    def choice(self, state: dict, instruction: str, *, options: Sequence[str], name: str) -> dict[str, Any]:
        return self.evaluate(state, [{"name": name, "type": "choice", "instruction": instruction,
                                      "criteria": {opt: None for opt in options}}])[name]


class JudgeUnavailable(RuntimeError):
    """Raised/recorded when a remote judge cannot produce a validated answer."""


class JevJudge:
    """Adapter for typesafe-ai/jev decisions API (batched, parallel questions).

    One evaluate() call sends ALL questions in a single request to
    POST {base_url}/api/alpha/decisions. Fail-closed: any transport or
    validation failure raises JudgeUnavailable and the caller records it;
    the demo path never depends on this adapter.

    boolean_threshold is POLICY, not model output: P(noul) and
    1-P(negated noul) are NOT complementary, and thresholds must not be
    reused across primitives.
    """

    DEFAULT_MODEL = "typesafe-ai/jev"
    MODEL_ENV = "SENTINEL_SYSTEM_ONE_MODEL"
    ENDPOINT_PATH = "/api/alpha/decisions"

    def __init__(self, api_key_env: str, base_url: str = "https://ai-gateway.vercel.sh",
                 model: str | None = None, timeout_s: float = 5.0,
                 boolean_threshold: float = 0.5, transport=None) -> None:
        if not api_key_env:
            raise ValueError("api_key_env must name an environment variable (never embed the key)")
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model or os.environ.get(self.MODEL_ENV, self.DEFAULT_MODEL)
        self._timeout_s = timeout_s
        self.boolean_threshold = boolean_threshold
        self._transport = transport  # injected callable for tests; real one built lazily

    @property
    def endpoint_path(self) -> str:
        return self.ENDPOINT_PATH

    def _post(self, payload: dict) -> dict:
        key = os.environ.get(self._api_key_env)
        if not key:
            raise JudgeUnavailable(f"env var {self._api_key_env} not set")
        transport = self._transport
        if transport is None:
            import httpx
            resp = httpx.post(
                f"{self._base_url}{self.ENDPOINT_PATH}",
                headers={"Authorization": f"Bearer {key}"},
                json=payload, timeout=self._timeout_s,
            )
            resp.raise_for_status()
            return resp.json()
        return transport(payload)

    def evaluate(self, state: dict, questions: list[dict]) -> dict[str, dict[str, Any]]:
        wire = {}
        for q in questions:
            wire[q["name"]] = {"type": q["type"], "instructions": q["instruction"], "criteria": q["criteria"]}
        raw = self._post({"model": self._model, "state": state, "questions": wire})
        try:
            responses = raw["answers"]
        except (KeyError, TypeError) as exc:
            raise JudgeUnavailable(f"unparseable judge response: {exc}") from exc
        answers: dict[str, dict[str, Any]] = {}
        for q in questions:
            name, kind = q["name"], q["type"]
            if name not in responses:
                raise JudgeUnavailable(f"missing answer for {name!r}")
            try:
                answer = responses[name]
                if kind == "noul":
                    prob = float(answer["noul"])
                    if not 0.0 <= prob <= 1.0:
                        raise JudgeUnavailable(f"{name}: noul probability out of range: {prob}")
                    answers[name] = {"value": prob >= self.boolean_threshold, "probability": prob}
                elif kind == "choice":
                    value = answer["choice"]
                    criteria = q["criteria"] or {}
                    if value not in criteria:
                        raise JudgeUnavailable(f"{name}: choice {value!r} not in criteria")
                    answers[name] = {"value": value, "probabilities": {k: float(v) for k, v in answer["probabilities"].items()},
                                     "confidence": answer.get("confidence")}
                elif kind == "score":
                    answers[name] = {"value": float(answer["score"]),
                                     "legend": dict(answer.get("legend", {})),
                                     "probabilities": {k: float(v) for k, v in answer.get("probabilities", {}).items()},
                                     "confidence": answer.get("confidence")}
                else:
                    raise JudgeUnavailable(f"unknown question type: {kind!r}")
            except JudgeUnavailable:
                raise
            except Exception as exc:  # untrusted upstream -> one exception class
                raise JudgeUnavailable(f"invalid answer for {name}: {exc}") from exc
        return answers

    def boolean(self, state: dict, instruction: str, *, name: str) -> dict[str, Any]:
        return self.evaluate(state, [{"name": name, "type": "noul", "instruction": instruction, "criteria": None}])[name]

    def choice(self, state: dict, instruction: str, *, options: Sequence[str], name: str) -> dict[str, Any]:
        return self.evaluate(state, [{"name": name, "type": "choice", "instruction": instruction,
                                      "criteria": {opt: None for opt in options}}])[name]


class LlmJudge:
    """General LLM baseline judge over an OpenAI-compatible chat/completions API.

    Same port, same answer shapes, and same fail-closed contract as JevJudge —
    this is the experiment's baseline arm. One evaluate() call sends ALL
    questions in a single JSON-mode chat request to
    POST {base_url}/v1/chat/completions.

    The 'confidence' field is the model's 0..1 SELF-assessment; unlike Jev's
    calibrated probabilities it is NOT calibrated — the experiment documents
    this asymmetry explicitly.
    """

    SYSTEM_INSTRUCTIONS = (
        "You are a judgment module answering fixed questions about one observation "
        "in a mountain-resort opening workflow. Reply ONLY with a single JSON object "
        'of the form {"answers": {"<question name>": {...}, ...}} containing EXACTLY '
        "one entry per requested question name (no extras). For each answer include "
        "a 'confidence' field: your self-assessed confidence (0..1) in that answer. "
        "Answer every question using ONLY the provided state; never request tools."
    )

    def __init__(self, api_key_env: str, base_url: str, model: str, timeout_s: float = 5.0,
                 boolean_threshold: float = 0.5, transport=None) -> None:
        if not api_key_env:
            raise ValueError("api_key_env must name an environment variable (never embed the key)")
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self.boolean_threshold = boolean_threshold
        self._transport = transport  # injected callable for tests; real one built lazily

    @property
    def endpoint_path(self) -> str:
        return "/v1/chat/completions"

    def _post(self, payload: dict) -> dict:
        key = os.environ.get(self._api_key_env)
        if not key:
            raise JudgeUnavailable(f"env var {self._api_key_env} not set")
        try:
            if self._transport is not None:
                return self._transport(f"{self._base_url}/v1/chat/completions",
                                       {"Authorization": f"Bearer {key}"}, payload)
            import httpx
            resp = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload, timeout=self._timeout_s,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            raise JudgeUnavailable(f"chat/completions request failed: {exc}") from exc

    def evaluate(self, state: dict, questions: list[dict]) -> dict[str, dict[str, Any]]:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": json.dumps({"state": state, "questions": questions})},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        raw = self._post(payload)
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise JudgeUnavailable(f"unparseable chat response: {exc}") from exc
        try:
            parsed = json.loads(content)
            responses = parsed["answers"]
            if not isinstance(responses, dict):
                raise TypeError("'answers' must be an object")
        except Exception as exc:
            raise JudgeUnavailable(f"non-JSON or malformed judge content: {exc}") from exc
        expected = {q["name"] for q in questions}
        got = set(responses)
        if got != expected:
            raise JudgeUnavailable(f"answer names {sorted(got)} != requested {sorted(expected)}")
        answers: dict[str, dict[str, Any]] = {}
        for q in questions:
            name, kind = q["name"], q["type"]
            try:
                answer = responses[name]
                if kind == "noul":
                    prob = float(answer["probability"])
                    if not 0.0 <= prob <= 1.0:
                        raise JudgeUnavailable(f"{name}: probability out of range: {prob}")
                    answers[name] = {"value": prob >= self.boolean_threshold, "probability": prob}
                elif kind == "choice":
                    criteria = q["criteria"] or {}
                    value = answer["choice"]
                    if value not in criteria:
                        raise JudgeUnavailable(f"{name}: choice {value!r} not in criteria")
                    answers[name] = {"value": value, "probabilities": {k: float(v) for k, v in answer["probabilities"].items()},
                                     "confidence": answer.get("confidence")}
                elif kind == "score":
                    answers[name] = {"value": float(answer["score"]),
                                     "probabilities": {k: float(v) for k, v in answer.get("probabilities", {}).items()},
                                     "confidence": answer.get("confidence")}
                else:
                    raise JudgeUnavailable(f"unknown question type: {kind!r}")
            except JudgeUnavailable:
                raise
            except Exception as exc:  # untrusted upstream -> one exception class
                raise JudgeUnavailable(f"invalid answer for {name}: {exc}") from exc
        return answers

    def boolean(self, state: dict, instruction: str, *, name: str) -> dict[str, Any]:
        return self.evaluate(state, [{"name": name, "type": "noul", "instruction": instruction, "criteria": None}])[name]

    def choice(self, state: dict, instruction: str, *, options: Sequence[str], name: str) -> dict[str, Any]:
        return self.evaluate(state, [{"name": name, "type": "choice", "instruction": instruction,
                                      "criteria": {opt: None for opt in options}}])[name]


def timed(judge_call) -> tuple[dict, int]:
    """Wrap a judge call, returning (result, latency_ms)."""
    start = time.perf_counter()
    result = judge_call()
    return result, int((time.perf_counter() - start) * 1000)


CONTEXT_VERSION = "judgment_context.v1"


def state_snapshot(mountain_state: dict, observation: dict) -> dict:
    """Pure projection: Mountain State + raw observation -> judgment_context.v1.

    Resolves all facts the judge must not have to derive itself (duplicates,
    patroller availability). Any judgment requiring a tool call to answer is
    the wrong question for a System One model — route it to the LLM planner.
    """
    haz = next((h for h in mountain_state.get("hazards", [])
                if h.get("id") == observation.get("detection_id")), None)
    if haz is None:
        raise KeyError(f"detection {observation.get('detection_id')!r} not in mountain state")
    return {
        "context_version": CONTEXT_VERSION,
        "observation": {
            "source": observation.get("source"),
            "object": haz.get("object"),
            "perception_confidence": haz.get("confidence"),
            "position_relation": haz.get("position_relation"),
            "duplicate_of_existing_detection": bool(haz.get("resolved")),
        },
        "mission": {
            "workflow": mountain_state.get("workflow"),
            "trail_status": mountain_state.get("trail_status"),
        },
        "operations": {
            "patroller_available": any(
                p.get("status") == "available" for p in mountain_state.get("patrollers", [])),
        },
    }
