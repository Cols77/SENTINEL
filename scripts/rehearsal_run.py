#!/usr/bin/env python
"""Rehearsal runner (P2 Task 6): one simulated demo run end to end.

1. Opens a fresh coordinator gateway (the same real ports the coordinator
   tests use -- ``tests.coordinator.harness``).
2. Builds the selected architecture's policy (P4 Task 3's
   ``architecture.build_policy``): ``F`` is the bare ``ShadowLadder(
   RulePolicy(), advisors, sink)``; ``E`` wraps that same ladder in an
   ``ArchitectureRouter`` that gives ``SELECT_PLAN`` to a proposer and folds
   every proposer failure back onto the ladder. Either way every RULE and
   judgment-point decision is audited to an append-only ``JsonlAuditSink``,
   and P4 Task 4's ``driver.drive_run`` runs the loop.
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

``--architecture {F,E}`` (default: the ``SENTINEL_ARCHITECTURE`` env var,
then ``F``) selects the architecture; ``--proposer scripted:<PLAN_ID>``
supplies E's proposer with a tiny non-LLM stand-in. ``--proposer jev`` uses
the approval-gated Jev proposer and refuses before artifact creation unless
``SENTINEL_JEV_PROPOSER_CONSENT=<batch-id>`` records fresh explicit consent
for G-live-jev-proposer. ``--architecture E`` with no ``--proposer`` exits 2
before anything is opened or created.

Every mutation here still goes through ``CommandGateway.submit`` (directly,
or through ``operator_choice.submit_choice``) -- this script adds no new
mutation path. Advisors never change what is executed: only the executor
policy's own output (RULE, or E's accepted proposal) is ever submitted.
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

from sentinel.coordinator.architecture import ArchitectureUnavailable, build_policy, resolve_architecture  # noqa: E402
from sentinel.coordinator.boundary import CoordinatorBoundary  # noqa: E402, F401 -- re-exported for callers that import it from here
from sentinel.coordinator.command_gateway import CommandGateway  # noqa: E402
from sentinel.coordinator.driver import drive_run  # noqa: E402
from sentinel.coordinator.legal_tools import EVIDENCE_GATE, SELECT_PLAN, load_catalogue_plans  # noqa: E402
from sentinel.coordinator.operator_choice import submit_choice  # noqa: E402
from sentinel.coordinator.policies import JevPolicy  # noqa: E402
from sentinel.coordinator.policy import ContextualPolicy, PolicyContext, one_call  # noqa: E402
from sentinel.coordinator.proposer_factory import (  # noqa: E402
    ScriptedProposer, build_proposer, require_jev_proposer_consent,
    require_proposer_consent, JEV_PROPOSER_CONSENT_ENV, JEV_PROPOSER_MODEL,
)
from sentinel.coordinator.repository import CoordinatorRepository  # noqa: E402
from sentinel.coordinator.shadow import JsonlAuditSink  # noqa: E402

from tests.coordinator.harness import FakeWorker, envelope  # noqa: E402

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
    """The real Task 2 advisor arms. Refuses without operator consent."""
    from scripts.shadow_sweep import require_live_consent
    require_live_consent("G-rehearsal")
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
                   prompt: Callable[[str], str] = input,
                   architecture: str | None = None,
                   proposer: ContextualPolicy | None = None,
                   audit_sink: Any | None = None) -> dict[str, Any]:
    """Drives one simulated demo run end to end and returns a small summary
    dict. Every mutation goes through ``CommandGateway.submit`` -- directly
    here, or through ``operator_choice.submit_choice`` -- there is no other
    mutation path.

    ``architecture`` (``resolve_architecture``'s rules: explicit value, then
    ``SENTINEL_ARCHITECTURE``, then ``"F"``) and ``proposer`` select which
    ``ContextualPolicy`` (P4 Task 3's ``build_policy``) drives the run. The
    policy -- and any ``ArchitectureUnavailable`` it raises for ``"E"`` with
    no ``proposer`` -- is built *before* the database is opened, so a bad
    architecture selection never creates one."""
    run_id = run_id or str(uuid.uuid4())
    agent_run_id = agent_run_id or str(uuid.uuid4())
    advisors = build_advisors(advisors_mode)  # before any side effect: live mode refuses without consent
    resolved_architecture = resolve_architecture(architecture)
    sink = audit_sink or JsonlAuditSink(audit_path)
    policy = build_policy(resolved_architecture, advisors=advisors, sink=sink, proposer=proposer)

    repo = CoordinatorRepository.open(db_path)
    try:
        gateway = CommandGateway(repo)
        catalogue = load_catalogue_plans()

        def on_approval(state) -> None:
            if not state.plan_approved:
                _confirm_plan(repo, gateway, run_id)
            elif state.task_status == "ASSIGNMENT_PROPOSED":
                _confirm_task(repo, gateway, run_id, state.task_id)
            else:
                raise RuntimeError(f"WAITING_FOR_APPROVAL but no known approval target: {state!r}")

        def on_judgment(pending: dict) -> None:
            label = _resolve_label(pending, choices, prompt)
            submit_choice(repo, gateway, run_id, label, catalogue=catalogue, sink=sink)

        drive_result = drive_run(
            repo=repo, gateway=gateway, policy=policy, catalogue=catalogue,
            run_id=run_id, agent_run_id=agent_run_id, request_text=request_text,
            on_approval=on_approval, on_judgment=on_judgment,
            max_steps=_MAX_STEPS, max_turns=8,
        )

        return {
            "run_id": run_id, "agent_run_id": agent_run_id, "status": drive_result["status"],
            "advisors": advisors_mode, "audit_path": str(audit_path), "db_path": str(db_path),
            "architecture": resolved_architecture,
            "approvals": drive_result["approvals"], "judgments": drive_result["judgments"],
        }
    finally:
        repo.close()


# --- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--advisors", required=True, choices=("none", "fake", "live"))
    parser.add_argument("--architecture", default=None, choices=("F", "E"),
                         help="F (default) or E; unset falls back to the SENTINEL_ARCHITECTURE env var, then F")
    parser.add_argument("--proposer", default=None,
                         help="llm/jev (live, separately consent-gated) or scripted:<PLAN_ID> for architecture E")
    parser.add_argument("--choices", default=None,
                         help="sp=<SELECT_PLAN label>,eg=<EVIDENCE_GATE label>; omitted entries are prompted for")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--request", default="inspect one station")
    parser.add_argument("--db", type=Path, default=None, help="sqlite path (default: artifacts/shadow/<run_id>.sqlite3)")
    parser.add_argument("--audit", type=Path, default=None, help="JSONL audit path (default: artifacts/shadow/<run_id>.jsonl)")
    args = parser.parse_args(argv)

    if args.advisors == "live":
        from scripts.shadow_sweep import require_live_consent
        try:
            require_live_consent("G-rehearsal")
        except PermissionError as exc:
            print(f"refusing to run rehearsal with '--advisors live': {exc}", file=sys.stderr)
            return 2

    if args.proposer in {"jev", "llm"}:
        try:
            require_proposer_consent(args.proposer)
        except PermissionError as exc:
            print(f"refusing to run rehearsal with '--proposer {args.proposer}': {exc}", file=sys.stderr)
            return 2

    try:
        proposer = build_proposer(args.proposer) if args.proposer not in {"jev", "llm"} else None
    except (PermissionError, ValueError) as exc:
        print(f"invalid --proposer: {exc}", file=sys.stderr)
        return 2

    run_id = args.run_id or str(uuid.uuid4())
    db_path = args.db or (REPO_ROOT / "artifacts" / "shadow" / f"{run_id}.sqlite3")
    audit_path = args.audit or (REPO_ROOT / "artifacts" / "shadow" / f"{run_id}.jsonl")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    sink = JsonlAuditSink(audit_path)
    if args.proposer in {"jev", "llm"}:
        try:
            proposer = build_proposer(args.proposer, sink=sink)
        except (PermissionError, ValueError) as exc:
            print(f"invalid --proposer: {exc}", file=sys.stderr)
            return 2

    try:
        summary = run_rehearsal(
            advisors_mode=args.advisors, choices=parse_choices(args.choices),
            db_path=db_path, audit_path=audit_path, run_id=run_id, request_text=args.request,
            architecture=args.architecture, proposer=proposer, audit_sink=sink,
        )
    except ArchitectureUnavailable as exc:
        print(f"refusing to run rehearsal: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
