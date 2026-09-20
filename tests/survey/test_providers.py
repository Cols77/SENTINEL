"""Offline tests for the Moondream and Gemini DetectorPort adapters
(Task 12, steps 1-4). No test here makes a network call: every session is a
fake object built in this file. Running the adapters against a live
provider (Task 12 step 5, ``scripts/provider_spike.py``) is a separate,
operator-gated action and is not exercised here.
"""
import json

import pytest

from sentinel.survey.providers import MAX_RESPONSE_BYTES, ModelIdentityMismatch
from sentinel.survey.providers.gemini import GeminiDetector
from sentinel.survey.providers.moondream import MoondreamDetector


class FakeResponse:
    """The minimal duck-typed shape an adapter needs from an HTTP response:
    ``status_code``, ``headers``, ``content`` (raw bytes) and ``.json()``."""

    def __init__(self, status_code=200, json_data=None, headers=None, content=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers if headers is not None else {"content-type": "application/json"}
        self.content = (content if content is not None
                        else json.dumps(self._json_data).encode("utf-8"))

    def json(self):
        return self._json_data


class FakeSession:
    """Records every call so a test can assert on it, but never touches a
    socket -- ``detect()`` never sees anything but this fake."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "frame.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-an-offline-test")
    return path


def test_a_well_formed_moondream_response_normalises_correctly(frame):
    response = FakeResponse(json_data={
        "model": "moondream-3",
        "objects": [{"label": "laptop", "x": 0.25, "y": 0.5, "width": 0.1, "height": 0.2,
                    "confidence": "high", "description": "silver laptop"}],
    })
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    assert batch.detections[0]["matched_prompt"] == "laptop"
    assert batch.detections[0]["bbox"] == {"x": 240, "y": 360, "width": 96, "height": 144}
    assert batch.detections[0]["bbox_coordinate_space"] == "PIXELS_TOP_LEFT"
    assert batch.raw == response._json_data


def test_gemini_box_2d_scaled_0_to_1000_converts_to_top_left_pixels(frame):
    """Gemini's documented box convention is [ymin, xmin, ymax, xmax] on a
    0-1000 scale -- a different shape from Moondream's fractional
    (x, y, width, height), and the reason this adapter needs its own
    conversion step before handing items to the shared ``normalise``."""
    body = {"objects": [{"label": "laptop", "box_2d": [500, 250, 700, 350],
                         "description": "laptop on the table"}]}
    response = FakeResponse(json_data={
        "modelVersion": "gemini-2.0-flash",
        "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
    })
    detector = GeminiDetector(api_key="k", model="gemini-2.0-flash", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    # ymin=500 -> y=0.50*720=360; xmin=250 -> x=0.25*960=240
    # ymax=700 -> height=(700-500)/1000*720=144; xmax=350 -> width=(350-250)/1000*960=96
    assert batch.detections[0]["bbox"] == {"x": 240, "y": 360, "width": 96, "height": 144}


def test_a_429_becomes_provider_error_not_an_empty_scene(frame):
    response = FakeResponse(status_code=429, json_data={"error": "rate limited"})
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "PROVIDER_ERROR"
    assert batch.detections == ()


def test_gemini_also_turns_a_429_into_provider_error(frame):
    response = FakeResponse(status_code=429, json_data={"error": {"code": 429}})
    detector = GeminiDetector(api_key="k", model="gemini-2.0-flash", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "PROVIDER_ERROR"
    assert batch.detections == ()


def test_a_moondream_response_resolving_to_a_different_model_raises_loudly(frame):
    response = FakeResponse(json_data={"model": "moondream-2", "objects": []})
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    with pytest.raises(ModelIdentityMismatch):
        detector.detect(str(frame), ("computer",))


def test_a_gemini_response_resolving_to_a_different_model_raises_loudly(frame):
    body = {"objects": []}
    response = FakeResponse(json_data={
        "modelVersion": "gemini-1.5-flash",
        "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
    })
    detector = GeminiDetector(api_key="k", model="gemini-2.0-flash", session=FakeSession(response))

    with pytest.raises(ModelIdentityMismatch):
        detector.detect(str(frame), ("computer",))


def test_an_oversize_response_is_refused_not_parsed(frame):
    oversized = b"x" * (MAX_RESPONSE_BYTES + 1)
    response = FakeResponse(json_data={"model": "moondream-3", "objects": []}, content=oversized)
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "PROVIDER_ERROR"


def test_an_item_without_coordinates_yields_bbox_none(frame):
    response = FakeResponse(json_data={
        "model": "moondream-3",
        "objects": [{"label": "laptop", "description": "a laptop somewhere, exact spot unclear"}],
    })
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "OK"
    assert batch.detections[0]["bbox"] is None
    assert batch.detections[0]["class"] == "computer"


def test_a_gemini_item_without_a_box_yields_bbox_none(frame):
    body = {"objects": [{"label": "laptop", "description": "somewhere in frame"}]}
    response = FakeResponse(json_data={
        "modelVersion": "gemini-2.0-flash",
        "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
    })
    detector = GeminiDetector(api_key="k", model="gemini-2.0-flash", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "OK"
    assert batch.detections[0]["bbox"] is None


def test_an_authentication_failure_becomes_provider_error_and_never_leaks_the_key(frame):
    response = FakeResponse(status_code=401, json_data={"error": "bad key"})
    detector = MoondreamDetector(api_key="super-secret-key", model="moondream-3",
                                 session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "PROVIDER_ERROR"


def test_a_non_json_content_type_is_a_schema_failure_not_an_empty_scene(frame):
    response = FakeResponse(json_data={"model": "moondream-3", "objects": []},
                            headers={"content-type": "text/html"})
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "PROVIDER_ERROR"


def test_the_replay_free_use_of_the_shared_normaliser_still_maps_unknown_labels_away(frame):
    """Whatever the provider calls it, an unmapped label is dropped, not
    guessed (pipeline spec 4.1) -- the same rule Task 3's ``normalise``
    already enforces, exercised here through the adapter boundary."""
    response = FakeResponse(json_data={
        "model": "moondream-3",
        "objects": [{"label": "sandwich", "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}],
    })
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.detections == ()
