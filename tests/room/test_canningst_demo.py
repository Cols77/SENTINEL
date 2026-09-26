"""The CanningSt demo scenario: the compact site under the operator-declared
60 cm predicted-position-error limit.

Live 2026-09-25 (l3-20260925-145838): with the committed 30 cm limit the
left and right rear zones were ZONE_REFUSED at look 2 ("predicted position
error over the 30 cm limit") -- single-tag fixes hold, but never tighten,
the xy budget, so the worst-case look-2 + return-home forecast cannot fit
30 cm from the rear cells. The demo profile raises only that one limit;
every other gate (and the refusal mechanism itself) stays live."""
from __future__ import annotations

import json
from pathlib import Path

from scripts import room_live_prepare
from tests.room import test_canningst_mission as mission

ROOT = Path(__file__).resolve().parents[2]
DEMO_DRIFT = ROOT / "fixtures" / "room_agent" / "v1" / "profiles" / "canningst-demo-drift.json"


def test_the_demo_drift_profile_only_raises_the_declared_demo_limits():
    # Three operator-declared demo raises, each named by its own evidence ref:
    # the 60 cm predicted-position-error limit (2026-09-25 live: rear zones
    # refused at look 2 under 30 cm), the 540 deg yaw budget for demo360's
    # backward looks (three 90-plus-deg turns per sweep exceed the measured
    # profile's 360), and the 45 deg heading-uncertainty budget (2026-09-26
    # live l3-20260926-111405: every fixless rear-wall sweep tripped the 15
    # deg forecast ~70 s in, so the zone could never complete and the battery
    # drained re-trying it; raised generously for the demo, to be fine-tuned
    # down later). Every other gate stays live.
    base = json.loads((mission.PROFILES / "canningst-drift.json").read_text(encoding="utf-8"))
    demo = json.loads(DEMO_DRIFT.read_text(encoding="utf-8"))
    changed = {key for key in base.keys() | demo.keys() if base.get(key) != demo.get(key)}
    assert changed == {"profile_id", "evidence_refs", "max_xy_uncertainty_cm", "max_yaw_deg",
                       "max_heading_uncertainty_deg"}
    assert demo["max_xy_uncertainty_cm"] == 60
    assert demo["max_yaw_deg"] == 540
    assert demo["max_heading_uncertainty_deg"] == 45
    assert demo["validated"] is True
    assert demo["evidence_refs"] == [*base["evidence_refs"],
                                     "operator-declared:demo-uncertainty-limit-20260925",
                                     "operator-declared:max-yaw-540-backward-look-20260925",
                                     "operator-declared:heading-uncertainty-45-backward-sweep-20260926"]


def test_the_prepare_script_offers_the_demo_scenario_on_the_compact_site():
    scenario = room_live_prepare.SCENARIOS["demo"]
    assert scenario.site == room_live_prepare.SITE_PATH
    assert scenario.drift == DEMO_DRIFT
    assert scenario.tag_map == room_live_prepare.TAG_MAP_PATH


def test_with_the_demo_limit_the_rear_zones_are_observed_not_refused(tmp_path, monkeypatch):
    demo = json.loads(DEMO_DRIFT.read_text(encoding="utf-8"))
    outcome, tello, events, detector = mission.run_mission(
        tmp_path, monkeypatch, max_xy_uncertainty_cm=demo["max_xy_uncertainty_cm"])
    assert mission.landed_over_the_pad(outcome, tello), outcome
    observed = mission.succeeded_observations(events)
    assert {mission.CENTER, mission.LEFT, mission.RIGHT} <= set(observed), \
        (observed, mission.tool_outcomes(events))
    refused = [(candidate, code) for candidate, code in mission.tool_outcomes(events)
               if code == "ZONE_REFUSED"]
    assert not refused, refused
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") in mission.tool_outcomes(events)
