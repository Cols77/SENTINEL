"""The CanningSt wide scenario: the same room, tags and measured drift rates,
with a 3x3 tag-facing viewpoint grid over most of the flight volume plus four
sideways looks, so a count mission tours several zones and turns to look at
the side walls instead of one sweep near the pad."""
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


def test_the_wide_site_keeps_the_room_and_spreads_a_grid_with_sideways_looks():
    wide, base = _site(), _site(BASE_SITE)
    assert wide.site_id == "canningst-wide"
    assert (wide.cube_site_sha256, wide.tag_map_sha256) == (base.cube_site_sha256, base.tag_map_sha256)
    assert wide.free_space_volumes[:2] == base.free_space_volumes[:2]  # same measured cube and flight volume
    assert (wide.home.nominal_pose.x_cm, wide.home.nominal_pose.y_cm) == (250, HOME_Y_CM)
    poses = [resolve_pose_id(wide, pose_id) for pose_id, _ in wide.pose_visibility]
    assert {round(p.x_cm) for p in poses} == {177, 250, 323} and {round(p.y_cm) for p in poses} == {197, 250, 303}
    headings = sorted(round(p.yaw_deg) % 360 for p in poses)
    assert headings.count(90) == 9 and headings.count(0) == 2 and headings.count(180) == 2
    assert len(wide.coverage_cells) == 24  # wall to wall, x 90-410
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
    assert changed == {"profile_id", "evidence_refs", "max_xy_uncertainty_cm", "max_heading_uncertainty_deg",
                       "max_yaw_deg"}
    assert (wide["max_xy_uncertainty_cm"], wide["max_heading_uncertainty_deg"], wide["max_yaw_deg"]) == (90, 30, 720)


def test_a_wide_count_mission_tours_the_room_then_answers_and_lands_on_the_pad(tmp_path, monkeypatch):
    """A greedy stand-in for Jev (always the top-ranked zone) tours both side
    columns. Its order can leave the last wall-corner zone out of reach
    (refused with a reason), so the answer may be a complete count or an
    explicit incomplete one -- never a stranded aircraft."""
    outcome, tello, events, detector = _fly(tmp_path, monkeypatch)
    visited = mission.succeeded_observations(events)
    assert outcome.landing is not None and outcome.landing.state == "LANDED", outcome
    assert abs(tello.x_cm - 250) < 1 and abs(tello.y_cm - HOME_Y_CM) < 1 and not tello.flying
    assert len(visited) >= 4 and {pose.split("-")[0] for pose in visited} == {"left", "center", "right"}, visited
    answers = [code for candidate, code in mission.tool_outcomes(events) if candidate.startswith("ANSWER:")]
    assert answers in (["ANSWERED_COUNT"], ["ANSWERED_INCOMPLETE"]), answers
    refused = [e.payload for e in events
               if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("code") == "ZONE_REFUSED"]
    assert all(item.get("detail") for item in refused)
    assert not [e for e in events if e.event_type == "SAFETY_ASSESSED"
                and e.payload.get("reason") in ("recovery_exhausted", "LOCALIZATION_LOST")]


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


class _SidewaysFirst(mission.ObservingJudge):
    """A Jev stand-in that always prefers a sideways look when one is offered."""

    def choice(self, state, instruction, *, criteria, name):
        if name == "room_action":
            side = [label for label in criteria
                    if label.startswith("OBSERVE:") and not label.endswith("toward_targets")]
            if side:
                self.names.append(name)
                return {"value": side[0], "probabilities": {label: .99 if label == side[0] else .001
                                                            for label in criteria}}
        return super().choice(state, instruction, criteria=criteria, name=name)


def test_sideways_zones_turn_the_drone_to_the_side_walls_and_it_still_lands_on_the_pad(tmp_path, monkeypatch):
    monkeypatch.setattr(mission, "ObservingJudge", _SidewaysFirst)
    outcome, tello, events, detector = _fly(tmp_path, monkeypatch)
    visited = mission.succeeded_observations(events)
    assert {"center-middle-middle-left", "center-middle-middle-right"} <= set(visited), visited
    assert any(c in tello.sent for c in ("ccw 90", "cw 90")), tello.sent  # really turned to a side wall
    assert outcome.landing is not None and outcome.landing.state == "LANDED" and not tello.flying
    assert abs(tello.x_cm - 250) < 1 and abs(tello.y_cm - HOME_Y_CM) < 1
    assert not [e for e in events if e.event_type == "SAFETY_ASSESSED"
                and e.payload.get("reason") in ("recovery_exhausted", "LOCALIZATION_LOST")]
