import json

import jsonschema
import pytest

from sentinel.survey.detector import (CLASS_MAP, DETECTOR_RAW_HIGH_PROVISIONAL,
                                      DETECTOR_RAW_MEDIUM_PROVISIONAL, DetectionBatch,
                                      FallbackDetector, ReplayDetector, ScriptedDetector,
                                      normalise)
from sentinel.survey.providers import ModelIdentityMismatch, ProviderAuthError, ProviderTimeout
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


# -- DETECTOR_RAW banding (plan phase 4, section D) -------------------------

def test_computer_monitor_is_a_distinct_class_map_entry_from_the_removed_monitor():
    """The two-word phrase scripts/survey_detect_gdino.py sends Grounding
    DINO is deliberately more specific than the bare "monitor" rev 3 R6
    removed -- it must resolve, not join "monitor" in being dropped."""
    assert CLASS_MAP["computer monitor"] == "computer"
    result = normalise([{"label": "computer monitor", "x": 0.1, "y": 0.1,
                        "width": 0.1, "height": 0.1}], 960, 720)
    assert result.dropped == ()
    assert result.pairs[0][0]["class"] == "computer"


def _detector_raw_item(score: float) -> dict:
    return {"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1,
            "score": score, "score_kind": "DETECTOR_RAW"}


def test_a_detector_raw_score_at_or_above_the_high_threshold_bands_high():
    detection, band = normalise([_detector_raw_item(DETECTOR_RAW_HIGH_PROVISIONAL)],
                                960, 720).pairs[0]
    assert band == "high"
    assert detection["score_kind"] == "DETECTOR_RAW"
    assert detection["raw_score"] == DETECTOR_RAW_HIGH_PROVISIONAL


def test_a_detector_raw_score_just_under_the_high_threshold_bands_medium():
    detection, band = normalise(
        [_detector_raw_item(DETECTOR_RAW_HIGH_PROVISIONAL - 0.001)], 960, 720).pairs[0]
    assert band == "medium"
    assert detection["raw_score"] == pytest.approx(DETECTOR_RAW_HIGH_PROVISIONAL - 0.001)


def test_a_detector_raw_score_at_or_above_the_medium_threshold_bands_medium():
    detection, band = normalise([_detector_raw_item(DETECTOR_RAW_MEDIUM_PROVISIONAL)],
                                960, 720).pairs[0]
    assert band == "medium"


def test_a_detector_raw_score_just_under_the_medium_threshold_bands_low():
    detection, band = normalise(
        [_detector_raw_item(DETECTOR_RAW_MEDIUM_PROVISIONAL - 0.001)], 960, 720).pairs[0]
    assert band == "low"


def test_a_detector_raw_detection_still_validates_against_the_contract():
    """The banding branch must not add a ninth key or otherwise diverge
    from the Detection contract the VLM path already satisfies (mirrors
    test_a_detection_produced_by_normalise_validates_against_the_contract)."""
    detection, band = normalise([_detector_raw_item(0.9)], 960, 720).pairs[0]
    assert "band" not in detection
    _validate_detection(detection)


def test_a_vlm_confidence_item_is_unaffected_by_the_detector_raw_branch():
    """VLM items keep today's behaviour exactly -- no score/score_kind on
    the input means the existing confidence-band path, unchanged."""
    detection, band = normalise([{"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.1,
                                 "height": 0.1, "confidence": "high"}], 960, 720).pairs[0]
    assert detection["score_kind"] == "VLM_CONFIDENCE_BAND"
    assert detection["raw_score"] is None
    assert band == "high"


def test_the_replay_detector_reads_a_bridge_written_file(tmp_path):
    """ReplayDetector serves a file shaped exactly like
    scripts/survey_detect_gdino.py's to_response() output -- built from the
    real function, not a hand reconstruction of its shape."""
    from scripts.survey_detect_gdino import to_response

    response = to_response(
        [{"query": "laptop", "score": 0.5, "box_xyxy": (96.0, 72.0, 192.0, 144.0)}],
        960, 720, "org/model", "deadbeef")
    (tmp_path / "f0.json").write_text(json.dumps(response), encoding="utf-8")

    batch = ReplayDetector(tmp_path).detect("frames/f0.png", ("computer",))

    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    assert batch.detections[0]["score_kind"] == "DETECTOR_RAW"
    assert batch.detections[0]["raw_score"] == 0.5
    assert batch.bands == ("high",)


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


# -- FallbackDetector (plan section C3) --------------------------------------

