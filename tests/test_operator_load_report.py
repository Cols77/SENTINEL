"""P3a Task 11: operator-load report.

Four scripted rehearsals through the real gateway -- no live advisor -- each
scored against the "approvals only" target from ``sentinel.operator_load``.
REPLAN is out of scope on this branch: it was implemented and then
deliberately dropped (``src/sentinel/coordinator/replan.py``) and is not a
decision point, so no replan scenario is scripted or asserted here.
"""
from __future__ import annotations

from scripts.operator_load_report import (
    SCENARIOS,
    clean_single_detection,
    clean_two_detections,
    duplicate_detection,
    generate_report,
    near_tie_assignment,
    render_markdown,
)


def test_clean_single_detection_is_approvals_only(tmp_path):
    result = clean_single_detection(tmp_path)
    touches = result["touches"]
    assert (touches["approvals"], touches["choices"], touches["escalations"]) == (2, 0, 0)
    assert touches["approvals_only"] is True
    assert touches["broken_by"] == []
    assert result["final_run_status"] == "COMPLETED"


def test_clean_two_detections_completes_and_strands_the_second_task(tmp_path):
    """Blocker A1 (proposal item P-1): the run completes on the first
    confirmed assignment, stranding the second task PROPOSED forever. That
    is a platform limit to report, not an operator-load failure -- the
    scenario still meets the approvals-only target."""
    result = clean_two_detections(tmp_path)
    touches = result["touches"]
    assert touches["approvals_only"] is True
    assert touches["choices"] == 0 and touches["escalations"] == 0
    assert result["final_run_status"] == "COMPLETED"
    assert result["finding"]["blocker"] == "A1"
    assert result["finding"]["stranded_task_status"] == "PROPOSED"
    assert result["finding"]["stranded_task_id"] is not None


def test_duplicate_detection_breaks_the_target_at_evidence_gate(tmp_path):
    result = duplicate_detection(tmp_path)
    touches = result["touches"]
    assert touches["broken_by"] == ["EVIDENCE_GATE"]
    assert touches["approvals_only"] is False
    assert touches["escalations"] == 1
    # the scripted answer is 'escalate' itself -- not a choice, per operator_touches
    assert touches["choices"] == 0


def test_near_tie_assignment_is_one_choice_and_one_escalation(tmp_path):
    result = near_tie_assignment(tmp_path)
    touches = result["touches"]
    assert touches["broken_by"] == ["ASSIGN"]
    assert touches["approvals_only"] is False
    assert touches["choices"] == 1 and touches["escalations"] == 1
    assert result["final_run_status"] == "COMPLETED"


def test_generate_report_runs_every_scenario_once_in_order(tmp_path):
    results = generate_report(tmp_path)
    assert [r["name"] for r in results] == [
        "Clean single detection", "Clean two detections", "Duplicate detection", "Near-tie assignment",
    ]
    assert len(SCENARIOS) == 4


def test_render_markdown_covers_every_scenario_and_states_replan_is_out_of_scope(tmp_path):
    results = generate_report(tmp_path)
    text = render_markdown(results)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [f"## {r['name']}" for r in results]
    assert "REPLAN is out of scope" in text
    assert "A1" in text  # the clean-two-detections finding is documented
