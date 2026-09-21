"""Bounded semantic judgment port (Jev experiment, shadow-only).

Policy code consumes judge OUTPUT as evidence; it never hands the judge
authority over mission-state transitions or flight actions.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


_MODEL_ID_CHAR_BUDGET = 256
_TOKEN_COUNT_LIMIT = 1_000_000_000_000


def _provider_observation(raw: Any) -> dict[str, Any]:
    """Return only bounded identity and usage metadata from an HTTP result."""
    document = raw if isinstance(raw, Mapping) else {}
    model = document.get("model")
    if not isinstance(model, str) or not model.strip():
        model = None
    else:
        model = model[:_MODEL_ID_CHAR_BUDGET]
    usage = document.get("usage")
    if not isinstance(usage, Mapping):
        usage = {}

    def count(name: str) -> int | float | None:
        value = usage.get(name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or value > _TOKEN_COUNT_LIMIT):
            return None
        return value

    return {"model": model, "usage": {name: count(name) for name in (
        "prompt_tokens", "completion_tokens", "total_tokens")}}


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
                 boolean_threshold: float = 0.5, transport=None,
                 observation_callback=None) -> None:
        if not api_key_env:
            raise ValueError("api_key_env must name an environment variable (never embed the key)")
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model or os.environ.get(self.MODEL_ENV, self.DEFAULT_MODEL)
        self._timeout_s = timeout_s
        self.boolean_threshold = boolean_threshold
        self._transport = transport  # injected callable for tests; real one built lazily
        self._observation_callback = observation_callback

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
            entry = {"type": q["type"], "instructions": q["instruction"]}
            if q.get("criteria") is not None:
                entry["criteria"] = q["criteria"]
            wire[q["name"]] = entry
        raw = self._post({"model": self._model, "state": state, "questions": wire})
        if self._observation_callback is not None:
            try:
                self._observation_callback(_provider_observation(raw))
            except Exception:
                pass  # observations cannot alter the judgment path
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
                    probabilities = answer["probabilities"]
                    if not isinstance(probabilities, Mapping) or any(
                        isinstance(probability, bool) or not isinstance(probability, (int, float))
                        for probability in probabilities.values()
                    ):
                        raise JudgeUnavailable(f"{name}: choice probabilities must be JSON numbers")
                    answers[name] = {"value": value, "probabilities": {k: float(v) for k, v in probabilities.items()},
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

    def choice(self, state: dict, instruction: str, *, options: Sequence[str] | None = None,
               criteria: Mapping[str, str | None] | None = None, name: str) -> dict[str, Any]:
        # D11: pass label descriptions through when the caller has them (the
        # policy seam's legal-candidate descriptions), instead of bare labels.
        resolved = dict(criteria) if criteria is not None else {opt: None for opt in (options or [])}
        return self.evaluate(state, [{"name": name, "type": "choice", "instruction": instruction,
                                      "criteria": resolved}])[name]


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
        'of the form {"answers": {"<question name": {...}, ...}} containing EXACTLY '
        "one entry per requested question name (no extras). Answer shapes by question "
        'type: "noul" -> {"probability": <0..1>}; "choice" -> {"choice": <one of the '
        'criteria keys>, "probabilities": {<criteria key>: <0..1>, ...}}; "score" -> '
        '{"score": <number>, "probabilities": {<legend key>: <0..1>, ...}}. For each '
        "answer also include a 'confidence' field: your self-assessed confidence (0..1) "
        "in that answer. Answer every question using ONLY the provided state; never "
        "request tools."
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

    def choice(self, state: dict, instruction: str, *, options: Sequence[str] | None = None,
               criteria: Mapping[str, str | None] | None = None, name: str) -> dict[str, Any]:
        # Same D11 signature as JevJudge: a caller holding label descriptions
        # (the policy seam's legal candidates) must be able to pass them to
        # either judge. Without this the LLM advisor raised TypeError on every
        # call, which the live shadow sweep of 2026-09-20 saw as error_rate 1.0.
        resolved = dict(criteria) if criteria is not None else {opt: None for opt in (options or [])}
        return self.evaluate(state, [{"name": name, "type": "choice", "instruction": instruction,
                                      "criteria": resolved}])[name]


def timed(judge_call) -> tuple[dict, int]:
    """Wrap a judge call, returning (result, latency_ms)."""
    start = time.perf_counter()
    result = judge_call()
    return result, int((time.perf_counter() - start) * 1000)


CONTEXT_VERSION = "judgment_context.v1"


SCENARIO_QUESTION_TOOL = "tool_choice"
SCENARIO_QUESTION_ARG = "arg_choice"


def _load_scenarios(scenarios_path: str | Path) -> list[dict[str, Any]]:
    document = json.loads(Path(scenarios_path).read_text(encoding="utf-8"))
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenario document must define a non-empty 'scenarios' list")
    return scenarios


def _arg_choice_criteria(scenario: dict[str, Any]) -> list[str]:
    """Catalogue plan IDs (in document order) for the arg_choice question."""
    catalogue = scenario.get("catalogue") or {}
    return [plan["id"] for plan in catalogue.get("plans", []) if plan.get("id")]


def replay_scenarios(scenarios_path: str, judge) -> dict[str, Any]:
    """Replay WP1 live_scenarios.json turns through a BoundedJudge.

    Mirrors the B10 live-acceptance pass criteria: each turn maps to ONE
    batched judge evaluate() call with a 'tool_choice' question (criteria =
    the scenario's legal_tools, keyed by name with description-or-name
    values) plus, when the turn asserts arguments (e.g.
    arguments.catalogue_plan_id), an 'arg_choice' question over the
    arguments. Turn passes iff tool_choice.value == expected_tool
    AND every asserted argument matches. When the scenario has no
    catalogue, arg_choice criteria fall back to the expected argument
    values; a turn asserting arguments with no usable criteria fails
    closed (never silently passes). Shadow-only: read-only over the
    scenario document, no network, no coordinator side effects. Note:
    coordinator_feedback is passed through as the raw scenario dict,
    not B10's sanitized sentinel.v0 tool-result wire shape (shadow
    harness per card spec).
    """
    scenarios = _load_scenarios(scenarios_path)
    report_scenarios: list[dict[str, Any]] = []
    for scenario in scenarios:
        legal_tools = list(scenario.get("legal_tools", []))
        descriptions = {
            event.get("summary", "")
            for event in scenario.get("event_context", [])
        }
        criteria: dict[str, str | None] = {}
        for tool in legal_tools:
            desc = next((s for s in descriptions if tool in s), None)
            criteria[tool] = desc if desc else tool
        catalogue = scenario.get("catalogue") or {}
        feedback = scenario.get("coordinator_feedback")

        turns_report: list[dict[str, Any]] = []
        scenario_error: str | None = None
        for turn_index, expected_turn in enumerate(scenario.get("expected_turns", [])):
            expected_tool = expected_turn.get("expected_tool")
            allowed = expected_tool if isinstance(expected_tool, list) else [expected_tool]
            expectations: dict[str, Any] = expected_turn.get("expectations") or {}
            arg_keys = [
                dotted.split(".", 1)[1]
                for dotted in expectations
                if dotted.startswith("arguments.")
            ]

            def _arg_question_name(index: int, key: str) -> str:
                # Canonical card shape: exactly 'arg_choice' for a single
                # asserted argument; suffixed per key when several exist so
                # each expectation (B10-style) is scored independently.
                return (SCENARIO_QUESTION_ARG if len(arg_keys) == 1
                        else f"{SCENARIO_QUESTION_ARG}_{index}_{key}")

            # B10 fails a turn for EVERY unmet expectation, so each asserted
            # argument gets its own choice question. Criteria always include
            # the expected values (plus catalogue plan IDs for
            # catalogue_plan_id) — an assertion is never silently dropped,
            # with or without a catalogue.
            arg_questions: list[tuple[str, str, dict[str, str]]] = []  # (name, key, criteria)
            for arg_index, key in enumerate(arg_keys):
                values: list[str] = [
                    str(v) for v in (expectations[f"arguments.{key}"]
                                     if isinstance(expectations[f"arguments.{key}"], list)
                                     else [expectations[f"arguments.{key}"]])
                ]
                if key == "catalogue_plan_id":
                    values.extend(plan_id for plan_id in _arg_choice_criteria(scenario)
                                  if plan_id not in values)
                arg_questions.append((_arg_question_name(arg_index, key), key,
                                      {v: v for v in values}))

            state: dict[str, Any] = {
                "user_request": scenario.get("user_request", ""),
                "phase": scenario.get("phase"),
                "state_version": scenario.get("state_version"),
                "legal_tools": legal_tools,
                "event_context": list(scenario.get("event_context", [])),
                "evidence_context": list(scenario.get("evidence_context") or []),
                "catalogue": catalogue,
            }
            if turn_index > 0 and isinstance(feedback, dict):
                state["coordinator_feedback"] = feedback

            questions: list[dict[str, Any]] = [{
                "name": SCENARIO_QUESTION_TOOL,
                "type": "choice",
                "instruction": (
                    "Which single tool must the coordinator call for this turn? "
                    "Choose exactly one legal tool for the supplied state."
                ),
                "criteria": criteria,
            }]
            if arg_questions:
                for q_name, _key, q_criteria in arg_questions:
                    questions.append({
                        "name": q_name,
                        "type": "choice",
                        "instruction": (
                            "Which value must the turn's arguments carry for this "
                            "asserted field? Choose exactly one."
                        ),
                        "criteria": q_criteria,
                    })

            answer_map: dict[str, dict[str, Any]] | None
            try:
                answer_map = judge.evaluate(state, questions)
            except Exception as exc:
                scenario_error = (
                    f"turn {turn_index}: judge unavailable: {exc}"
                    if not isinstance(exc, JudgeUnavailable)
                    else f"turn {turn_index}: {exc}"
                )
                turns_report.append({
                    "turn": turn_index, "passed": False,
                    "failures": [scenario_error],
                })
                break

            failures: list[str] = []
            tool_answer = answer_map.get(SCENARIO_QUESTION_TOOL)
            actual_tool = tool_answer.get("value") if tool_answer else None
            if actual_tool not in allowed:
                failures.append(
                    f"expected {allowed}, judge selected {actual_tool!r}")
            turn_report: dict[str, Any] = {
                "turn": turn_index,
                "tool_choice": actual_tool,
                "expected_tool": allowed,
                "failures": failures,
            }
            if arg_questions:
                for q_name, key, _q_criteria in arg_questions:
                    arg_answer = answer_map.get(q_name)
                    if not arg_answer:
                        failures.append(
                            f"arguments.{key}: judge produced no answer for {q_name!r}")
                        continue
                    arg_value = arg_answer.get("value")
                    turn_report[f"arg_choice_{key}"] = arg_value
                    expected = expectations[f"arguments.{key}"]
                    expected_values = expected if isinstance(expected, list) else [expected]
                    if arg_value not in expected_values:
                        failures.append(
                            f"arguments.{key}: expected one of {expected_values}, "
                            f"judge selected {arg_value!r}"
                        )
            turn_report["passed"] = not failures
            turns_report.append(turn_report)

        report_scenarios.append({
            "name": scenario.get("name"),
            "passed": scenario_error is None and all(t["passed"] for t in turns_report),
            "error": scenario_error,
            "turns": turns_report,
        })

    return {
        "scenario_set": "sentinel.live-llm-acceptance.v1",
        "passed": all(s["passed"] for s in report_scenarios),
        "scenarios": report_scenarios,
    }


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
    size_m = haz.get("size_m")
    return {
        "context_version": CONTEXT_VERSION,
        "observation": {
            "source": observation.get("source"),
            "object": haz.get("object"),
            "perception_confidence": haz.get("confidence"),
            "size_m": float(size_m) if size_m is not None else None,
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


REQUIRED_OBSERVATION_FIELDS = (
    "source", "object", "perception_confidence", "size_m",
    "position_relation", "duplicate_of_existing_detection",
)


def validate_judgment_state(state: dict) -> dict:
    """Validate that a state payload is a judgment_context.v1 projection.

    Fail-closed guard for replay: the harness must never send prose
    observations or criteria-style dicts where the verified wire shape is
    the versioned structured snapshot. Returns the input unchanged on
    success (usable inline: ``state = validate_judgment_state(state)``).
    """
    if not isinstance(state, dict):
        raise ValueError("judgment state must be an object")
    if state.get("context_version") != CONTEXT_VERSION:
        raise ValueError(f"context_version must be {CONTEXT_VERSION!r}, got {state.get('context_version')!r}")
    for section, required in (
        ("observation", REQUIRED_OBSERVATION_FIELDS),
        ("mission", ("workflow", "trail_status")),
        ("operations", ("patroller_available",)),
    ):
        section_value = state.get(section)
        if not isinstance(section_value, dict):
            raise ValueError(f"judgment state section {section!r} must be an object")
        missing = [field for field in required if field not in section_value]
        if missing:
            raise ValueError(f"judgment state {section} missing fields: {missing}")
    obs = state["observation"]
    if not isinstance(obs["object"], str) or not obs["object"]:
        raise ValueError("observation.object must be a non-empty string")
    confidence = obs["perception_confidence"]
    if confidence is not None and (not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0):
        raise ValueError("observation.perception_confidence must be null or 0..1")
    size_m = obs["size_m"]
    if size_m is not None and (not isinstance(size_m, (int, float)) or size_m <= 0):
        raise ValueError("observation.size_m must be null or a positive number")
    if not isinstance(obs["position_relation"], str):
        raise ValueError("observation.position_relation must be a string")
    if not isinstance(obs["duplicate_of_existing_detection"], bool):
        raise ValueError("observation.duplicate_of_existing_detection must be boolean")
    if not isinstance(state["operations"]["patroller_available"], bool):
        raise ValueError("operations.patroller_available must be boolean")
    return state
