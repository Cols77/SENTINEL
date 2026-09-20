"""P4 Task 4: the run-driving loop, in ``src/`` instead of a script.

This is ``scripts/rehearsal_run.py::run_rehearsal``'s per-turn loop, extracted
so production code (a future ``sentinel coordinate`` CLI entry point, P4 Task
6) can drive a run without importing from ``scripts/``. ``drive_run`` owns
creating the run and running ``AgentLoop`` until it is terminal; it is the
operator who decides what to do at each pause, through the two callbacks --
``drive_run`` itself never confirms a plan, a task or a judgment-point choice
on its own.

``policy`` is a bare ``ContextualPolicy`` (a ``ShadowLadder`` for F, or an
``ArchitectureRouter`` for E, from ``architecture.build_policy``) -- this
module does the ``bind_context`` itself, once per run, so callers never have
to remember to.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

from .agent_loop import AgentLoop
from .boundary import CoordinatorBoundary
from .decision_state import DecisionState, read_decision_state
from .operator_choice import pending_decision
from .policy import ContextualPolicy, bind_context

_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "EMERGENCY"}

__all__ = ["drive_run"]


def _run_create_command(run_id: str, request_text: str) -> dict[str, Any]:
    """The ``RUN_CREATE`` command envelope for ``run_id``. Deterministic
    ``command_id``/``idempotency_key`` (derived from ``run_id``, like
    ``agent_loop.command_for``'s own uuid5 derivation) so driving the same
    ``run_id`` twice is idempotent rather than a fresh, colliding run."""
    key = f"drive-run-create:{run_id}"
    return {
        "contract_version": "sentinel.v0", "wire_type": "command_envelope",
        "command_id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
        "command_type": "RUN_CREATE", "run_id": run_id,
        "actor_type": "OPERATOR", "actor_id": "OPERATOR_1",
        "correlation_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "correlation:" + key)),
        "idempotency_key": key, "issued_at": "2999-01-01T00:00:00Z",
        "payload": {"request_text": request_text},
    }


def drive_run(*, repo, gateway, policy: ContextualPolicy, catalogue, run_id: str, agent_run_id: str,
              request_text: str, on_approval: Callable[[DecisionState], None],
              on_judgment: Callable[[dict], None], max_steps: int = 10, max_turns: int = 8) -> dict[str, Any]:
    """Create ``run_id`` and run ``AgentLoop`` until it reaches a terminal
    run status. ``WAITING_FOR_APPROVAL`` calls ``on_approval(state)`` with the
    live ``DecisionState``; a ``NEEDS_REVIEW`` result at a real judgment point
    calls ``on_judgment(pending)`` with ``operator_choice.pending_decision``'s
    dict. Both callbacks are the operator -- this function submits nothing on
    its own behalf beyond ``RUN_CREATE``.

    Returns ``{"status", "steps", "approvals", "judgments"}``; the last two
    are counts of operator touches. Raises ``RuntimeError`` on the same three
    conditions ``run_rehearsal`` raised on before this was extracted: a
    ``NEEDS_REVIEW`` result at a point that is not a judgment point, any
    other unexpected result, or exhausting ``max_steps`` without reaching a
    terminal status.
    """
    boundary = CoordinatorBoundary(repo, gateway)
    bound_policy = bind_context(policy, repo, catalogue)
    gateway.submit(_run_create_command(run_id, request_text))

    approvals = 0
    judgments = 0
    steps = 0
    for _ in range(max_steps):
        status = repo.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0]
        if status in _TERMINAL_STATUSES:
            break

        steps += 1
        result = AgentLoop(boundary, bound_policy, max_turns=max_turns, repository=repo,
                            catalogue=catalogue).run(run_id, agent_run_id, request_text)

        if result.get("error_code") == "WAITING_FOR_APPROVAL":
            state = read_decision_state(repo, run_id)
            on_approval(state)
            approvals += 1
            continue

        if result.get("status") == "NEEDS_REVIEW":
            pending = pending_decision(repo, run_id, catalogue)
            if pending is None:
                raise RuntimeError(f"NEEDS_REVIEW but the run is not at a judgment point: {result!r}")
            on_judgment(pending)
            judgments += 1
            continue

        raise RuntimeError(f"drive_run stopped on an unexpected result: {result!r}")
    else:
        raise RuntimeError(f"drive_run did not complete within {max_steps} steps")

    final_status = repo.connection.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0]
    return {"status": final_status, "steps": steps, "approvals": approvals, "judgments": judgments}
