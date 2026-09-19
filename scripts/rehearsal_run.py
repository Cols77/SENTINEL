#!/usr/bin/env python
"""Rehearsal runner (P2 Task 6): one simulated demo run end to end.

1. Opens a fresh coordinator gateway (the same real ports the coordinator
   tests use -- ``tests.coordinator.harness``).
2. Wraps the F executor (P1's ``RulePolicy``) in a ``ShadowLadder`` with the
   selected advisor arms, auditing every RULE and judgment-point decision to
   an append-only ``JsonlAuditSink``.
3. At each judgment point (``SELECT_PLAN``, ``EVIDENCE_GATE``) the operator's
   choice is taken from ``--choices sp=<label>,eg=<label>`` when given, or
   prompted for interactively otherwise -- ``pending_decision`` never
   surfaces an advisor's pick, confidence or band (Task 3's
   anti-automation-bias rule), so nothing here can leak one either.
4. The plan and task approvals (``OPERATOR_DECIDE`` ``CONFIRM``) that P1's
   ``WAIT_APPROVAL`` points require are submitted automatically, with the
   worker's preflight/dispatch/perception steps run in between -- the same
   sequence ``tests/coordinator/test_operator_choice.py``'s end-to-end test
   proves.

``--advisors none`` runs the executor alone (no advisor calls at all);
``--advisors fake`` runs small offline, non-LLM stand-ins (``scripts.
shadow_sweep``'s ``FakeJudge``/``StaticJudge``) -- no network call, safe for
routine rehearsal and for this script's own tests; ``--advisors live`` runs
the real Jev/LLM shadow advisors (P2 Task 2) and refuses to run -- printing
why, exit code 2 -- unless ``SENTINEL_SHADOW_SWEEP_CONSENT=<batch-id>`` is
set: a fresh, explicit operator consent for a paid remote call (plan gate
G-rehearsal, Task 6 Step 4). Nothing in this script sets that variable.

Every mutation here still goes through ``CommandGateway.submit`` (directly,
or through ``operator_choice.submit_choice``) -- this script adds no new
mutation path. Advisors never change what is executed: the ``ShadowLadder``
executor (``RulePolicy``) is the only thing whose output is ever submitted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "src", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sentinel.coordinator.agent_loop import AgentLoop  # noqa: E402
from sentinel.coordinator.command_gateway import CommandGateway  # noqa: E402
from sentinel.coordinator.decision_state import read_decision_state  # noqa: E402
from sentinel.coordinator.legal_tools import EVIDENCE_GATE, SELECT_PLAN, load_catalogue_plans  # noqa: E402
from sentinel.coordinator.operator_choice import pending_decision, submit_choice  # noqa: E402
from sentinel.coordinator.policies import JevPolicy, RulePolicy  # noqa: E402
from sentinel.coordinator.policy import bind_context  # noqa: E402
from sentinel.coordinator.repository import CoordinatorRepository  # noqa: E402
from sentinel.coordinator.shadow import JsonlAuditSink, ShadowLadder  # noqa: E402

from tests.coordinator.harness import CoordinatorBoundary, FakeWorker, envelope  # noqa: E402

_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "EMERGENCY"}
_MAX_STEPS = 10
_POINT_ARG_KEYS = {SELECT_PLAN: "sp", EVIDENCE_GATE: "eg"}


# --- advisor construction ----------------------------------------------

def _offline_advisors() -> list[tuple[str, Any]]:
    """Small, deterministic, non-LLM stand-ins -- no network call. Reuses
    Task 5's own offline baselines so `fake` exercises the identical
    JevPolicy/ShadowLadder plumbing a live advisor would, without a live
    call."""
    from scripts.shadow_sweep import FakeJudge, StaticJudge
    return [("jev", JevPolicy(FakeJudge())), ("llm", JevPolicy(StaticJudge()))]


def _live_advisors() -> list[tuple[str, Any]]:
    """The real Task 2 advisor arms. Only ever reached after the consent
    gate in ``main`` -- see the module docstring."""
    from sentinel.judgment import JevJudge, LlmJudge
    jev = JevJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai",
                    model="typesafe/jev-1.13-20260917")
    llm = LlmJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api",
                   model="z-ai/glm-5.3-flash")
    return [("jev", JevPolicy(jev)), ("llm", JevPolicy(llm))]


def build_advisors(mode: str) -> list[tuple[str, Any]]:
    if mode == "none":
        return []
    if mode == "fake":
        return _offline_advisors()
    if mode == "live":
        return _live_advisors()
    raise ValueError(f"unknown --advisors mode: {mode!r}")


# --- operator choice resolution -----------------------------------------

def parse_choices(raw: str | None) -> dict[str, str]:
    """``"sp=PLAN_ROUTE_ONLY_2_ZONE,eg=create_task"`` -> ``{"sp": ..., "eg": ...}``."""
    out: dict[str, str] = {}
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        if not sep:
            raise ValueError(f"--choices entry {part!r} is not key=value")
        out[key.strip()] = value.strip()
    return out


def _resolve_label(pending: dict, choices: dict[str, str], prompt: Callable[[str], str]) -> str:
    point = pending["decision_point"]
    key = _POINT_ARG_KEYS.get(point)
    if key is not None and key in choices:
        return choices[key]
    options = ", ".join(f'{o["label"]!r}' for o in pending["options"])
    # No advisor pick, confidence or band is ever available to show here --
    # `pending_decision` withholds them by construction (Task 3).
    answer = prompt(f"{point}: choose one of [{options}]: ")
    return answer.strip()


# --- plan/task approval plumbing (mirrors test_operator_choice.py's e2e) --

def _plan_reference(repo: CoordinatorRepository, run_id: str) -> dict:
    plan = repo.connection.execute(
        "SELECT * FROM plans WHERE run_id = ? ORDER BY created_at DESC LIMIT 1", (run_id,),
    ).fetchone()
    return {
        "plan_id": plan["plan_id"], "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }


def _state_version(repo: CoordinatorRepository, run_id: str) -> int:
    return repo.read_state(run_id).to_dict()["state_version"]


def _confirm_plan(repo: CoordinatorRepository, gateway: CommandGateway, run_id: str) -> None:
    reference = _plan_reference(repo, run_id)
    gateway.submit(envelope("OPERATOR_DECIDE", {
        "decision": "CONFIRM", "target": {"kind": "PLAN", "plan_reference": reference},
    }, sequence=10, run_id=run_id, actor_type="OPERATOR", expected=_state_version(repo, run_id)))
    worker = FakeWorker(gateway)
    worker.preflight(run_id, reference["plan_id"], reference["plan_sha256"])
    station_id = reference["station_ids"][0]
    worker.dispatch(run_id, reference["plan_id"], station_id, _state_version(repo, run_id))
    worker.capture_and_perceive(run_id, reference["plan_id"], reference["plan_sha256"])


def _confirm_task(repo: CoordinatorRepository, gateway: CommandGateway, run_id: str, task_id: str) -> None:
    gateway.submit(envelope("OPERATOR_DECIDE", {
        "decision": "CONFIRM", "target": {"kind": "TASK", "task_id": task_id},
    }, sequence=30, run_id=run_id, actor_type="OPERATOR", expected=_state_version(repo, run_id)))


# --- the rehearsal itself -------------------------------------------------

def run_rehearsal(*, advisors_mode: str, choices: dict[str, str], db_path: Path, audit_path: Path,
                   run_id: str | None = None, agent_run_id: str | None = None,
                   request_text: str = "inspect one station",
                   prompt: Callable[[str], str] = input) -> dict[str, Any]:
    """Drives one simulated demo run end to end and returns a small summary
    dict. Every mutation goes through ``CommandGateway.submit`` -- directly
    here, or through ``operator_choice.submit_choice`` -- there is no other
    mutation path."""
    run_id = run_id or str(uuid.uuid4())
    agent_run_id = agent_run_id or str(uuid.uuid4())

    repo = CoordinatorRepository.open(db_path)
    try:
        gateway = CommandGateway(repo)
        boundary = CoordinatorBoundary(repo, gateway)
        catalogue = load_catalogue_plans()
        sink = JsonlAuditSink(audit_path)
        advisors = build_advisors(advisors_mode)
        ladder = ShadowLadder(RulePolicy(), advisors, sink)
        policy = bind_context(ladder, repo, catalogue)

        gateway.submit(envelope("RUN_CREATE", {"request_text": request_text}, sequence=1,
                                 run_id=run_id, actor_type="OPERATOR"))

        for _ in range(_MAX_STEPS):
            status = repo.connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0]
            if status in _TERMINAL_STATUSES:
                break

            result = AgentLoop(boundary, policy, max_turns=8, repository=repo, catalogue=catalogue).run(
                run_id, agent_run_id, request_text)

            if result.get("error_code") == "WAITING_FOR_APPROVAL":
                state = read_decision_state(repo, run_id)
                if not state.plan_approved:
                    _confirm_plan(repo, gateway, run_id)
                elif state.task_status == "ASSIGNMENT_PROPOSED":
                    _confirm_task(repo, gateway, run_id, state.task_id)
                else:
                    raise RuntimeError(f"WAITING_FOR_APPROVAL but no known approval target: {state!r}")
                continue

            if result.get("status") == "NEEDS_REVIEW":
                pending = pending_decision(repo, run_id, catalogue)
                if pending is None:
                    raise RuntimeError(f"NEEDS_REVIEW but the run is not at a judgment point: {result!r}")
                label = _resolve_label(pending, choices, prompt)
                submit_choice(repo, gateway, run_id, label, catalogue=catalogue, sink=sink)
                continue

            raise RuntimeError(f"rehearsal run stopped on an unexpected result: {result!r}")
        else:
            raise RuntimeError(f"rehearsal did not complete within {_MAX_STEPS} steps")

        final_status = repo.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0]
        return {
            "run_id": run_id, "agent_run_id": agent_run_id, "status": final_status,
            "advisors": advisors_mode, "audit_path": str(audit_path), "db_path": str(db_path),
        }
    finally:
        repo.close()


# --- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--advisors", required=True, choices=("none", "fake", "live"))
    parser.add_argument("--choices", default=None,
                         help="sp=<SELECT_PLAN label>,eg=<EVIDENCE_GATE label>; omitted entries are prompted for")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--request", default="inspect one station")
    parser.add_argument("--db", type=Path, default=None, help="sqlite path (default: artifacts/shadow/<run_id>.sqlite3)")
    parser.add_argument("--audit", type=Path, default=None, help="JSONL audit path (default: artifacts/shadow/<run_id>.jsonl)")
    args = parser.parse_args(argv)

    if args.advisors == "live" and not os.environ.get("SENTINEL_SHADOW_SWEEP_CONSENT"):
        print(
            "refusing to run rehearsal with '--advisors live': SENTINEL_SHADOW_SWEEP_CONSENT is not set. "
            "This is a paid remote call and requires fresh, explicit operator consent "
            "(plan gate G-rehearsal) -- set SENTINEL_SHADOW_SWEEP_CONSENT=<batch-id> to proceed.",
            file=sys.stderr,
        )
        return 2

    run_id = args.run_id or str(uuid.uuid4())
    db_path = args.db or (REPO_ROOT / "artifacts" / "shadow" / f"{run_id}.sqlite3")
    audit_path = args.audit or (REPO_ROOT / "artifacts" / "shadow" / f"{run_id}.jsonl")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    summary = run_rehearsal(
        advisors_mode=args.advisors, choices=parse_choices(args.choices),
        db_path=db_path, audit_path=audit_path, run_id=run_id, request_text=args.request,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
