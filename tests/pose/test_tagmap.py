import json, pytest
from sentinel.pose.tagmap import load_tag_map, TagMap

VALID = {
    "schema_version": "tag-map-v1", "tag_family": "36h11",
    "room": {"width_units": 100, "height_units": 100, "origin": "LEFT_ALLEY_FRONT_CORNER"},
    "tags": [{"tag_id": 0, "zone_id": "LEFT_ALLEY_FRONT", "x_units": 5.0, "y_units": 10.0,
              "z_units": 1.5, "yaw_deg": 90.0, "size_m": 0.16, "mounting": "WALL"}],
    "camera_calibration": {"width": 960, "height": 720, "fx": 547.0, "fy": 547.0,
                           "cx": 480.0, "cy": 360.0, "dist_coeffs": [], "calibration_source": "nominal"},
}

def test_loads_and_indexes_by_tag_id(tmp_path):
    p = tmp_path / "tag-map.json"; p.write_text(json.dumps(VALID))
    m = load_tag_map(str(p))
    assert m.tag(0).zone_id == "LEFT_ALLEY_FRONT"
    assert m.K.shape == (3, 3)

def test_wrong_schema_version_is_rejected(tmp_path):
    bad = dict(VALID, schema_version="tag-map-v0")
    p = tmp_path / "bad.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_tag_map(str(p))
