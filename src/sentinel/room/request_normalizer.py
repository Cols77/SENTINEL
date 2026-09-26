"""The operator's request, rephrased into the goal taxonomy's vocabulary.

The goal compiler (``sentinel.room.goals``) classifies a request through
fixed label sets and answers CLARIFICATION_REQUIRED/UNSUPPORTED for phrasing
outside them ("how many cups are around?"). Before goal compilation, the live
flight may make ONE bounded GLM call that rewrites the request into one short
imperative sentence in that vocabulary ("Count the mugs in the room").

Rules:

- Paid only when authorised (the caller passes the run's
  ``--i-authorise-paid-calls`` decision); without it no request is sent.
- Bounded: the call has a hard wall-clock deadline (``DEFAULT_TIMEOUT_S``).
- ``normalize_request`` raises ``NormalizerUnavailable`` on any failure; the
  caller is fail-open and flies the operator's original text.
- It never touches the Jev chain: its output is only the request text the
  audited goal compiler then reads, and the caller records both texts.

Client, key handling and model pin follow ``sentinel.room.operator_answer``.
"""
from __future__ import annotations

import threading

from sentinel.survey.live_detect import LIVE_CLASSES
from sentinel.survey.llm_adapter import LlmAdapter, Transport

DEFAULT_MODEL = "z-ai/glm-5.3"
DEFAULT_BASE_URL = "https://openrouter.ai/api"
DEFAULT_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_TIMEOUT_S = 8.0
MAX_REQUEST_CHARS = 300

SYSTEM_INSTRUCTIONS = (
    "You rewrite an indoor drone operator's request into one short imperative sentence that a "
    "fixed goal parser understands. Use only this vocabulary. Actions: count, find, or report "
    "the state of. Object classes: " + ", ".join(LIVE_CLASSES) + " (map a synonym to the "
    "closest class only when it clearly means that class, for example 'cups' means 'mugs', "
    "'laptops' means 'computers', 'people' means 'persons'). Scope: the room. "
    "Preserve the operator's meaning exactly: never add, drop or change an object, a number, a "
    "qualifier or a place. If the request already fits this vocabulary, return it unchanged. "
    "If it cannot be expressed in this vocabulary, return the original text verbatim. "
    "Output the sentence only: no quotes, no explanation, no markdown."
)


class NormalizerUnavailable(RuntimeError):
    """Normalization did not produce a usable sentence; fly the original text."""


class _NormalizerClient(LlmAdapter):
    """The survey adapter's transport and key handling, for one plain chat completion."""

    def rewrite(self, request_text: str) -> tuple[str, str | None]:
        # Same reasoning settings as the operator answer: GLM 5.3 reasons
        # mandatorily; effort "low" keeps the reply inside max_tokens.
        raw = self._post({"model": self.model, "temperature": 0, "max_tokens": 400,
                          "reasoning": {"effort": "low", "exclude": True},
                          "messages": [{"role": "system", "content": SYSTEM_INSTRUCTIONS},
                                       {"role": "user", "content": request_text}]})
        try:
            text = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NormalizerUnavailable(f"malformed chat response: {exc}") from exc
        resolved = raw.get("model") if isinstance(raw, dict) else None
        if resolved is not None and str(resolved) != self.model:
            raise NormalizerUnavailable(
                f"provider resolved model {resolved!r}, expected {self.model!r}")
        return _clean(text), (str(resolved) if resolved is not None else None)


def _clean(text: object) -> str:
    if not isinstance(text, str):
        raise NormalizerUnavailable("empty chat response")
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise NormalizerUnavailable("empty chat response")
    sentence = lines[0]
    if len(sentence) >= 2 and sentence[0] == sentence[-1] and sentence[0] in "\"'`":
        sentence = sentence[1:-1].strip()
    if not sentence:
        raise NormalizerUnavailable("empty chat response")
    if len(sentence) > MAX_REQUEST_CHARS:
        raise NormalizerUnavailable("reply longer than a single short sentence")
    return sentence


def normalize_request(request_text: str, *, authorised: bool, model: str = DEFAULT_MODEL,
                      base_url: str = DEFAULT_BASE_URL, api_key_env: str = DEFAULT_KEY_ENV,
                      timeout_s: float = DEFAULT_TIMEOUT_S,
                      transport: Transport | None = None) -> tuple[str, str]:
    """``(normalized_text, model_id)``; raises ``NormalizerUnavailable`` on
    any failure, including no authorisation and the ``timeout_s`` deadline."""
    if not authorised:
        raise NormalizerUnavailable("paid calls not authorised")
    if not isinstance(request_text, str) or not request_text.strip():
        raise NormalizerUnavailable("empty request")
    client = _NormalizerClient(api_key_env=api_key_env, base_url=base_url, model=model,
                               timeout_s=timeout_s, transport=transport, max_calls=1)
    outcome: dict = {}

    def call() -> None:
        try:
            outcome["result"] = client.rewrite(request_text)
        except BaseException as exc:  # handed back to the caller's thread
            outcome["error"] = exc

    # httpx's timeout is per phase, not total: a daemon thread and join()
    # bound the whole call; a late reply is simply discarded.
    worker = threading.Thread(target=call, name="request-normalizer", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise NormalizerUnavailable(f"no reply within {timeout_s:g} s")
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, NormalizerUnavailable):
            raise error
        raise NormalizerUnavailable(str(error)[:300]) from error
    text, resolved = outcome["result"]
    return text, resolved or model
