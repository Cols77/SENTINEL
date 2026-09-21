"""P4 Task 3: one agent loop, two selectable architectures.

**F** (default, the 2026-09-25 demo path) is unchanged by this module: rules
execute, the operator chooses at ``SELECT_PLAN``, advisors answer in shadow.
``build_policy("F", ...)`` returns the very same object graph the code built
before this module existed -- a bare ``ShadowLadder(RulePolicy(), advisors,
sink)``, never wrapped.

**E** is identical to F everywhere except ``SELECT_PLAN``, where a *proposer*
policy (any ``ContextualPolicy``, tested here with a scripted stub -- Codex's
``LlmProposer`` plugs into the same seam later, P3b1 Task 2, P4 Task 6) is
asked first. ``ArchitectureRouter`` is the approval boundary: it accepts a
proposal only if it is well-formed and names a real catalogue plan, forwards
the proposer's controller-canonical escalation, and folds every other outcome
-- an exception, an illegal tool, an unknown plan, or a malformed result --
back onto the same ladder (architecture F) on that same turn. E never lets a
model act without this check.
"""
from __future__ import annotations

import os
from typing import Mapping, Sequence

from .legal_tools import ESCALATE_LABEL, SELECT_PLAN
from .policies import RulePolicy
from .policy import ContextualPolicy, PolicyContext
from .proposer import is_canonical_escalation
from .shadow import AuditSink, ShadowLadder

ARCHITECTURES = ("F", "E")
DEFAULT_ARCHITECTURE = "F"
ARCHITECTURE_ENV = "SENTINEL_ARCHITECTURE"


class ArchitectureUnavailable(RuntimeError):
    """Raised by ``build_policy("E", ...)`` when no proposer is supplied."""


def resolve_architecture(value: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """Explicit ``value`` wins, then the ``SENTINEL_ARCHITECTURE`` env var,
    then ``"F"``. Case-insensitive, stripped. An unrecognised value raises
    ``ValueError`` naming the allowed values -- a typo never silently falls
    back to the default."""
    env = os.environ if environ is None else environ
    candidate = value if value is not None else env.get(ARCHITECTURE_ENV)
    if candidate is None:
        return DEFAULT_ARCHITECTURE
    resolved = candidate.strip().upper()
    if resolved not in ARCHITECTURES:
        raise ValueError(f"unknown architecture {candidate!r}; must be one of {ARCHITECTURES}")
    return resolved


class ArchitectureRouter:
    """``ContextualPolicy`` for architecture E. ``SELECT_PLAN`` goes to
    ``proposer``; everything else, and every proposer failure, goes to
    ``ladder`` (architecture F). The router, not the proposer, is the
    boundary: only a well-formed, catalogue-legal ``inspection.propose_plan``
    call or a controller-canonical explicit escalation is ever accepted and
    returned as-is."""

    def __init__(self, ladder: ContextualPolicy, proposer: ContextualPolicy, sink: AuditSink) -> None:
        self.ladder = ladder
        self.proposer = proposer
        self.sink = sink

    def decide(self, request: dict, context: PolicyContext) -> dict:
        if context.legal.decision_point != SELECT_PLAN:
            return self.ladder.decide(request, context)

        try:
            result = self.proposer.decide(request, context)
        except Exception as exc:  # noqa: BLE001 -- any proposer failure degrades to F
            return self._fallback(request, context, f"exception:{type(exc).__name__}")

        if is_canonical_escalation(result, agent_run_id=request["agent_run_id"]):
            self._write_route(
                context, route="proposer_escalation", label=ESCALATE_LABEL,
            )
            return result

        reason = self._reject_reason(result, context)
        if reason is not None:
            return self._fallback(request, context, reason)

        label = result["tool_calls"][0]["arguments"]["catalogue_plan_id"]
        self._write_route(context, route="proposer", label=label)
        return result

    def _fallback(self, request: dict, context: PolicyContext, reason: str) -> dict:
        self._write_route(context, route="fallback_F", reason=reason)
        return self.ladder.decide(request, context)

    def _write_route(self, context: PolicyContext, *, route: str, **details: str) -> None:
        self.sink.write({
            "record": "architecture_route", "architecture": "E",
            "run_id": context.state.run_id, "state_version": context.state.state_version,
            "decision_point": context.legal.decision_point, "route": route, **details,
        })

    @staticmethod
    def _reject_reason(result: object, context: PolicyContext) -> str | None:
        """``None`` iff ``result`` is accepted: exactly one ``TOOL_CALLS``
        tool call, named ``inspection.propose_plan``, whose
        ``catalogue_plan_id`` is the label of a candidate in
        ``context.legal.candidates`` with that same tool name."""
        if not isinstance(result, dict):
            return "malformed_result"
        if result.get("status") != "TOOL_CALLS":
            return "proposer_escalated"
        tool_calls = result.get("tool_calls")
        if not isinstance(tool_calls, list) or len(tool_calls) != 1:
            return "malformed_result"
        call = tool_calls[0]
        if not isinstance(call, dict):
            return "malformed_result"
        tool_name = call.get("tool_name")
        if tool_name != "inspection.propose_plan":
            return f"illegal_tool:{tool_name}"
        arguments = call.get("arguments")
        if not isinstance(arguments, dict) or "catalogue_plan_id" not in arguments:
            return "malformed_result"
        plan_id = arguments["catalogue_plan_id"]
        valid_labels = {c.label for c in context.legal.candidates if c.tool_name == "inspection.propose_plan"}
        if plan_id not in valid_labels:
            return "unknown_plan"
        return None

    def forget(self, agent_run_id: str) -> None:
        """Clear per-run model memory across both architecture routes."""
        for policy in (self.proposer, self.ladder):
            forget = getattr(policy, "forget", None)
            if callable(forget):
                forget(agent_run_id)

    def close(self) -> None:
        closer = getattr(self.ladder, "close", None)
        if closer is not None:
            closer()


def build_policy(architecture: str, *, advisors: Sequence[tuple[str, ContextualPolicy]], sink: AuditSink,
                  proposer: ContextualPolicy | None = None) -> ContextualPolicy:
    """``"F"`` -> ``ShadowLadder(RulePolicy(), advisors, sink)``, returned
    bare. ``"E"`` -> ``ArchitectureRouter`` over that same ladder and
    ``proposer``; ``proposer`` of ``None`` raises ``ArchitectureUnavailable``."""
    ladder = ShadowLadder(RulePolicy(), advisors, sink)
    if architecture == "F":
        return ladder
    if architecture == "E":
        if proposer is None:
            raise ArchitectureUnavailable(
                "architecture E needs a proposer policy; the LLM proposer comes from plan P3b1 Task 2"
            )
        return ArchitectureRouter(ladder, proposer, sink)
    raise ValueError(f"unknown architecture {architecture!r}; must be one of {ARCHITECTURES}")


__all__ = [
    "ARCHITECTURES", "DEFAULT_ARCHITECTURE", "ARCHITECTURE_ENV",
    "ArchitectureUnavailable", "ArchitectureRouter", "build_policy", "resolve_architecture",
]
