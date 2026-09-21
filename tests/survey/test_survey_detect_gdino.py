"""tests for scripts/survey_detect_gdino.py's pure, torch-free surface
(plan phase 4, section D). No sentinel_perception or torch import happens
anywhere in this file -- that is the point: to_response() and the module
constants must stay importable from the main environment alone."""
from scripts.survey_detect_gdino import ALL_QUERIES, CLASS_PHRASES, PHRASE_TO_CLASS, to_response


def test_to_response_converts_absolute_pixels_to_fractional_boxes():
    detections = [{"query": "laptop", "score": 0.42, "box_xyxy": (96.0, 72.0, 192.0, 144.0)}]
    response = to_response(detections, 960, 720, "org/model", "deadbeef")

    assert response["model_id"] == "org/model"
    assert response["model_sha"] == "deadbeef"
    obj = response["objects"][0]
    assert obj["label"] == "laptop"
    assert obj["x"] == 96.0 / 960
    assert obj["y"] == 72.0 / 720
    assert obj["width"] == (192.0 - 96.0) / 960
    assert obj["height"] == (144.0 - 72.0) / 720
    assert obj["score"] == 0.42
    assert obj["score_kind"] == "DETECTOR_RAW"


def test_to_response_round_trips_a_full_frame_box_to_the_unit_square():
    detections = [{"query": "person", "score": 0.9, "box_xyxy": (0.0, 0.0, 960.0, 720.0)}]
    obj = to_response(detections, 960, 720, "org/model", "sha")["objects"][0]
    assert obj["x"] == 0.0
    assert obj["y"] == 0.0
    assert obj["width"] == 1.0
    assert obj["height"] == 1.0


def test_to_response_with_no_detections_is_an_empty_objects_list():
    response = to_response([], 960, 720, "org/model", "sha")
    assert response["objects"] == []
    assert response["model_id"] == "org/model"
    assert response["model_sha"] == "sha"


def test_to_response_echoes_the_raw_score_unmodified():
    """score_kind DETECTOR_RAW means normalise() bands this score itself --
    to_response must never round, clamp or otherwise alter it."""
    detections = [{"query": "pen", "score": 0.123456, "box_xyxy": (10.0, 10.0, 20.0, 20.0)}]
    obj = to_response(detections, 960, 720, "org/model", "sha")["objects"][0]
    assert obj["score"] == 0.123456


def test_class_phrases_every_phrase_is_in_all_queries_and_maps_back_to_its_class():
    for class_name, phrases in CLASS_PHRASES.items():
        for phrase in phrases:
            assert phrase in ALL_QUERIES
            assert PHRASE_TO_CLASS[phrase] == class_name