_OK_DETECTION = {"class": "computer", "matched_prompt": "computer", "raw_score": None,
                 "score_kind": "NONE", "bbox": None, "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                 "description": None, "provider_item_id": None}


class _RaisingDetector:
    def __init__(self, exc):
        self._exc = exc

    def detect(self, image_path, classes):
        raise self._exc


class _FixedDetector:
    def __init__(self, batch):
        self._batch = batch

    def detect(self, image_path, classes):
        return self._batch


def test_fallback_is_not_tried_when_the_primary_succeeds():
    primary = _FixedDetector(DetectionBatch((_OK_DETECTION,), "OK"))
    fallback = _RaisingDetector(AssertionError("fallback must not be called"))
    detector = FallbackDetector(primary, fallback, primary_name="primary",
                                fallback_name="fallback")

    batch = detector.detect("frame.jpg", ("computer",))

    assert batch.detections == (_OK_DETECTION,)
    assert batch.raw["provider"] == "primary"


def test_fallback_is_not_tried_on_a_class_map_miss():
    primary = _FixedDetector(DetectionBatch((), "CLASS_MAP_MISS", dropped=("sandwich",)))
    fallback = _RaisingDetector(AssertionError("fallback must not be called"))
    detector = FallbackDetector(primary, fallback, primary_name="primary",
                                fallback_name="fallback")

    batch = detector.detect("frame.jpg", ("computer",))

    assert batch.status == "CLASS_MAP_MISS"
    assert batch.raw["provider"] == "primary"


def test_a_provider_error_batch_from_the_primary_triggers_the_fallback():
    primary = _FixedDetector(DetectionBatch((), "PROVIDER_ERROR"))
    fallback = _FixedDetector(DetectionBatch((_OK_DETECTION,), "OK"))
    detector = FallbackDetector(primary, fallback, primary_name="primary",
                                fallback_name="fallback")

    batch = detector.detect("frame.jpg", ("computer",))

    assert batch.detections == (_OK_DETECTION,)
    assert batch.raw["provider"] == "fallback"
    assert batch.raw["primary_error"] == "PROVIDER_ERROR"


def test_a_raised_provider_error_from_the_primary_triggers_the_fallback():
    primary = _RaisingDetector(ProviderTimeout("slow"))
    fallback = _FixedDetector(DetectionBatch((_OK_DETECTION,), "OK"))
    detector = FallbackDetector(primary, fallback, primary_name="primary",
                                fallback_name="fallback")

    batch = detector.detect("frame.jpg", ("computer",))

    assert batch.raw["provider"] == "fallback"
    assert batch.raw["primary_error"] == "ProviderTimeout"


def test_a_raised_model_identity_mismatch_from_the_primary_still_triggers_the_fallback():
    """C3: fallback is tried on ProviderError "incl. subclasses" --
    ModelIdentityMismatch is a ProviderError subclass, and this caller is
    exactly the kind of deliberate catcher its own docstring requires."""
    primary = _RaisingDetector(ModelIdentityMismatch("drift"))
    fallback = _FixedDetector(DetectionBatch((_OK_DETECTION,), "OK"))
    detector = FallbackDetector(primary, fallback, primary_name="primary",
                                fallback_name="fallback")

    batch = detector.detect("frame.jpg", ("computer",))

    assert batch.raw["provider"] == "fallback"
    assert batch.raw["primary_error"] == "ModelIdentityMismatch"


def test_when_both_primary_and_fallback_fail_the_batch_is_provider_error_with_both_named():
    primary = _RaisingDetector(ProviderTimeout("slow"))
    fallback = _RaisingDetector(ProviderAuthError("bad key"))
    detector = FallbackDetector(primary, fallback, primary_name="primary",
                                fallback_name="fallback")

    batch = detector.detect("frame.jpg", ("computer",))

    assert batch.status == "PROVIDER_ERROR"
    assert batch.detections == ()
    assert batch.raw["primary_error"] == "ProviderTimeout"
    assert batch.raw["fallback_error"] == "ProviderAuthError"


def test_the_primarys_own_raw_response_is_preserved_alongside_the_provider_stamp():
    primary = _FixedDetector(DetectionBatch((_OK_DETECTION,), "OK", raw={"objects": ["x"]}))
    fallback = _RaisingDetector(AssertionError("fallback must not be called"))
    detector = FallbackDetector(primary, fallback, primary_name="primary",
                                fallback_name="fallback")

    batch = detector.detect("frame.jpg", ("computer",))

    assert batch.raw["objects"] == ["x"]
    assert batch.raw["provider"] == "primary"
