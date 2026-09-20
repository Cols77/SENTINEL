"""Operator choice path (P2 Task 3): answer a judgment-point escalation
within the current wire schema. ``pending_decision`` describes what the
operator may choose among, with **no advisor output** -- that is the
anti-automation-bias rule enforced at its narrowest point. ``submit_choice``
recomputes the legal set fresh (never trusts a stale caller-supplied one),
rejects anything that is not a legal label at a judgment point, and submits
the operator's pick through the same ``CommandGateway.submit`` every other
mutation goes through, using the shared ``command_for`` mapping (P1's
``AgentLoop._execute`` uses the same mapping, so there is one source of
truth for tool name -> command type).
"""
from __future__ import annotations

from typing import Any, Sequence

from .agent_loop import command_for
from .decision_state import read_decision_state
from .errors import CoordinatorError
from .legal_tools import ESCALATE_LABEL, JUDGMENT_POINTS, CataloguePlan, legal_set


def pending_decision(repository: Any, run_id: str, catalogue: "Sequence[CataloguePlan]") -> dict | None:
    """The pending judgment-point decision for ``run_id``, or ``None`` if the
    run is not currently waiting on one. The result is operator-facing: it
    carries only the decision point and the legal candidates' labels and
    descriptions -- never an advisor's pick, confidence or band."""
    state = read_decision_state(repository, run_id)
    legal = legal_set(state, catalogue)
    if legal.decision_point not in JUDGMENT_POINTS:
        return None
    return {
        "decision_point": legal.decision_point,
        "options": [{"label": candidate.label, "description": candidate.description}
                    for candidate in legal.candidates],
    }


def submit_choice(repository: Any, gateway: Any, run_id: str, label: str, *,
                   catalogue: "Sequence[CataloguePlan]", sink: Any, actor_id: str = "OPERATOR_1") -> dict[str, Any]:
    """Submit the operator's pick among the current legal candidates at a
    judgment point.

    1. Recomputes the legal set fresh from live state.
    2. Rejects with ``CoordinatorError("INVALID_OPERATOR_CHOICE")`` unless
       the decision point is a judgment point and ``label`` is legal there.
    3. Submits the candidate's command through ``gateway.submit`` as an
       ``OPERATOR`` actor, with a deterministic idempotency key so the same
       choice submitted twice is idempotent. ``escalate`` submits no
       command -- there is nothing to execute -- and is only recorded.
    4. Appends one ``operator_choice`` audit record.
    5. Returns the gateway result (or, for ``escalate``, a small descriptive
       result -- there is no gateway result to return).
    """
    state = read_decision_state(repository, run_id)
    legal = legal_set(state, catalogue)
    if legal.decision_point not in JUDGMENT_POINTS or label not in legal.labels:
        raise CoordinatorError(
            "INVALID_OPERATOR_CHOICE",
            f"{label!r} is not a legal choice at {legal.decision_point!r} (legal: {list(legal.labels)})",
        )
    candidate = legal.by_label(label)
    state_version = state.state_version
    key = f"operator-choice-{run_id}-{state_version}-{label}"

    if label == ESCALATE_LABEL:
        # Nothing to execute -- escalation is the absence of an operator
        # pick among the acting candidates, not a command.
        result: dict[str, Any] = {
            "status": "ESCALATED", "run_id": run_id, "decision_point": legal.decision_point, "label": label,
        }
    else:
        command = command_for(candidate.tool_name, candidate.arguments, run_id, "OPERATOR", actor_id, key)
        assert command is not None, f"legal candidate {candidate.label!r} has no command mapping"
        command["expected_state_version"] = state_version
        result = gateway.submit(command)

    sink.write({
        "record": "operator_choice", "run_id": run_id, "state_version": state_version,
        "decision_point": legal.decision_point, "label": label, "actor_id": actor_id,
    })
    return result


__all__ = ["pending_decision", "submit_choice"]
