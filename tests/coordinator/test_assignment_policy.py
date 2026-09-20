from __future__ import annotations

import pytest

from sentinel.coordinator.assignment_policy import (
    ASSIGNMENT_CLEAR_GAP, assignment_facts, gap_band, is_near_tie, rank_gap)
from sentinel.coordinator.tasks import SimulatedPatroller

from .p3_helpers import LEFT_MID, P3Run


@pytest.mark.parametrize("scores,gap,band,near", [
    ((0.0,), None, "single", False),
    ((0.80, 0.80), 0.0, "tie", True),
    ((0.81, 0.79), 0.02, "very_close", True),
    ((0.85, 0.79), 0.06, "close", True),
    ((0.90, 0.80), 0.10, "clear", False),      # 0.9 - 0.8 == 0.0999… in floats: rounding must make it clear
    ((0.95, 0.60, 0.59), 0.35, "clear", False),
])
def test_gap_bands(scores, gap, band, near):
    assert rank_gap(scores) == gap
    assert gap_band(rank_gap(scores)) == band
    assert is_near_tie(scores) is near
    assert assignment_facts(scores) == {"patrollers_ranked": len(scores), "rank_gap_band": band, "near_tie": near}


def test_clear_gap_constant_is_declared():
    assert ASSIGNMENT_CLEAR_GAP == 0.10


def test_packet_v2_carries_assignment_facts_for_the_current_task(tmp_path):
    run = P3Run(tmp_path, "near-tie")
    try:
        run.approve_route_plan()
        task_id = run.create_task(run.ingest("a", LEFT_MID, 0.9))
        assert run.state().to_packet_v2()["assignment"] is None            # not ranked yet
        # The gateway cannot carry scores (blocker A3); this service call is the one permitted exception.
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.81, "PATROLLER_2": 0.79})
        assert run.state().to_packet_v2()["assignment"] == {
            "patrollers_ranked": 2, "rank_gap_band": "very_close", "near_tie": True}
    finally:
        run.close()
