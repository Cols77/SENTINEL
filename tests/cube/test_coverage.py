"""The tag-coverage guard, calibrated against what the 2026-09-21/22 flights actually did."""
import json
from pathlib import Path

import pytest

from sentinel.cube.coverage import BLIND, MARGINAL, OK, CoverageModel
from sentinel.cube.site import parse_site

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = json.loads((ROOT / "calibration/tag-map-calibrated.json").read_text())["camera_calibration"]
FLIGHT2 = json.loads((ROOT / "docs/wp5-flight2-site.json").read_text())

#: The height offset at which the model reproduces the flights below, fitted together with
#: coverage.HOVER_PITCH_DOWN_DEG (7 deg): the drone flies this far above the altitude the runner
#: tracks. Confirmed on 2026-09-23 by the tags, by a range-free pixel reading and by the operator's
#: tape (~120 cm at a commanded 70). The site files carry the same number.
CALIBRATED_BIAS_CM = 45


def _model(doc=FLIGHT2, **changes):
    return CoverageModel(parse_site({**doc, **changes}), CALIBRATION)


FLIGHT2_MODEL = _model(altitude_bias_cm=CALIBRATED_BIAS_CM)


# (x, y) commanded at 100 cm, and what the flight saw there
@pytest.mark.parametrize("name, xy, allowed", [
    ("R_NEAR 09-22: no tag in 287 frames", (300, 270), {BLIND}),
    ("L_NEAR 09-22: three trims, tags on the frame edge", (215, 270), {BLIND}),
    ("R_EDGE 09-22: localised, 1 tag at times", (300, 250), {MARGINAL}),
    ("L_EDGE 09-22: localised", (210, 250), {MARGINAL}),
    ("R_FAR 09-22: localised, 4 cm", (300, 205), {MARGINAL, OK}),
    ("DEEP 09-22: localised", (250, 190), {OK}),
    ("L_FAR 09-22: localised", (215, 205), {OK}),
    ("HOME 09-21: localised every run", (250, 250), {OK}),
    ("LEFT_FAR 09-21: localised", (220, 215), {OK}),
    ("FAR_L_HI 09-23: localised at 135 cm commanded", (190, 145), {OK, MARGINAL}),
    ("FAR_MID_HI 09-23: localised, 1.2 cm", (250, 145), {OK, MARGINAL}),
])
def test_the_guard_reproduces_the_flights(name, xy, allowed):
    z = 135 if "09-23" in name else 100
    model = _model(altitude_bias_cm=CALIBRATED_BIAS_CM) if "09-23" not in name else _flight3()
    assert model.verdict(*xy, z).level in allowed, name


def _flight3():
    return CoverageModel(parse_site(json.loads((ROOT / "docs/wp5-flight3-site.json").read_text())),
                         CALIBRATION)


def test_the_height_offset_and_the_hover_pitch_are_calibrated_as_a_pair():
    """Neither is identifiable alone: a higher drone and a more downward camera trade off. The
    pair is what the flights fix -- with the site's 45 cm but no hover tilt, four 09-22 stops that
    localised every run come out BLIND."""
    from sentinel.cube import coverage as coverage_module
    original = coverage_module.HOVER_PITCH_DOWN_DEG
    coverage_module.HOVER_PITCH_DOWN_DEG = 0.0
    try:
        flat = _model(altitude_bias_cm=CALIBRATED_BIAS_CM)
        assert flat.verdict(250, 250, 100).level == BLIND        # HOME, which never failed
    finally:
        coverage_module.HOVER_PITCH_DOWN_DEG = original
    assert _model(altitude_bias_cm=CALIBRATED_BIAS_CM).verdict(250, 250, 100).level != BLIND


def test_a_flight_can_re_check_its_stops_from_the_height_it_measured():
    model = _flight3()
    assert model.height_offset_cm == 45
    higher = model.measured(90.0)
    assert higher.height_offset_cm == 90 and model.height_offset_cm == 45
    assert higher.verdict(250, 145, 135).level == BLIND          # 90 cm higher loses the tags


def test_without_the_bias_the_model_would_have_passed_the_blind_stop():
    """The reason the site carries altitude_bias_cm: with 0, R_NEAR looked fine."""
    assert _model(altitude_bias_cm=0).verdict(300, 270, 100).level != BLIND


def test_a_takeoff_surface_raises_the_drone_like_a_bias():
    """Taking off from a 20 cm box and commanding 100 puts the camera where a 20 cm bias does."""
    assert (_model(takeoff_z_cm=20, altitude_bias_cm=0).verdict(300, 270, 100)
            == _model(takeoff_z_cm=0, altitude_bias_cm=20).verdict(300, 270, 100))


def test_the_verdict_follows_the_tags_not_a_fixed_region():
    """Another venue is another site file: raising the tags 40 cm opens the near row."""
    raised = {**FLIGHT2, "altitude_bias_cm": CALIBRATED_BIAS_CM,
              "stands": [{**s, "heights_m": [0.93]} for s in FLIGHT2["stands"]]}
    assert _model(raised).verdict(215, 270, 100).level != BLIND
    moved = {**FLIGHT2, "altitude_bias_cm": CALIBRATED_BIAS_CM,
             "stands": [{**s, "x_m": s["x_m"] + 1.0} for s in FLIGHT2["stands"]]}
    assert _model(moved).verdict(210, 250, 100).level == BLIND


def test_the_flight3_site_is_flyable_low_everywhere_and_high_on_its_far_half():
    site = json.loads((ROOT / "docs/wp5-flight3-site.json").read_text())
    model = CoverageModel(parse_site(site), CALIBRATION)
    low = {v.level for _, row in model.grid(site["commanded"]["min_z_cm"], step_cm=30) for _, v in row}
    assert BLIND not in low
    top = model.grid(site["commanded"]["max_z_cm"], step_cm=30)
    assert all(v.level != BLIND for y, row in top if y <= 170 for _, v in row)
    assert any(v.level == BLIND for y, row in top if y >= 230 for _, v in row)


def test_cube_run_refuses_a_tag_map_that_disagrees_with_the_site_file():
    from sentinel.cube.coverage import check_tag_map_matches_site
    from sentinel.pose.tagmap import TagMap
    site = parse_site(FLIGHT2)
    check_tag_map_matches_site(TagMap(site.tag_map_doc(CALIBRATION)), site)
    moved = parse_site({**FLIGHT2, "stands": [{**FLIGHT2["stands"][0], "heights_m": [0.33]},
                                              FLIGHT2["stands"][1]]})
    with pytest.raises(ValueError, match="tag 2 sits elsewhere"):
        check_tag_map_matches_site(TagMap(moved.tag_map_doc(CALIBRATION)), site)


def test_commanded_heading_preserves_defaults_and_changes_visibility():
    model = FLIGHT2_MODEL
    assert model.verdict(250, 190, 100) == model.verdict(250, 190, 100, commanded_yaw_deg=90)
    assert model.tags_in_view(250, 190, 100, commanded_yaw_deg=270) == 0
    assert model.verdict(250, 190, 100, commanded_yaw_deg=270).level == BLIND


def test_heading_and_xy_uncertainty_expand_the_evaluated_region():
    model = FLIGHT2_MODEL
    assert model.verdict(250, 190, 100).level == OK
    assert model.verdict(250, 190, 100, heading_uncertainty_deg=180).level == BLIND
    assert model.verdict(250, 190, 100, xy_uncertainty_cm=200).level == BLIND
    with pytest.raises(ValueError):
        model.verdict(250, 190, 100, heading_uncertainty_deg=-1)
