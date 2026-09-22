import math

import pytest

from sentinel.survey.detector import DETECTOR_RAW_MEDIUM_PROVISIONAL, normalise
from sentinel.survey.merge import CONFIRM_MIN_STOPS, associate, majority_degraded, merge_sightings
from sentinel.survey.observability import FOCAL_PX, PRINCIPAL_X, PRINCIPAL_Y

DELTA_Z_CM = 55.0


def _sighting(sighting_id, stop, x_cm, class_name="computer", bbox=None, band="high",
              description=None, y_cm=0.0, heading_deg=0.0, look_deg=0.0):
    return {"sighting_id": sighting_id, "stop_index": stop, "x_cm": x_cm, "y_cm": y_cm,
            "heading_deg": heading_deg, "look_deg": look_deg, "class": class_name,
            "band": band, "description": description, "bbox": bbox,
            "delta_z_cm": DELTA_Z_CM}


def _looking_at(stop_index, cam_x, target_x, target_y, sighting_id=None, **kwargs):
    """A sighting whose bbox is exactly what a real camera at (cam_x, 0),
    heading 0, would produce for an object resting on the measured ground
    plane at (target_x, target_y). Round-trips exactly through
    geometry.position_of because it is built from the same formula."""
    dx, dy = target_x - cam_x, target_y - 0.0
    bearing = math.atan2(dx, dy)
    u = PRINCIPAL_X + FOCAL_PX * math.tan(bearing)
    # rev 5: a pinhole's image row depends on the forward distance dy, not
    # on the slant range this helper used before (see test_geometry).
    v_px = DELTA_Z_CM * FOCAL_PX / dy
    bbox = {"x": u - 20, "y": PRINCIPAL_Y + v_px - 20, "width": 40, "height": 40}
    return _sighting(sighting_id or f"s{stop_index}", stop_index, cam_x, bbox=bbox, **kwargs)


def test_one_object_seen_from_three_stops_is_counted_once_and_confirmed():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0) for i in range(3)]
    objects = merge_sightings(sightings)
    assert len(objects) == 1
    assert objects[0]["band"] == "CONFIRMED"
    assert objects[0]["distinct_stops"] == CONFIRM_MIN_STOPS
    assert objects[0]["x_cm"] == pytest.approx(100.0, abs=5)
    assert objects[0]["y_cm"] == pytest.approx(300.0, abs=5)


def test_two_well_separated_objects_stay_two():
    sightings = ([_looking_at(i, i * 75.0, -200.0, 300.0) for i in range(3)]
                 + [_looking_at(i, i * 75.0, 400.0, 300.0, sighting_id=f"t{i}")
                    for i in range(3)])
    objects = merge_sightings(sightings)
    assert len(objects) == 2


def test_an_object_seen_from_only_one_stop_is_uncertain_not_dropped():
    objects = merge_sightings([_looking_at(0, 0.0, 100.0, 300.0)])
    assert len(objects) == 1
    assert objects[0]["band"] == "UNCERTAIN"
    assert objects[0]["distinct_stops"] == 1
    assert objects[0]["x_cm"] == pytest.approx(100.0, abs=1.0)


def test_different_classes_never_merge_however_close():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0) for i in range(3)]
    sightings += [_looking_at(i, i * 75.0, 100.0, 300.0, sighting_id=f"p{i}",
                              class_name="phone") for i in range(3)]
    objects = merge_sightings(sightings)
    assert sorted(o["class"] for o in objects) == ["computer", "phone"]


def test_a_sighting_without_a_box_cannot_be_merged_and_stays_uncertain():
    blind = _sighting("b0", 0, 0.0, bbox=None)
    objects = merge_sightings([blind])
    assert objects[0]["band"] == "UNCERTAIN"
    assert objects[0]["x_cm"] is None


def test_a_low_band_sighting_cannot_confirm_an_object():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0, band="low") for i in range(3)]
    assert merge_sightings(sightings)[0]["band"] == "UNCERTAIN"


