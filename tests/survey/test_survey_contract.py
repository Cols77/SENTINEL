import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "docs" / "superpowers" / "specs"
                     / "contracts" / "sentinel-survey-v1.schema.json").read_text(encoding="utf-8"))


def _validate(name, doc):
    jsonschema.validate(doc, {**SCHEMA, "$ref": f"#/$defs/{name}"})


def test_a_detection_without_a_box_is_valid_because_boxes_are_never_manufactured():
    _validate("Detection", {"class": "computer", "matched_prompt": "laptop",
                            "raw_score": None, "score_kind": "NONE", "bbox": None,
                            "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                            "description": "a laptop on the near table", "provider_item_id": None})


def test_a_vlm_band_is_a_legal_score_kind_and_a_detector_raw_score_is_too():
    for kind in ("VLM_CONFIDENCE_BAND", "DETECTOR_RAW", "NONE"):
        _validate("Detection", {"class": "computer", "matched_prompt": "laptop",
                                "raw_score": 0.9, "score_kind": kind,
                                "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                                "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                                "description": "d", "provider_item_id": "p"})


def test_an_unknown_class_is_rejected():
    with pytest.raises(jsonschema.ValidationError):
        _validate("Detection", {"class": "rock", "matched_prompt": "rock", "raw_score": None,
                                "score_kind": "NONE", "bbox": None,
                                "bbox_coordinate_space": "PIXELS_TOP_LEFT",
                                "description": "d", "provider_item_id": None})


def test_a_class_count_must_declare_how_it_was_obtained():
    with pytest.raises(jsonschema.ValidationError):
        _validate("ClassCount", {"class": "computer", "confirmed": 3, "uncertain": 1})
