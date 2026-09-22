"""The tag-coverage guard, calibrated against what the 2026-09-21/22 flights actually did."""
import json
from pathlib import Path

import pytest

from sentinel.cube.coverage import BLIND, MARGINAL, OK, CoverageModel
from sentinel.cube.site import parse_site

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = json.loads((ROOT / "calibration/tag-map-calibrated.json").read_text())["camera_calibration"]
FLIGHT2 = json.loads((ROOT / "docs/wp5-flight2-site.json").read_text())

#: The altitude bias at which the model reproduces the flights below (ground takeoffs, tags
#: tape-measured at 53 cm): `height?` read this much low (tags cut off at the frame bottom).
CALIBRATED_BIAS_CM = 18


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
])
def test_the_guard_reproduces_the_flights(name, xy, allowed):
    assert FLIGHT2_MODEL.verdict(*xy, 100).level in allowed, name


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


def test_the_flight3_site_has_no_blind_cell_in_its_commanded_band():
    site = json.loads((ROOT / "docs/wp5-flight3-site.json").read_text())
    model = CoverageModel(parse_site(site), CALIBRATION)
    for z in (site["commanded"]["min_z_cm"], site["commanded"]["max_z_cm"]):
        levels = {v.level for _, row in model.grid(z, step_cm=30) for _, v in row}
        assert BLIND not in levels, z


def test_cube_run_refuses_a_tag_map_that_disagrees_with_the_site_file():
    from sentinel.cube.coverage import check_tag_map_matches_site
    from sentinel.pose.tagmap import TagMap
    site = parse_site(FLIGHT2)
    check_tag_map_matches_site(TagMap(site.tag_map_doc(CALIBRATION)), site)
    moved = parse_site({**FLIGHT2, "stands": [{**FLIGHT2["stands"][0], "heights_m": [0.33]},
                                              FLIGHT2["stands"][1]]})
    with pytest.raises(ValueError, match="tag 2 sits elsewhere"):
        check_tag_map_matches_site(TagMap(moved.tag_map_doc(CALIBRATION)), site)
