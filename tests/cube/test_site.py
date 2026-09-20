import copy

import pytest

from sentinel.cube.site import parse_site
from sentinel.pose.tagmap import TagMap

SITE_DOC = {
    "schema_version": "cube-site-v1",
    "line_y_m": 5.0,
    "stands": [{"x_m": x, "first_tag_id": 2 * i, "heights_m": [0.90, 1.45], "size_m": 0.192}
               for i, x in enumerate([0.25, 0.75, 2.3, 2.7, 3.1, 4.65, 5.15])],
    "cube": {"min_x_m": 0.3, "max_x_m": 5.1, "min_y_m": 2.0, "max_y_m": 4.2, "max_z_m": 2.0},
    "commanded": {"inset_m": 0.3, "min_z_cm": 80, "max_z_cm": 180},
}
CALIBRATION = {"width": 960, "height": 720, "fx": 893.0, "fy": 894.0, "cx": 468.0,
               "cy": 354.0, "dist_coeffs": [], "calibration_source": "chessboard"}


def test_tag_map_has_two_tags_per_stand_facing_minus_y():
    tag_map = TagMap(parse_site(SITE_DOC).tag_map_doc(CALIBRATION))
    assert all(tag_map.has_tag(i) for i in range(14)) and not tag_map.has_tag(14)
    low, high = tag_map.tag(4), tag_map.tag(5)          # third stand, x = 2.3 m
    assert (low.x_units, low.y_units, low.z_units, low.yaw_deg) == (23.0, 50.0, 0.90, -90.0)
    assert (high.x_units, high.z_units, high.zone_id) == (23.0, 1.45, "STAND_2")
    assert tag_map.calibration_source == "chessboard"


def test_envelope_is_the_cube_inset_by_the_drift_margin():
    env = parse_site(SITE_DOC).envelope()
    assert (env.min_x_cm, env.max_x_cm, env.min_y_cm, env.max_y_cm) == (60, 480, 230, 390)
    assert (env.min_z_cm, env.max_z_cm, env.max_move_cm) == (80, 180, 300)


def test_footprint_is_the_cube_itself():
    f = parse_site(SITE_DOC).footprint()
    assert (f.min_x_cm, f.max_x_cm, f.min_y_cm, f.max_y_cm) == (30.0, 510.0, 200.0, 420.0)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(schema_version="nope"), "unsupported"),
    (lambda d: d.update(stands=[]), "stand"),
    (lambda d: d["stands"][1].update(first_tag_id=0), "duplicate"),
    (lambda d: d["cube"].update(max_y_m=4.8), "0.5 m"),     # cube must stop short of the tag line
    (lambda d: d["cube"].update(max_z_m=1.5), "altitude"),  # band top above the cube
])
def test_invalid_sites_are_refused(mutate, message):
    doc = copy.deepcopy(SITE_DOC)
    mutate(doc)
    with pytest.raises(ValueError, match=message):
        parse_site(doc)


def test_cube_site_map_cli_writes_a_loadable_tag_map(tmp_path):
    import json
    from sentinel.cli import main
    from sentinel.pose.tagmap import load_tag_map

    site = tmp_path / "site.json"
    site.write_text(json.dumps(SITE_DOC))
    out = tmp_path / "cube-map.json"
    assert main(["cube-site-map", "--site", str(site), "--calibration-from",
                 "calibration/tag-map-calibrated.json", "--out", str(out)]) == 0
    assert load_tag_map(str(out)).has_tag(13)
