#!/usr/bin/env python
"""Operator-load report (P3a Task 11).

Four scripted rehearsals through the real coordinator gateway, no live
advisor: ``ShadowLadder(RulePolicy(), advisors=[], sink=MemoryAuditSink())``
as the ``AgentLoop`` policy, with a scripted operator who confirms every
approval and answers each escalation with one fixed label through
``operator_choice.submit_choice``. Each scenario is scored against the
"approvals only" target with ``sentinel.operator_load.operator_touches``.

Scenarios: a clean single detection; two clean detections (the run completes
on the first confirmed assignment -- blocker A1 -- stranding the second task;
reported as a finding, not a failure); a duplicate detection at
EVIDENCE_GATE; and a near-tie ranking at ASSIGN.

REPLAN is **not** scripted here. It was implemented and then deliberately
dropped (see the module docstring of ``sentinel.coordinator.replan``): it is
not a decision point on this branch, ``legal_set`` never returns it, and
``replan.covering_plans`` is an unwired helper for P3a-2.

Every mutation goes through ``CommandGateway.submit`` -- directly, through
``AgentLoop``, or through ``operator_choice.submit_choice`` -- except the one
sanctioned exception, ``SimulatedPatroller.rank_for_task``, used to build the
near-tie ranking (the gateway can only ever produce a single-patroller
ranking, blocker A3).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "src", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sentinel.coordinator.agent_loop import AgentLoop  # noqa: E402
from sentinel.coordinator.legal_tools import ASSIGN, EVIDENCE_GATE, load_catalogue_plans  # noqa: E402
from sentinel.coordinator.operator_choice import pending_decision, submit_choice  # noqa: E402
from sentinel.coordinator.policies import RulePolicy  # noqa: E402
from sentinel.coordinator.policy import bind_context  # noqa: E402
from sentinel.coordinator.shadow import MemoryAuditSink, ShadowLadder  # noqa: E402
from sentinel.coordinator.tasks import SimulatedPatroller  # noqa: E402
from sentinel.operator_load import operator_touches  # noqa: E402

from tests.coordinator.harness import AGENT_RUN_ID, CoordinatorBoundary  # noqa: E402
from tests.coordinator.p3_helpers import LEFT_MID, RIGHT_MID, P3Run  # noqa: E402

CATALOGUE = load_catalogue_plans()
REQUEST_TEXT = "route-only survey of the alley stations"


# --- shared plumbing ---------------------------------------------------------

def _drive(run: "P3Run", policy: Any, *, max_turns: int = 8) -> dict[str, Any]:
    boundary = CoordinatorBoundary(run.repo, run.gateway)
    return AgentLoop(boundary, policy, max_turns=max_turns, repository=run.repo, catalogue=CATALOGUE).run(
        run.run_id, AGENT_RUN_ID, REQUEST_TEXT)


def _confirm_task(run: "P3Run", task_id: str) -> None:
    run.submit("OPERATOR_DECIDE", {"decision": "CONFIRM", "target": {"kind": "TASK", "task_id": task_id}},
               actor_type="OPERATOR", with_version=True)


def _events(run: "P3Run") -> list[dict]:
    return list(run.repo.read_events(run.run_id, 0, 256))


# --- scenarios ---------------------------------------------------------------

def clean_single_detection(tmp_path: Path) -> dict[str, Any]:
    """One in-plan detection above the task-score threshold: the rule
    creates the task, ranks it, and proposes the rank-1 patroller unattended.
    The operator's only two touches are the plan approval and the final
    assignment confirmation."""
    run = P3Run(tmp_path, "load-clean-single")
    try:
        run.approve_route_plan()
        run.ingest("a", LEFT_MID, 0.90)
        sink = MemoryAuditSink()
        policy = bind_context(ShadowLadder(RulePolicy(), [], sink), run.repo, CATALOGUE)

        result = _drive(run, policy)
        assert result.get("status") != "NEEDS_REVIEW", f"clean single detection escalated: {result}"
        task_id = run.state().tasks[0].task_id
        _confirm_task(run, task_id)

        return {
            "name": "Clean single detection",
            "narrative": clean_single_detection.__doc__,
            "touches": operator_touches(sink.records, _events(run)),
            "final_run_status": run.state().run_status,
        }
    finally:
        run.close()


def clean_two_detections(tmp_path: Path) -> dict[str, Any]:
    """Two in-plan detections at different stations both clear the rule
    unattended, but the run completes on the FIRST confirmed assignment
    (blocker A1, proposal item P-1): the second task is stranded PROPOSED
    forever. This is a known platform limit, not an operator-load defect --
    no further operator touch was needed or possible for either task."""
    run = P3Run(tmp_path, "load-clean-two")
    try:
        run.approve_route_plan()
        run.ingest("a", LEFT_MID, 0.90)
        run.ingest("c", RIGHT_MID, 0.95)
        sink = MemoryAuditSink()
        policy = bind_context(ShadowLadder(RulePolicy(), [], sink), run.repo, CATALOGUE)

        result = _drive(run, policy)
        assert result.get("status") != "NEEDS_REVIEW", f"clean two detections escalated: {result}"
        first_task_id = run.state().tasks[0].task_id
        _confirm_task(run, first_task_id)

        final_state = run.state()
        stranded = next((t for t in final_state.tasks if t.task_id != first_task_id), None)
        return {
            "name": "Clean two detections",
            "narrative": clean_two_detections.__doc__,
            "touches": operator_touches(sink.records, _events(run)),
            "final_run_status": final_state.run_status,
            "finding": {
                "blocker": "A1",
                "stranded_task_id": stranded.task_id if stranded else None,
                "stranded_task_status": stranded.status if stranded else None,
            },
        }
    finally:
        run.close()


def duplicate_detection(tmp_path: Path) -> dict[str, Any]:
    """A second detection at a station that already has an open task cannot
    be rule-settled ("same rock, or a second hazard?"), so it becomes a
    judgment point at EVIDENCE_GATE. The scripted operator's fixed answer is
    'escalate' (the approved v2 draft label -- no fact in the packet
    distinguishes a second hazard from a repeat sighting); because the
    duplicate stays at the head of the detection queue (blocker A7), the
    first task's own rank/assign never runs either -- one escalation blocks
    the whole run."""
    run = P3Run(tmp_path, "load-duplicate")
    try:
        run.approve_route_plan()
        run.ingest("a", LEFT_MID, 0.90)
        run.ingest("b", LEFT_MID, 0.92)
        sink = MemoryAuditSink()
        policy = bind_context(ShadowLadder(RulePolicy(), [], sink), run.repo, CATALOGUE)

        result = _drive(run, policy, max_turns=4)
        assert result.get("status") == "NEEDS_REVIEW", f"expected the duplicate to escalate: {result}"
        pending = pending_decision(run.repo, run.run_id, CATALOGUE)
        assert pending is not None and pending["decision_point"] == EVIDENCE_GATE
        submit_choice(run.repo, run.gateway, run.run_id, "escalate", catalogue=CATALOGUE, sink=sink)

        return {
            "name": "Duplicate detection",
            "narrative": duplicate_detection.__doc__,
            "touches": operator_touches(sink.records, _events(run)),
            "final_run_status": run.state().run_status,
        }
    finally:
        run.close()


def near_tie_assignment(tmp_path: Path) -> dict[str, Any]:
    """Two patrollers are ranked within ``ASSIGNMENT_CLEAR_GAP`` of each
    other: the rule will not guess, so it becomes a judgment point at ASSIGN.
    The scripted operator's fixed answer is 'assign_rank_1' -- confirming the
    top-ranked patroller -- after which the run finishes exactly as the
    clean single-detection scenario does."""
    run = P3Run(tmp_path, "load-near-tie")
    try:
        run.approve_route_plan()
        run.ingest("a", LEFT_MID, 0.90)
        sink = MemoryAuditSink()
        policy = bind_context(ShadowLadder(RulePolicy(), [], sink), run.repo, CATALOGUE)

        _drive(run, policy, max_turns=1)  # the rule creates the task; stop before it ranks
        task_id = run.state().tasks[0].task_id
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.81, "PATROLLER_2": 0.79})

        result = _drive(run, policy, max_turns=2)
        assert result.get("status") == "NEEDS_REVIEW", f"expected the near-tie to escalate: {result}"
        pending = pending_decision(run.repo, run.run_id, CATALOGUE)
        assert pending is not None and pending["decision_point"] == ASSIGN
        submit_choice(run.repo, run.gateway, run.run_id, "assign_rank_1", catalogue=CATALOGUE, sink=sink)
        _confirm_task(run, task_id)

        return {
            "name": "Near-tie assignment",
            "narrative": near_tie_assignment.__doc__,
            "touches": operator_touches(sink.records, _events(run)),
            "final_run_status": run.state().run_status,
        }
    finally:
        run.close()


SCENARIOS: tuple[Callable[[Path], dict[str, Any]], ...] = (
    clean_single_detection, clean_two_detections, duplicate_detection, near_tie_assignment,
)


def generate_report(tmp_path: Path) -> list[dict[str, Any]]:
    """Run every scenario in its own sqlite database under ``tmp_path`` and
    return one result dict per scenario, in the order above."""
    return [scenario(tmp_path / scenario.__name__) for scenario in SCENARIOS]


# --- rendering ----------------------------------------------------------------

def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Operator-load report (P3a Task 11)",
        "",
        "Four scripted rehearsals through the real coordinator gateway, no live advisor: "
        "`ShadowLadder(RulePolicy(), advisors=[], sink=MemoryAuditSink())` under `AgentLoop`, with a "
        "scripted operator who confirms every approval and answers each escalation with one fixed "
        "label through `submit_choice`. Scored against the \"approvals only\" target "
        "(`sentinel.operator_load.operator_touches`).",
        "",
        "REPLAN is out of scope on this branch: it was implemented and then deliberately dropped "
        "(see the module docstring of `src/sentinel/coordinator/replan.py`) -- it is not a decision "
        "point, `legal_set` never returns it, and no replan scenario is scripted here.",
        "",
        "| Scenario | Approvals | Choices | Escalations | Approvals only | Broken by | Final run status |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        touches = result["touches"]
        broken_by = ", ".join(touches["broken_by"]) if touches["broken_by"] else "—"
        lines.append(
            f"| {result['name']} | {touches['approvals']} | {touches['choices']} | "
            f"{touches['escalations']} | {'yes' if touches['approvals_only'] else 'no'} | {broken_by} | "
            f"{result['final_run_status']} |"
        )
    lines.append("")
    for result in results:
        lines.append(f"## {result['name']}")
        lines.append("")
        lines.append((result["narrative"] or "").strip())
        lines.append("")
        finding = result.get("finding")
        if finding is not None:
            lines.append(
                f"**Finding (blocker {finding['blocker']}):** task `{finding['stranded_task_id']}` is "
                f"stranded at status `{finding['stranded_task_status']}` once the run completes. This "
                "is a known platform limit (see the consolidated WP0 amendment proposal), not a defect "
                "introduced by this report."
            )
            lines.append("")
    return "\n".join(lines) + "\n"


# --- CLI -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    del argv  # no arguments today; kept for symmetry with the other scripts/ CLIs
    out_path = REPO_ROOT / "artifacts" / "p3" / "operator-load.md"
    with tempfile.TemporaryDirectory() as tmp_dir:
        results = generate_report(Path(tmp_dir))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(results), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
