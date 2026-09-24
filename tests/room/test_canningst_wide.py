"""The CanningSt wide scenario: the same room, tags and measured drift rates,
with a 3x3 tag-facing viewpoint grid over most of the flight volume, so a
count mission tours several zones instead of one sweep near the pad."""
from __future__ import annotations

from dataclasses import replace
import functools
import json
from pathlib import Path

from sentinel.room import live_session
from sentinel.room.semantic import resolve_pose_id
from sentinel.room.site import parse_room_site
from tests.room import test_canningst_mission as mission

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "room_agent" / "v1"
WIDE_SITE = FIXTURES / "canningst-wide-site.json"
WIDE_DRIFT = FIXTURES / "profiles" / "canningst-wide-drift.json"
BASE_SITE = FIXTURES / "canningst-site.json"
HOME_Y_CM = 220.0


def _site(path=WIDE_SITE):
    return parse_room_site(json.loads(path.read_text(encoding="utf-8")))


def _wide_binding(site_changes=None, **drift_changes):
    loaded = live_session.load_flight_binding(
        site_path=WIDE_SITE, drift_profile_path=WIDE_DRIFT,
        energy_profile_path=mission.PROFILES / "canningst-energy.json",
        command_limits_path=mission.PROFILES / "canningst-command-limits.json",
        profile_paths=(mission.PROFILES / "canningst-flight-profile.json",))
    if site_changes:
        loaded = replace(loaded, site=replace(loaded.site, **site_changes))
    if drift_changes:
        loaded = replace(loaded, drift_profile=replace(loaded.drift_profile, **drift_changes))
    return loaded


def _fly(tmp_path, monkeypatch, **changes):
    monkeypatch.setattr(mission, "binding", _wide_binding)
    monkeypatch.setattr(mission, "FlyingTello", functools.partial(mission.FlyingTello, y_cm=HOME_Y_CM))
    return mission.run_mission(tmp_path, monkeypatch, **changes)


def test_the_wide_site_keeps_the_room_and_spreads_a_tag_facing_grid():
    wide, base = _site(), _site(BASE_SITE)
    assert wide.site_id == "canningst-wide"
    assert (wide.cube_site_sha256, wide.tag_map_sha256) == (base.cube_site_sha256, base.tag_map_sha256)
    assert wide.free_space_volumes[:2] == base.free_space_volumes[:2]  # same measured cube and flight volume
    assert (wide.home.nominal_pose.x_cm, wide.home.nominal_pose.y_cm) == (250, HOME_Y_CM)
    poses = [resolve_pose_id(wide, pose_id) for pose_id, _ in wide.pose_visibility]
    assert {round(p.x_cm) for p in poses} == {177, 250, 323} and {round(p.y_cm) for p in poses} == {197, 250, 303}
    assert all(p.yaw_deg == 90 for p in poses)
    assert len(wide.coverage_cells) == 18
    # Only the centre column sees both wall tags head-on.
    assert {pose_id.split("-")[0] for pose_id, _ in wide.pose_tag_visibility} == {"center"}


def test_the_committed_wide_files_are_what_the_generator_builds():
    from scripts import room_site_wide

    assert json.loads(WIDE_SITE.read_text(encoding="utf-8")) == room_site_wide.build_site()
    assert json.loads(WIDE_DRIFT.read_text(encoding="utf-8")) == room_site_wide.build_drift()


def test_the_wide_drift_profile_only_widens_the_uncertainty_limits():
    base = json.loads((mission.PROFILES / "canningst-drift.json").read_text(encoding="utf-8"))
    wide = json.loads(WIDE_DRIFT.read_text(encoding="utf-8"))
    changed = {key for key in base.keys() | wide.keys() if base.get(key) != wide.get(key)}
    assert changed == {"profile_id", "evidence_refs", "max_xy_uncertainty_cm", "max_heading_uncertainty_deg"}
    assert (wide["max_xy_uncertainty_cm"], wide["max_heading_uncertainty_deg"]) == (90, 30)


def test_a_wide_count_mission_tours_four_zones_then_answers_and_lands_on_the_pad(tmp_path, monkeypatch):
    outcome, tello, events, detector = _fly(tmp_path, monkeypatch)
    visited = mission.succeeded_observations(events)
    assert outcome.landing is not None and outcome.landing.state == "LANDED", outcome
    assert abs(tello.x_cm - 250) < 1 and abs(tello.y_cm - HOME_Y_CM) < 1 and not tello.flying
    assert len(visited) == 4 and {pose.split("-")[0] for pose in visited} == {"left", "center", "right"}, visited
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") in mission.tool_outcomes(events)
    assert not mission.refusals(events) & mission.PATH_REFUSALS, mission.refusals(events)


def test_with_the_compact_limits_far_zones_are_refused_and_the_drone_still_answers(tmp_path, monkeypatch):
    """A zone beyond the drift budget is refused on its own, with a reason for
    Jev; the aircraft keeps flying the zones it can reach, then answers."""
    outcome, tello, events, detector = _fly(tmp_path, monkeypatch, max_xy_uncertainty_cm=60,
                                            max_heading_uncertainty_deg=25)
    outcomes = [e.payload for e in events if e.event_type == "ROOM_TOOL_OUTCOME"]
    refused = [o for o in outcomes if o["code"] == "ZONE_REFUSED"]
    assert refused and all(o.get("detail") for o in refused)
    assert len(mission.succeeded_observations(events)) >= 2
    assert outcome.landing is not None and outcome.landing.state == "LANDED" and not tello.flying
    assert outcomes[-1]["candidate_id"].startswith("ANSWER:")
