from __future__ import annotations

import json
import re

import pytest

from sentinel.coordinator.decision_state import DecisionState, DetectionView, TaskView

from .p3_helpers import LEFT_FRONT, LEFT_MID, RIGHT_MID, P3Run

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@pytest.fixture
def run(tmp_path):
    r = P3Run(tmp_path, "multi")
    r.approve_route_plan()
    yield r
    r.close()


def test_three_detections_are_listed_oldest_first(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    b = run.ingest("b", LEFT_MID, 0.92)
    c = run.ingest("c", RIGHT_MID, 0.95)
    state = run.state()
    assert [d.result_id for d in state.detections] == [a["result_id"], b["result_id"], c["result_id"]]
    assert state.tasks == ()
    assert state.detection["result_id"] == a["result_id"]          # compat accessor = head of the queue
    assert state.task_id is None


def test_tasking_the_first_detection_moves_it_out_of_the_queue(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    b = run.ingest("b", LEFT_MID, 0.92)
    run.ingest("c", RIGHT_MID, 0.95)
    task_id = run.create_task(a)
    state = run.state()
    assert len(state.detections) == 2 and state.detections[0].result_id == b["result_id"]
    assert [t.task_id for t in state.tasks] == [task_id]
    assert state.tasks[0].status == "PROPOSED" and state.tasks[0].ranked_patrollers == ()
    assert state.task_id == task_id and state.task_status == "PROPOSED"   # compat accessors = current_task


def test_detection_facts_flag_a_duplicate_of_an_open_task(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    run.ingest("b", LEFT_MID, 0.92)
    run.ingest("c", RIGHT_MID, 0.95)
    run.create_task(a)
    state = run.state()
    dup, other = state.detections
    assert state.detection_facts(dup) == {"score_band": "very_high", "inside_plan": True,
                                          "duplicates_open_task": True, "repeat_of_handled": False}
    assert state.detection_facts(other)["duplicates_open_task"] is False


def test_outside_plan_detection_is_flagged(run):
    run.ingest("x", LEFT_FRONT, 0.90)
    state = run.state()
    assert state.detection_facts(state.detections[0])["inside_plan"] is False


def test_current_task_is_the_oldest_unsettled_task_and_ranking_scores_are_exposed(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    c = run.ingest("c", RIGHT_MID, 0.95)
    first, second = run.create_task(a), run.create_task(c)
    run.rank(first)
    state = run.state()
    assert state.current_task.task_id == first
    assert state.tasks[0].ranked_patrollers == ("PATROLLER_1",) and state.tasks[0].ranking_scores == (0.0,)
    assert state.tasks[1].task_id == second and state.tasks[1].ranked_patrollers == ()


def test_repeat_of_handled_on_a_constructed_state():
    """An ASSIGNED task alongside a pending detection is unreachable through the gateway today
    (the run completes on the first confirmed assignment, blocker A1), so this fact is unit-tested
    on a constructed state."""
    handled = TaskView("t", LEFT_MID, "ASSIGNED", "r0", ("PATROLLER_1",), (0.0,))
    again = DetectionView("r1", "a1", LEFT_MID, "DETECTED", 0.9, "rock")
    state = DecisionState(run_id="run", state_version=1, run_status="RUNNING", agent_phase="PLAN",
                          emergency_latched=False, request_text=None, plan_id="p", plan_mode="ROUTE_ONLY_2_ZONE",
                          plan_station_ids=(LEFT_MID, RIGHT_MID), plan_approved=True, detection=None,
                          open_task_station_ids=(LEFT_MID,), task_id="t", task_status="ASSIGNED",
                          detections=(again,), tasks=(handled,))
    facts = state.detection_facts(again)
    assert facts["repeat_of_handled"] is True and facts["duplicates_open_task"] is False


def test_packets_carry_no_uuid_and_v1_packet_shape_is_unchanged(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    run.ingest("b", LEFT_MID, 0.92)
    run.create_task(a)
    state = run.state()
    assert set(state.to_packet()) == {"context_version", "run", "operator_request", "facts"}
    assert state.to_packet()["context_version"] == "decision_packet.v1"
    v2 = state.to_packet_v2()
    assert v2["context_version"] == "decision_packet.v2"
    assert v2["queue"] == {"pending_detections": 1, "open_tasks": 1}
    assert v2["detection"]["duplicates_open_task"] is True
    assert not UUID_RE.search(json.dumps(v2)) and not UUID_RE.search(json.dumps(state.to_packet()))
