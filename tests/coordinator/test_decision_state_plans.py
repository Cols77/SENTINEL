from __future__ import annotations

import sqlite3

import pytest

from .p3_helpers import LEFT_MID, RIGHT_MID, P3Run

ROUTE, FULL = "PLAN_ROUTE_ONLY_2_ZONE", "PLAN_MARKER_VERIFIED_6"


@pytest.fixture
def run(tmp_path):
    r = P3Run(tmp_path, "plans")
    yield r
    r.close()


def test_rejected_mid_run_proposal_leaves_the_old_plan_approved(run):
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("REJECT")
    state = run.state()
    assert state.plan_station_ids == (LEFT_MID, RIGHT_MID) and state.plan_approved is True
    assert state.run_status == "RUNNING"


def test_confirmed_mid_run_proposal_becomes_the_plan(run):
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("CONFIRM")
    state = run.state()
    assert len(state.plan_station_ids) == 6 and state.plan_approved is True


def test_pending_first_plan_is_visible_but_not_approved(run):
    run.propose_plan(ROUTE)
    state = run.state()
    assert state.plan_id is not None and state.plan_approved is False


def test_rejected_only_plan_returns_the_run_to_no_plan(run):
    run.propose_plan(ROUTE)
    run.decide_latest_plan("REJECT")
    state = run.state()
    assert state.plan_id is None and state.plan_station_ids == () and state.plan_approved is False


def test_proposed_catalogue_entries_are_listed(run):
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("REJECT")
    assert run.state().proposed_catalogue_plan_ids == (ROUTE, FULL)


def test_characterization_reproposing_an_entry_raises_a_raw_integrity_error(run):
    """Blocker A9 (proposal item P-6). When P-6 is granted this test flips; do not 'fix' it here."""
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("REJECT")
    with pytest.raises(sqlite3.IntegrityError):
        run.propose_plan(FULL)
