"""Task 9: `covering_plans` as a pure helper, plus characterization of the
behaviour that made the REPLAN detection trigger unshippable.

`REPLAN` is NOT a decision point in this branch. See the module docstring of
`sentinel.coordinator.replan` for the three reasons, and proposal item P-5
for the trigger that would make it viable. These tests exist so that a future
attempt starts from evidence rather than repeating the analysis.
"""
from __future__ import annotations

import pytest

from sentinel.coordinator.legal_tools import (
    EVIDENCE_GATE,
    EVIDENCE_TASK_SCORE,
    ESCALATE_LABEL,
    legal_set,
    load_catalogue_plans,
)
from sentinel.coordinator.replan import covering_plans

from .p3_helpers import LEFT_FRONT, LEFT_MID, RIGHT_MID, P3Run
from .test_catalogue_draft import DRAFT_CATALOGUE_PATH

CATALOGUE = load_catalogue_plans()
ROUTE_STATIONS = (LEFT_MID, RIGHT_MID)
REAL_STATIONS = ("LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK",
                 "RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK")


@pytest.fixture
def run(tmp_path):
    r = P3Run(tmp_path, "replan")
    r.approve_route_plan()
    yield r
    r.close()


# --- covering_plans as a pure function -------------------------------------


def test_covering_plans_over_the_route_plan_is_the_full_site_plan_only():
    found = covering_plans(CATALOGUE, LEFT_FRONT, ROUTE_STATIONS, ())
    assert [p.plan_id for p in found] == ["PLAN_MARKER_VERIFIED_6"]


def test_covering_plans_excludes_an_entry_already_proposed_in_this_run():
    assert covering_plans(CATALOGUE, LEFT_FRONT, ROUTE_STATIONS, ("PLAN_MARKER_VERIFIED_6",)) == ()


def test_covering_plans_orders_fewest_stations_first_then_by_id():
    draft = load_catalogue_plans(DRAFT_CATALOGUE_PATH)
    found = covering_plans(draft, LEFT_MID, (), ())
    sizes = [len(p.station_ids) for p in found]
    assert sizes == sorted(sizes)
    assert found[0].plan_id == "PLAN_ROUTE_LEFT_ALLEY_MIDDLE"  # the single-station plan wins


def test_covering_plans_is_empty_when_no_plan_covers_the_union():
    """A station outside the catalogue entirely has no covering plan."""
    assert covering_plans(CATALOGUE, "ROOFTOP_HVAC", ROUTE_STATIONS, ()) == ()


def test_the_v1_catalogue_makes_the_covering_test_degenerate():
    """Reason 1 the REPLAN detection trigger was dropped: PLAN_MARKER_VERIFIED_6
    holds all six stations, so it is a superset of every station set. A rule of
    "replan when some plan covers it" therefore fires for EVERY real out-of-plan
    station -- the general case, not the narrow one the plan described."""
    for station in REAL_STATIONS:
        assert covering_plans(CATALOGUE, station, ROUTE_STATIONS, ()) != (), station


def test_the_rich_draft_catalogue_does_not_remove_the_degeneracy():
    """Note for P3a-2: PLAN_VERIFIED_FULL_SITE is still a superset of
    everything, so adopting the rich catalogue (item P-3) does not by itself
    make a covering-set trigger workable."""
    draft = load_catalogue_plans(DRAFT_CATALOGUE_PATH)
    for station in REAL_STATIONS:
        assert covering_plans(draft, station, ROUTE_STATIONS, ()) != (), station


# --- characterization: what the decision path actually does today ----------


@pytest.mark.parametrize("score", [EVIDENCE_TASK_SCORE, 0.90, 0.99])
def test_characterization_out_of_plan_detection_escalates_by_rule(run, score):
    """An out-of-plan detection at or above the task-creation score escalates by
    rule -- it does NOT become a REPLAN judgment point. This is the operator-
    approved v1 label (fixtures/decisions/README.md, 2026-09-20) and the
    expectation the frozen turn generator encodes. When proposal item P-5 lands
    and P3a-2 wires REPLAN to an operator message, this test stays true: a
    detection alone must still not trigger a replan."""
    run.ingest("out", LEFT_FRONT, score)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == EVIDENCE_GATE
    assert legal.rule_choice == ESCALATE_LABEL
    assert legal.labels == ("create_task", ESCALATE_LABEL)


def test_characterization_out_of_plan_below_task_score_also_escalates(run):
    run.ingest("low", LEFT_FRONT, 0.62)
    legal = legal_set(run.state(), CATALOGUE)
    assert legal.decision_point == EVIDENCE_GATE
    assert legal.rule_choice == ESCALATE_LABEL


def test_characterization_a_mid_run_proposal_is_accepted_by_the_gateway(run):
    """The gateway half of REPLAN already works -- a mid-run plan proposal is
    accepted and goes to WAITING_FOR_APPROVAL, and Task 3's read model keeps the
    old plan in force until the new one is confirmed. What is missing is a
    legitimate *trigger* (proposal item P-5), not the mechanism."""
    run.propose_plan("PLAN_MARKER_VERIFIED_6")
    state = run.state()
    assert state.run_status == "WAITING_FOR_APPROVAL"
    assert state.plan_station_ids == ROUTE_STATIONS  # old plan still in force
    run.decide_latest_plan("REJECT")
    assert run.state().plan_station_ids == ROUTE_STATIONS
