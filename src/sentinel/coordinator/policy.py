"""Policy seam (D8, D9): any callable that maps an ``agent_request`` dict to an
``agent_result`` dict is a Policy, so the LLM adapter, the rule ladder and a
Jev policy are interchangeable and share one gateway path through AgentLoop.

``ContextualPolicy`` is the richer shape a RULE or Jev implementation wants:
it is handed the live, read-only ``DecisionState`` and the deterministic
``LegalSet`` for the current turn instead of re-deriving them from the raw
request. ``bind_context`` adapts a ``ContextualPolicy`` into a plain
``Policy`` by reading that state once per request.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from sentinel.contracts import AgentModelResult

from .decision_state import DecisionState, read_decision_state
from .legal_tools import CataloguePlan, LegalSet, legal_set

Policy = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class PolicyContext:
    """What a ``ContextualPolicy`` may know at a decision point, beyond the
    bare wire request: the read-only decision state and its computed legal
    set (Task 1, Task 2)."""

    state: DecisionState
    legal: LegalSet


@runtime_checkable
class ContextualPolicy(Protocol):
    """A policy that decides from the live decision state and legal set,
    rather than only the bounded wire request."""

    def decide(self, request: dict[str, Any], context: PolicyContext) -> dict[str, Any]:
        ...


def bind_context(
    policy: ContextualPolicy,
    repository: Any,
    catalogue: "tuple[CataloguePlan, ...] | list[CataloguePlan]" = (),
) -> Policy:
    """Adapt a ``ContextualPolicy`` into a plain ``Policy``: read the live
    ``DecisionState`` and ``LegalSet`` for the request's run once per turn and
    hand them to ``policy.decide``. AgentLoop keeps calling the returned
    callable exactly as it would call any other policy."""

    def _policy(request: dict[str, Any]) -> dict[str, Any]:
        state = read_decision_state(repository, request["run_id"])
        legal = legal_set(state, catalogue)
        return policy.decide(request, PolicyContext(state, legal))

    return _policy


def one_call(request: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Build a schema-valid ``agent_result`` with exactly one tool call for
    ``request``, matching its ``agent_run_id``. This is the shared building
    block every simple (RULE, Jev) policy uses instead of hand-rolling the
    wire envelope."""
    return AgentModelResult.from_dict(
        {
            "contract_version": "sentinel.v0",
            "wire_type": "agent_result",
            "agent_run_id": request["agent_run_id"],
            "status": "TOOL_CALLS",
            "tool_calls": [
                {
                    "contract_version": "sentinel.v0",
                    "wire_type": "agent_tool_call",
                    "tool_name": tool_name,
                    "arguments": arguments,
                }
            ],
        }
    ).to_dict()


__all__ = ["ContextualPolicy", "Policy", "PolicyContext", "bind_context", "one_call"]
