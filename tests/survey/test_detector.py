import json

import jsonschema
import pytest

from sentinel.survey.detector import CLASS_MAP, ReplayDetector, ScriptedDetector, normalise
from tests.survey.test_survey_contract import SCHEMA


def _validate_detection(detection):
    jsonschema.validate(detection, {**SCHEMA, "$ref": "#/$defs/Detection"})


def test_normalise_converts_fractional_boxes_to_top_left_pixels():
    items = [{"label": "laptop", "x": 0.25, "y": 0.5, "width": 0.1, "height": 0.2,
              "confidence": "high", "description": "silver laptop"}]
    result = normalise(items, 960, 720)
    detection, band = result.pairs[0]
    assert detection["bbox"] == {"x": 240, "y": 360, "width": 96, "height": 144}
    assert detection["bbox_coordinate_space"] == "PIXELS_TOP_LEFT"
    assert detection["class"] == "computer"
    assert detection["matched_prompt"] == "laptop"
    assert band == "high"


def test_a_vlm_never_claims_a_detector_raw_score():
    """WP3's rule: a VLM band is supplemental and must not be compared to a
    detector threshold."""
    result = normalise([{"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.1,
                        "height": 0.1, "confidence": "high"}], 960, 720)
    detection, _band = result.pairs[0]
    assert detection["score_kind"] == "VLM_CONFIDENCE_BAND"
    assert detection["raw_score"] is None


def test_an_item_with_no_location_survives_with_a_null_box_rather_than_a_guess():
    """Pipeline spec 4.1: a box is never manufactured from prose."""
    result = normalise([{"label": "laptop", "description": "a laptop somewhere"}], 960, 720)
    detection, _band = result.pairs[0]
    assert detection["bbox"] is None
    assert detection["class"] == "computer"


def test_an_unmappable_label_is_dropped_not_guessed_but_traced():
    """rev 3 R6: a dropped label is not silently continued past -- it comes
    back out so a whole-class miss (e.g. every laptop under "macbook") is
    visible rather than reporting an empty scene."""
    result = normalise([{"label": "sandwich", "x": 0.1, "y": 0.1,
                        "width": 0.1, "height": 0.1}], 960, 720)
    assert result.pairs == ()
    assert result.dropped == ("sandwich",)


def test_monitor_is_not_in_the_class_map():
    """rev 3 R6: in a lecture room "monitor" turns every external display
    and the projector screen into a computer."""
    assert "monitor" not in CLASS_MAP
    result = normalise([{"label": "monitor", "x": 0.1, "y": 0.1,
                        "width": 0.1, "height": 0.1}], 960, 720)
    assert result.pairs == ()
    assert result.dropped == ("monitor",)


def test_a_box_outside_the_frame_is_clamped_to_the_frame():
    result = normalise([{"label": "laptop", "x": 0.95, "y": 0.1, "width": 0.2,
                        "height": 0.1}], 960, 720)
    detection, _band = result.pairs[0]
    assert detection["bbox"]["x"] + detection["bbox"]["width"] <= 960


def test_a_detection_produced_by_normalise_validates_against_the_contract():
    """rev 4 V7: band must never be a ninth key on a Detection -- its
    contract is 8 fields with additionalProperties false."""
    items = [{"label": "laptop", "x": 0.25, "y": 0.5, "width": 0.1, "height": 0.2,
              "confidence": "high", "description": "silver laptop", "id": "p1"}]
    detection, band = normalise(items, 960, 720).pairs[0]
    assert "band" not in detection
    assert band == "high"
    _validate_detection(detection)


def test_the_replay_detector_never_calls_out_and_returns_the_stored_response(tmp_path):
    (tmp_path / "f0.json").write_text(json.dumps(
        {"objects": [{"label": "laptop", "x": 0.5, "y": 0.5, "width": 0.1, "height": 0.1,
                      "confidence": "high"}]}),
        encoding="utf-8")
    batch = ReplayDetector(tmp_path).detect("frames/f0.png", ("computer",))
    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    assert batch.bands == ("high",)
    assert batch.dropped == ()
    assert batch.raw == {"objects": [{"label": "laptop", "x": 0.5, "y": 0.5,
                                      "width": 0.1, "height": 0.1, "confidence": "high"}]}


def test_a_missing_replay_response_is_a_provider_error_not_an_empty_scene(tmp_path):
    batch = ReplayDetector(tmp_path).detect("frames/absent.png", ("computer",))
    assert batch.status == "PROVIDER_ERROR"
    assert batch.detections == ()


def test_a_frame_where_most_labels_miss_the_class_map_is_flagged_loudly(tmp_path):
    """rev 3 R6: a model that finds every laptop under an unmapped label
    must not silently report an empty scene."""
    (tmp_path / "f0.json").write_text(json.dumps({"objects": [
        {"label": "macbook", "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
        {"label": "macbook", "x": 0.3, "y": 0.1, "width": 0.1, "height": 0.1},
        {"label": "macbook", "x": 0.5, "y": 0.1, "width": 0.1, "height": 0.1},
        {"label": "pen", "x": 0.7, "y": 0.1, "width": 0.02, "height": 0.02},
    ]}), encoding="utf-8")
    batch = ReplayDetector(tmp_path).detect("frames/f0.png", ("computer", "pen"))
    assert batch.status == "CLASS_MAP_MISS"
    assert len(batch.detections) == 1
    assert batch.dropped == ("macbook", "macbook", "macbook")


def test_a_frame_where_the_class_map_miss_is_a_minority_stays_ok(tmp_path):
    (tmp_path / "f0.json").write_text(json.dumps({"objects": [
        {"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
        {"label": "laptop", "x": 0.3, "y": 0.1, "width": 0.1, "height": 0.1},
        {"label": "laptop", "x": 0.5, "y": 0.1, "width": 0.1, "height": 0.1},
        {"label": "sandwich", "x": 0.7, "y": 0.1, "width": 0.1, "height": 0.1},
    ]}), encoding="utf-8")
    batch = ReplayDetector(tmp_path).detect("frames/f0.png", ("computer",))
    assert batch.status == "OK"
    assert len(batch.detections) == 3
    assert batch.dropped == ("sandwich",)


def test_the_scripted_detector_normalises_the_raw_items_it_was_given():
    script = {"f0.png": [{"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.1,
                          "height": 0.1, "confidence": "medium"}]}
    batch = ScriptedDetector(script).detect("frames/f0.png", ("computer",))
    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    assert batch.bands == ("medium",)


def test_the_scripted_detector_without_a_matching_key_is_a_provider_error():
    batch = ScriptedDetector({}).detect("frames/absent.png", ("computer",))
    assert batch.status == "PROVIDER_ERROR"
    assert batch.detections == ()
