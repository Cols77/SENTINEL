from __future__ import annotations

import sqlite3

import pytest

from sentinel.coordinator.errors import CoordinatorError

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


def test_reproposing_an_entry_raises_a_typed_gateway_error(run):
    """Blocker A9, formerly proposal item P-6 — now **resolved**.

    This was a characterization test asserting the raw ``sqlite3.IntegrityError``
    that escaped ``CommandGateway.submit`` and killed the run by bypassing every
    caller's ``CoordinatorError`` handling, ``AgentLoop``'s included. The
    operator released the gateway to the P1/P2 session for that one defect; the
    fix landed (``1ba6bfb``) and reached this branch when P3 merged into master,
    so the test flipped here exactly as the plan scheduled it.

    It now pins the *fixed* behaviour: a typed, greppable error code rather than
    a raw database exception. The narrow catch matters — only
    ``sqlite3.IntegrityError`` is translated, and a non-plans constraint keeps
    its own distinct ``STATE_CONSTRAINT_VIOLATION`` code, so the next constraint
    bug still surfaces instead of blending into ordinary rejections."""
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("REJECT")
    with pytest.raises(CoordinatorError) as caught:
        run.propose_plan(FULL)
    assert caught.value.code == "PLAN_ALREADY_PROPOSED"
    assert not isinstance(caught.value, sqlite3.IntegrityError)
