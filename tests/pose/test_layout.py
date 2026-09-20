import numpy as np

from sentinel.cube.site import parse_site
from sentinel.pose.layout import predict_cell, predict_grid
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

TAG_MAP = TagMap(parse_site(SITE_DOC).tag_map_doc(CALIBRATION))


def test_in_front_of_the_centre_block_many_tags_give_a_tight_fix():
    cell = predict_cell(TAG_MAP, 2.7, 3.0, 1.4, rng=np.random.default_rng(0), trials=40)
    assert cell["tags_in_frame"] >= 4          # the design simulation saw 6
    assert cell["p95_cm"] is not None and cell["p95_cm"] < 10.0


def test_close_in_front_of_an_open_alley_mouth_is_blind():
    cell = predict_cell(TAG_MAP, 1.5, 4.0, 1.0, rng=np.random.default_rng(0), trials=10)
    assert cell["tags_in_frame"] == 0 and cell["p95_cm"] is None


def test_grid_is_deterministic_for_a_seed_and_covers_every_cell():
    a = predict_grid(TAG_MAP, [0.6, 2.7], [2.5, 3.5], [1.0], trials=10, seed=3)
    b = predict_grid(TAG_MAP, [0.6, 2.7], [2.5, 3.5], [1.0], trials=10, seed=3)
    assert a == b and len(a) == 4
    assert {(c["x_m"], c["y_m"]) for c in a} == {(0.6, 2.5), (0.6, 3.5), (2.7, 2.5), (2.7, 3.5)}


def test_cube_layout_check_cli_runs(tmp_path, capsys):
    import json
    from sentinel.cli import main

    site = tmp_path / "site.json"
    site.write_text(json.dumps(SITE_DOC))
    code = main(["cube-layout-check", "--site", str(site), "--calibration-from",
                 "calibration/tag-map-calibrated.json", "--alt", "1.4", "--step", "1.5",
                 "--trials", "5"])
    out = capsys.readouterr().out
    assert code == 0 and "altitude 1.4 m" in out and "blind cell" in out