def test_a_low_band_detector_raw_sighting_cannot_confirm_an_object():
    """Plan phase 4 (D), end-to-end: a real ``DETECTOR_RAW`` score below
    DETECTOR_RAW_MEDIUM_PROVISIONAL bands "low" through the actual
    ``normalise()`` path (not a hand-set ``band="low"`` dict, which
    test_a_low_band_sighting_cannot_confirm_an_object already covers for
    the VLM path), and that band still blocks CONFIRMED once merged."""
    item = {"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.05, "height": 0.05,
            "score": DETECTOR_RAW_MEDIUM_PROVISIONAL - 0.01, "score_kind": "DETECTOR_RAW"}
    _detection, band = normalise([item], 960, 720).pairs[0]
    assert band == "low"

    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0, band=band) for i in range(3)]
    assert merge_sightings(sightings)[0]["band"] == "UNCERTAIN"


def test_people_are_never_position_merged_because_they_move():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0, class_name="person")
                 for i in range(3)]
    objects = merge_sightings(sightings)
    assert all(o["class"] == "person" for o in objects)
    assert all(o["x_cm"] is None for o in objects)


def test_each_sighting_belongs_to_exactly_one_object_even_when_two_are_plausible():
    """rev 3 R1: mutual-nearest-neighbour, so a sighting that could
    plausibly join either of two nearby objects joins only one -- never the
    "accumulate into every cluster within reach" behaviour that let
    triangulated phantoms reach CONFIRMED in rev 1-2."""
    anchor_left = [_looking_at(i, i * 75.0, 0.0, 300.0, sighting_id=f"L{i}") for i in range(3)]
    # rev 6: the tolerance is no longer round. Across the line of sight it is 9% of
    # range for a measured frame (27 cm here), not 25% (75 cm), so for M to be
    # plausibly either object the two must sit 44 cm apart, not 90 -- they are never
    # fused with each other, because each stop sees both in one frame.
    anchor_right = [_looking_at(i, i * 75.0, 44.0, 300.0, sighting_id=f"R{i}") for i in range(3)]
    ambiguous = _looking_at(3, 225.0, 23.0, 300.0, sighting_id="M")

    objects = merge_sightings(anchor_left + anchor_right + [ambiguous])

    assert len(objects) == 2
    all_ids = [sighting_id for obj in objects for sighting_id in obj["sighting_ids"]]
    assert all_ids.count("M") == 1


def test_depth_spread_over_30_percent_marks_the_object_uncertain_and_degraded():
    """Four stops that each put the object somewhere else: 45 cm apart in
    depth, close enough to chain into one cluster, smeared over 135 cm at
    a median depth of 367 cm. That is the geometry disagreeing with itself
    (design spec 5.4). rev 5: this test used to build its disagreement
    from a slant-range projection no camera produces; the sightings it
    flagged agreed perfectly."""
    # rev 6: stops 10 cm apart keep the disagreement along the line of sight, where
    # 25% of range still applies; spread 75 cm apart it also had a sideways part that
    # the tighter across-sight tolerance no longer chains together.
    sightings = [_looking_at(i, i * 10.0, 0.0, 300.0 + 45.0 * i, sighting_id=f"n{i}")
                 for i in range(4)]
    objects = merge_sightings(sightings)
    assert len(objects) == 1
    obj = objects[0]
    assert obj["distinct_stops"] == 4
    assert obj["depth_spread_pct"] > 30.0
    assert obj["band"] == "UNCERTAIN"


def test_depth_spread_stays_low_for_a_well_conditioned_merge():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0) for i in range(3)]
    obj = merge_sightings(sightings)[0]
    assert obj["depth_spread_pct"] < 30.0


def test_associate_is_the_engine_merge_sightings_calls_per_class():
    sightings = [_looking_at(i, i * 75.0, 100.0, 300.0) for i in range(3)]
    objects = associate(sightings)
    assert len(objects) == 1
    assert objects[0]["distinct_stops"] == 3


def test_majority_degraded_objects_trigger_a_sweep_wide_fallback():
    objects = [
        {"depth_spread_pct": 45.0}, {"depth_spread_pct": 50.0}, {"depth_spread_pct": 5.0},
    ]
    assert majority_degraded(objects) is True


def test_a_minority_of_degraded_objects_does_not_trigger_the_fallback():
    objects = [
        {"depth_spread_pct": 45.0}, {"depth_spread_pct": 5.0}, {"depth_spread_pct": 4.0},
    ]
    assert majority_degraded(objects) is False


def test_majority_degraded_of_no_objects_is_false():
    assert majority_degraded([]) is False
