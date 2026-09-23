"""tests for scripts/survey_detect_gdino.py's pure, torch-free surface
(plan phase 4, section D). No sentinel_perception or torch import happens
anywhere in this file -- that is the point: to_response() and the module
constants must stay importable from the main environment alone."""
import json
import sys
import types
from contextlib import contextmanager

from PIL import Image

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


def _install_fake_sentinel_perception(monkeypatch, *, bad_query):
    """Stub sentinel_perception.detection/.models well enough to drive
    main() without torch. ``bad_query`` stands in for what
    detect_multipass() really returns for a garbled label: _match_query's
    documented fallback (detection.py) returns the raw label unchanged when
    nothing substring-matches, e.g. a merge of two sent phrases ("rock
    boulder") -- never a member of PHRASE_TO_CLASS. The fake clean() filters
    it the way the real is_valid_label()-backed clean() would, so this
    reproduces main() asserting over unfiltered raw detections."""

    class FakeDetection:
        def __init__(self, query, score, box_xyxy):
            self.query = query
            self.score = score
            self.box_xyxy = box_xyxy

    class FakeResult:
        error = None

        def __init__(self, detections):
            self.detections = detections

    good = FakeDetection("laptop", 0.5, (1.0, 1.0, 5.0, 5.0))
    bad = FakeDetection(bad_query, 0.9, (2.0, 2.0, 6.0, 6.0))

    def fake_detect_multipass(detector, image_path, query_groups, frame_index, sector):
        return [FakeResult([good, bad])]

    def fake_clean(detections, img_w, img_h, queries, drop_stuff=True):
        return [d for d in detections if d.query in queries], {}

    def fake_deduplicate(detections, iou_thresh=0.65):
        return detections

    class FakeGroundingDinoDetector:
        def __init__(self, model_id, model_sha, box_threshold=0.15, text_threshold=0.20):
            pass

    def fake_load_manifest():
        return {"detector_openvocab": {"id": "org/fake-model", "sha": "deadbeef"}}

    @contextmanager
    def fake_stage(name):
        yield

    detection_mod = types.ModuleType("sentinel_perception.detection")
    detection_mod.GroundingDinoDetector = FakeGroundingDinoDetector
    detection_mod.clean = fake_clean
    detection_mod.deduplicate = fake_deduplicate
    detection_mod.detect_multipass = fake_detect_multipass
    models_mod = types.ModuleType("sentinel_perception.models")
    models_mod.load_manifest = fake_load_manifest
    models_mod.stage = fake_stage
    pkg = types.ModuleType("sentinel_perception")
    pkg.detection = detection_mod
    pkg.models = models_mod

    monkeypatch.setitem(sys.modules, "sentinel_perception", pkg)
    monkeypatch.setitem(sys.modules, "sentinel_perception.detection", detection_mod)
    monkeypatch.setitem(sys.modules, "sentinel_perception.models", models_mod)


def test_main_drops_an_unmatched_raw_query_instead_of_crashing(monkeypatch, tmp_path):
    """Regression for the review finding: main() used to assert every RAW
    detect_multipass() detection was one of the 5 sent phrases, before
    clean() had a chance to filter the tokenizer fragments and merged-query
    labels detection.py's own docstring says are routine on real footage.
    That assert fired on the first garbled frame and killed the whole run.
    It must instead only ever see already-cleaned detections and so never
    fire on this common case."""
    _install_fake_sentinel_perception(monkeypatch, bad_query="rock boulder")

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    out_dir = tmp_path / "out"
    Image.new("RGB", (100, 80)).save(frames_dir / "frame0001.png")

    from scripts.survey_detect_gdino import main

    rc = main(["--frames", str(frames_dir), "--out", str(out_dir)])

    assert rc == 0
    written = json.loads((out_dir / "frame0001.json").read_text(encoding="utf-8"))
    assert [obj["label"] for obj in written["objects"]] == ["laptop"]
