"""Read-only operator chat (P2 Task 4): the LLM narrates the current state
and past decisions -- it never acts, approves, rejects or chooses, and it
never sees an advisor's pick before the operator has decided at that
judgment point (the anti-automation-bias rule, enforced here at the prompt
boundary rather than trusted to the model).

This module is deliberately decoupled from every mutating surface. It is
built from three injected callables -- ``complete`` (a chat completion),
``read_state`` (-> a ``DecisionState``) and ``read_audit`` (-> the run's
shadow audit records) -- so nothing here ever opens a database connection or
reaches a command path. ``tests/test_chat_boundary.py`` (d) checks this
module's own source for the mutating surfaces it must never import or call.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping, Sequence

from sentinel.adapter.llm.config import LLMConfig
from sentinel.coordinator.decision_state import DecisionState

SYSTEM_MESSAGE = (
    "You are the SENTINEL operator assistant. You explain the current state and "
    "past decisions. You cannot take actions, approve, reject, or choose. If asked "
    "to act, tell the operator which UI/CLI action does it."
)

_RECENT_AUDIT_LIMIT = 10
_JUDGMENT_RECORD = "judgment"
# Audit record type written when the operator decides; only its presence
# releases the advisors' picks for that decision into the chat prompt.
_RESOLVING_RECORD = "operator_choice"


def _resolved_decisions(records: Sequence[dict]) -> set[tuple[Any, Any, Any]]:
    return {
        (record.get("run_id"), record.get("state_version"), record.get("decision_point"))
        for record in records
        if record.get("record") == _RESOLVING_RECORD
    }


def withhold_unresolved_advisors(records: Sequence[dict]) -> list[dict]:
    """The anti-automation-bias filter: a ``judgment`` record whose
    ``(run_id, state_version, decision_point)`` has no matching resolving
    record *anywhere* in ``records`` yet has its ``advisors`` field removed.
    Every other record -- including an already-resolved ``judgment`` record
    and every ``rule`` record -- passes through unchanged."""
    resolved = _resolved_decisions(records)
    out: list[dict] = []
    for record in records:
        key = (record.get("run_id"), record.get("state_version"), record.get("decision_point"))
        if record.get("record") == _JUDGMENT_RECORD and key not in resolved:
            record = {field: value for field, value in record.items() if field != "advisors"}
        out.append(record)
    return out


class OperatorChat:
    """Read-only narrator over ``DecisionState`` and the shadow audit trail.

    ``complete`` is any ``list[dict] -> str`` chat completion (a fake in
    tests, ``production_complete`` below in production). ``read_state`` and
    ``read_audit`` are injected so this module never touches a repository or
    a database connection directly, and never gains a write path by
    accident.
    """

    def __init__(self, complete: Callable[[list[dict]], str],
                 read_state: Callable[[str], DecisionState],
                 read_audit: Callable[[str], list[dict]]) -> None:
        self.complete = complete
        self.read_state = read_state
        self.read_audit = read_audit

    def reply(self, run_id: str, message: str) -> str:
        state = self.read_state(run_id)
        packet = state.to_packet()
        audit = withhold_unresolved_advisors(self.read_audit(run_id))
        recent_audit = audit[-_RECENT_AUDIT_LIMIT:]
        user_content = "\n\n".join((
            json.dumps(packet, sort_keys=True),
            json.dumps(recent_audit, sort_keys=True),
            message,
        ))
        messages = [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": user_content},
        ]
        return self.complete(messages)


def production_complete(config: LLMConfig, base_url: str, *, timeout_s: float | None = None,
                         environ: Mapping[str, str] | None = None,
                         transport: Callable[[str, dict, dict], dict] | None = None,
                         ) -> Callable[[list[dict]], str]:
    """A thin production ``complete`` over ``adapter.llm.config``: it reads
    only ``config.model`` and the *name* of the credential environment
    variable (``config.api_key_env``), resolving the key at call time, and
    posts one Chat Completions request with **no ``tools`` parameter** --
    this narrator must never be able to request a tool call."""
    resolved_base = base_url.rstrip("/")
    timeout = timeout_s if timeout_s is not None else config.timeout_ms / 1000.0

    def complete(messages: list[dict]) -> str:
        source = environ if environ is not None else os.environ
        key = source.get(config.api_key_env) if config.api_key_env else None
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        payload = {"model": config.model, "messages": messages, "temperature": 0}
        url = f"{resolved_base}/v1/chat/completions"
        if transport is not None:
            raw = transport(url, headers, payload)
        else:
            import httpx
            response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            raw = response.json()
        return raw["choices"][0]["message"]["content"]

    return complete


__all__ = ["OperatorChat", "SYSTEM_MESSAGE", "production_complete", "withhold_unresolved_advisors"]
