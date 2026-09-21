# src/sentinel/survey/llm_adapter.py
"""The live adapter behind ``--mode agent``: a model writes the tool call.

Approved by the operator on 2026-09-22 ("Yes I approve, build the live agent
adapter"). It is the survey's own, deliberately small, tool-calling client:
the coordinator has one (``sentinel.adapter.llm``), but the survey flow is a
sibling package that does not import the coordinator's loop, and that file
is owned elsewhere.

What the adapter does and does not do:

- One ``chat/completions`` request per decision, with exactly the tools the
  legal set offers declared as OpenAI-style function tools, ``option_id`` as
  an enum of the legal ids. The model cannot name a sweep that was not
  offered: the API refuses the call before it reaches us, and
  ``LlmProposer`` refuses it again after.
- Returns the ``TOOL_CALLS`` document ``LlmProposer.decide`` validates. The
  legality check, the escalate-on-anything-odd rule, and the audit record
  stay in the proposer; this module never decides anything.
- The reason the operator sees at approval is the MODEL's own words. That
  is the point of agent mode and its risk (a fluent wrong reason passes);
  ``judge`` mode is the one with a confidence gate.
- The raw response is kept on ``last_raw`` so the proposer's audit record
  can carry it, and every call is counted: the loop asks for at most three
  decisions per run (a proposal and up to two reviews), and the adapter
  refuses a fourth even if asked.
- No key is ever embedded or logged; ``api_key_env`` names the variable.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

#: Propose, then at most two reviews (candidates.MAX_FLIGHTS): the loop never
#: asks for more, and the adapter will not answer more.
MAX_CALLS_PER_RUN = 3

SYSTEM_INSTRUCTIONS = (
    "You direct one short indoor drone survey. You are given the operator's request, the "
    "results gathered so far, and the sweeps that may legally be flown next, each with its "
    "stops, viewing range and the object classes it can resolve. Choose by calling EXACTLY "
    "one of the tools offered. Give a reason in one or two plain sentences that the operator "
    "will read before approving the flight: say what the sweep will show and why it answers "
    "the request. Never invent a sweep, a count, or a fact about the room. If no sweep "
    "answers the request, or the results already do, call the tool that says so."
)

Transport = Callable[[str, dict[str, str], dict], dict]


class AdapterUnavailable(RuntimeError):
    """Transport or response failure; ``LlmProposer`` turns it into an escalate."""


def _tool_for(candidate) -> dict:
    if candidate.tool_name == "survey.propose_sweep":
        return {
            "type": "function",
            "function": {
                "name": "survey_propose_sweep",
                "description": "Fly one of the offered sweeps next.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "option_id": {"type": "string", "enum": []},
                        "reason": {"type": "string",
                                   "description": "One or two sentences for the operator."},
                    },
                    "required": ["option_id", "reason"],
                    "additionalProperties": False,
                },
            },
        }
    if candidate.tool_name == "survey.submit_report":
        return {
            "type": "function",
            "function": {
                "name": "survey_submit_report",
                "description": "Stop flying and report the counts gathered so far.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    return {
        "type": "function",
        "function": {
            "name": "survey_escalate",
            "description": "Hand the decision to the operator.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    }


def build_tools(candidates) -> list[dict]:
    """One declared tool per tool name the legal set offers; the sweep tool's
    ``option_id`` enumerates exactly the legal ids."""
    tools: dict[str, dict] = {}
    for candidate in candidates:
        tool = tools.setdefault(candidate.tool_name, _tool_for(candidate))
        if candidate.tool_name == "survey.propose_sweep":
            tool["function"]["parameters"]["properties"]["option_id"]["enum"].append(
                candidate.arguments["option_id"])
    return list(tools.values())


def build_messages(request: dict, context) -> list[dict]:
    offered = [
        {"tool": c.tool_name, "label": c.label,
         **({"option_id": c.arguments["option_id"]} if "option_id" in c.arguments else {}),
         "description": c.description}
        for c in context.legal.candidates
    ]
    user = {
        "request": request.get("request_text", ""),
        "decision_point": context.legal.decision_point,
        "results_so_far": context.results,
        "offered": offered,
    }
    return [{"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": json.dumps(user, default=str)}]


_TOOL_NAMES = {"survey_propose_sweep": "survey.propose_sweep",
               "survey_submit_report": "survey.submit_report",
               "survey_escalate": "survey.escalate"}


def to_document(raw: dict) -> dict:
    """The provider's chat response as the ``TOOL_CALLS`` document
    ``LlmProposer`` expects. Anything not exactly one well-formed tool call
    is returned as it is -- the proposer is the one that says "escalate"."""
    try:
        message = raw["choices"][0]["message"]
        calls = message.get("tool_calls") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return {"status": "MALFORMED", "tool_calls": []}
    out = []
    for call in calls:
        function = call.get("function") or {}
        name = _TOOL_NAMES.get(function.get("name"))
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = None
        if name is None or not isinstance(arguments, dict):
            return {"status": "MALFORMED", "tool_calls": []}
        out.append({"tool_name": name, "arguments": arguments})
    return {"status": "TOOL_CALLS", "tool_calls": out}


class LlmAdapter:
    """``propose(request, context) -> TOOL_CALLS document``, the interface
    ``survey.proposer.LlmProposer`` was written against."""

    def __init__(self, api_key_env: str, base_url: str, model: str, *,
                 timeout_s: float = 120.0, transport: Transport | None = None,
                 max_calls: int = MAX_CALLS_PER_RUN) -> None:
        if not api_key_env:
            raise ValueError("api_key_env must name an environment variable (never embed the key)")
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self.model = model
        self._timeout_s = timeout_s
        self._transport = transport
        self._max_calls = max_calls
        self.calls_made = 0
        self.last_raw: dict | None = None

    def _post(self, payload: dict) -> dict:
        key = os.environ.get(self._api_key_env)
        if not key:
            raise AdapterUnavailable(f"env var {self._api_key_env} not set")
        url = f"{self._base_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            if self._transport is not None:
                return self._transport(url, headers, payload)
            import httpx
            response = httpx.post(url, headers=headers, json=payload, timeout=self._timeout_s)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            # never echo the key: the exception text is the transport's, not ours
            raise AdapterUnavailable(f"chat/completions request failed: {exc}") from exc

    def propose(self, request: dict, context) -> dict:
        if self.calls_made >= self._max_calls:
            raise AdapterUnavailable(
                f"agent adapter refuses call {self.calls_made + 1}: at most "
                f"{self._max_calls} paid calls per run")
        payload = {
            "model": self.model,
            "messages": build_messages(request, context),
            "tools": build_tools(context.legal.candidates),
            "tool_choice": "required",
            "temperature": 0,
        }
        self.calls_made += 1
        raw = self._post(payload)
        self.last_raw = raw
        resolved = raw.get("model") if isinstance(raw, dict) else None
        if resolved is not None and not str(resolved).startswith(self.model):
            raise AdapterUnavailable(f"pinned {self.model!r} but the provider resolved {resolved!r}")
        return to_document(raw)
