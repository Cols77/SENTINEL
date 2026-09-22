"""Offline tests for the Moondream and Gemini DetectorPort adapters
(Task 12, steps 1-4). No test here makes a network call: every session is a
fake object built in this file. Running the adapters against a live
provider (Task 12 step 5, ``scripts/provider_spike.py``) is a separate,
operator-gated action and is not exercised here.
"""
import base64
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
    """Real Moondream shape (C1): fractional x_min/y_min/x_max/y_max
    corners, no label of its own -- the requested class becomes the
    detection's label."""
    response = FakeResponse(json_data={
        "request_id": "r1",
        "objects": [{"x_min": 0.25, "y_min": 0.5, "x_max": 0.35, "y_max": 0.7}],
    })
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.status == "OK"
    assert batch.detections[0]["class"] == "computer"
    assert batch.detections[0]["matched_prompt"] == "computer"
    # 0.25*960=240, (0.35-0.25)*960=96, 0.5*720=360, (0.7-0.5)*720=144
    assert batch.detections[0]["bbox"] == {"x": 240, "y": 360, "width": 96, "height": 144}
    assert batch.detections[0]["bbox_coordinate_space"] == "PIXELS_TOP_LEFT"


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


def test_a_moondream_item_missing_a_corner_yields_bbox_none(frame):
    """A real Moondream item always carries all four corners (C1); this
    keeps the defensive "never guess a box" behaviour under test for a
    malformed response."""
    response = FakeResponse(json_data={
        "objects": [{"x_min": 0.1, "y_min": 0.1, "x_max": 0.2}],
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


def test_gemini_also_drops_an_unmapped_label_but_traces_it(frame):
    """Whatever the provider calls it, an unmapped label is dropped, not
    guessed (pipeline spec 4.1) -- the same rule Task 3's ``normalise``
    already enforces, exercised here through the adapter boundary. Unlike
    Moondream (C1: it never returns its own label -- see
    ``moondream.py``'s module docstring), Gemini's JSON items *do* carry an
    arbitrary model-supplied label, so this rule stays reachable here."""
    body = {"objects": [{"label": "sandwich", "box_2d": [100, 100, 200, 200]}]}
    response = FakeResponse(json_data={
        "modelVersion": "gemini-2.0-flash",
        "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
    })
    detector = GeminiDetector(api_key="k", model="gemini-2.0-flash", session=FakeSession(response))

    batch = detector.detect(str(frame), ("computer",))

    assert batch.detections == ()
    assert batch.dropped == ("sandwich",)


def test_moondream_payload_image_field_base64_decodes_to_the_exact_file_bytes(frame):
    """The mandatory C2 regression test: the original defect sent a byte
    count instead of the image."""
    response = FakeResponse(json_data={"objects": []})
    session = FakeSession(response)
    MoondreamDetector(api_key="k", model="moondream-3", session=session).detect(
        str(frame), ("computer",))

    sent = session.calls[0]["json"]["image_url"]
    prefix, _, b64 = sent.partition(",")
    assert prefix.startswith("data:image/") and prefix.endswith(";base64")
    assert base64.b64decode(b64) == frame.read_bytes()


def test_gemini_payload_inline_data_base64_decodes_to_the_exact_file_bytes(frame):
    """The mandatory C2 regression test, Gemini side: the original defect
    sent ``data_size_bytes`` instead of the image."""
    body = {"objects": []}
    response = FakeResponse(json_data={
        "modelVersion": "gemini-2.0-flash",
        "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}],
    })
    session = FakeSession(response)
    GeminiDetector(api_key="k", model="gemini-2.0-flash", session=session).detect(
        str(frame), ("computer",))

    parts = session.calls[0]["json"]["contents"][0]["parts"]
    image_part = next(p for p in parts if "inline_data" in p)
    assert base64.b64decode(image_part["inline_data"]["data"]) == frame.read_bytes()


def test_moondream_asks_once_per_class_and_a_second_class_failure_fails_the_whole_batch(frame):
    ok = FakeResponse(json_data={"objects": [{"x_min": 0.1, "y_min": 0.1, "x_max": 0.2,
                                              "y_max": 0.2}]})
    rate_limited = FakeResponse(status_code=429, json_data={"error": "rate limited"})

    class _SequencedSession:
        def __init__(self, responses):
            self._responses = list(responses)
            self.calls = []

        def post(self, url, *, headers, json, timeout):
            self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return self._responses.pop(0)

    session = _SequencedSession([ok, rate_limited])
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=session)

    batch = detector.detect(str(frame), ("computer", "phone"))

    assert batch.status == "PROVIDER_ERROR"
    assert len(session.calls) == 2
    assert session.calls[0]["json"]["object"] == "computer"
    assert session.calls[1]["json"]["object"] == "phone"


def test_moondream_sightings_are_not_banded_low_so_they_can_confirm_an_object(frame):
    """Moondream's detect has no confidence field at all. Banded "low" -- the
    default for a missing confidence -- none of its sightings could ever
    confirm an object, and a Moondream-only run would count nothing. The
    honest reading is "no confidence claimed" (score_kind NONE), with
    confirmation left to agreement from three stops."""
    from sentinel.survey.merge import merge_sightings

    response = FakeResponse(json_data={
        "request_id": "r1",
        "objects": [{"x_min": 0.48, "y_min": 0.60, "x_max": 0.52, "y_max": 0.66}],
    })
    detector = MoondreamDetector(api_key="k", model="moondream-3", session=FakeSession(response))
    batch = detector.detect(str(frame), ("computer",))

    assert batch.bands == ("medium",)
    assert batch.detections[0]["score_kind"] == "NONE"
    assert batch.detections[0]["raw_score"] is None

    sightings = [{"sighting_id": f"s{i}", "class": "computer", "stop_index": i,
                  "band": batch.bands[0], "bbox": batch.detections[0]["bbox"],
                  "frame_x_cm": 0.0, "frame_y_cm": 0.0, "heading_deg": 0.0, "look_deg": 0.0}
                 for i in range(3)]
    assert merge_sightings(sightings)[0]["band"] == "CONFIRMED"


def test_a_vlm_that_omits_its_confidence_is_still_banded_low():
    """Gemini is asked for a confidence word. Leaving it out is not the same
    as having no such field, and stays "low"."""
    from sentinel.survey.detector import normalise

    result = normalise([{"label": "laptop", "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}])
    assert result.pairs[0][1] == "low"
    with pytest.raises(ValueError):
        normalise([], unscored_band="certain")


def _openrouter_answer(text, model="google/gemini-2.5-flash"):
    return FakeResponse(json_data={"model": model,
                                   "choices": [{"message": {"role": "assistant", "content": text}}]})


def test_gemini_through_openrouter_sends_the_image_as_a_data_url_with_a_bearer_key(frame):
    from sentinel.survey.providers.gemini import OPENROUTER_CHAT_URL, OpenRouterGeminiDetector
    session = FakeSession(_openrouter_answer('{"objects": []}'))
    OpenRouterGeminiDetector(api_key="k", model="google/gemini-2.5-flash",
                             session=session).detect(str(frame), ("computer",))

    call = session.calls[0]
    assert call["url"] == OPENROUTER_CHAT_URL
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["json"]["model"] == "google/gemini-2.5-flash"
    image_part = next(p for p in call["json"]["messages"][0]["content"] if p["type"] == "image_url")
    header, data = image_part["image_url"]["url"].split(",", 1)
    assert header == "data:image/jpeg;base64"
    assert base64.b64decode(data) == frame.read_bytes()


def test_gemini_through_openrouter_parses_a_fenced_answer_into_the_same_boxes(frame):
    """Gemini fences its JSON in markdown unless a response schema is set."""
    from sentinel.survey.providers.gemini import OpenRouterGeminiDetector
    body = {"objects": [{"label": "laptop", "box_2d": [500, 250, 700, 350],
                         "description": "laptop on the table"}]}
    session = FakeSession(_openrouter_answer("```json\n" + json.dumps(body) + "\n```"))
    batch = OpenRouterGeminiDetector(api_key="k", model="google/gemini-2.5-flash",
                                     session=session).detect(str(frame), ("computer",))

    assert batch.status == "OK"
    assert batch.detections[0]["bbox"] == {"x": 240, "y": 360, "width": 96, "height": 144}


def test_gemini_direct_also_accepts_a_fenced_answer(frame):
    body = {"objects": [{"label": "laptop", "box_2d": [500, 250, 700, 350]}]}
    response = FakeResponse(json_data={
        "modelVersion": "gemini-2.0-flash",
        "candidates": [{"content": {"parts": [
            {"text": "```json\n" + json.dumps(body) + "\n```"}]}}],
    })
    batch = GeminiDetector(api_key="k", model="gemini-2.0-flash",
                           session=FakeSession(response)).detect(str(frame), ("computer",))
    assert batch.status == "OK" and len(batch.detections) == 1


def test_gemini_through_openrouter_refuses_a_different_resolved_model(frame):
    from sentinel.survey.providers.gemini import OpenRouterGeminiDetector
    session = FakeSession(_openrouter_answer('{"objects": []}',
                                             model="google/gemini-2.5-flash-lite"))
    with pytest.raises(ModelIdentityMismatch):
        OpenRouterGeminiDetector(api_key="k", model="google/gemini-2.5-flash",
                                 session=session).detect(str(frame), ("computer",))


def test_gemini_asks_for_json_mode_on_both_endpoints(frame):
    """Spike 2026-09-22: without JSON mode an empty scene came back as prose."""
    from sentinel.survey.providers.gemini import OpenRouterGeminiDetector
    direct = FakeSession(FakeResponse(json_data={
        "modelVersion": "gemini-2.0-flash",
        "candidates": [{"content": {"parts": [{"text": '{"objects": []}'}]}}]}))
    GeminiDetector(api_key="k", model="gemini-2.0-flash", session=direct).detect(
        str(frame), ("computer",))
    assert direct.calls[0]["json"]["generationConfig"]["responseMimeType"] == "application/json"

    routed = FakeSession(_openrouter_answer('{"objects": []}'))
    OpenRouterGeminiDetector(api_key="k", model="google/gemini-2.5-flash",
                             session=routed).detect(str(frame), ("computer",))
    assert routed.calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_an_empty_gemini_scene_is_ok_not_a_provider_error(frame):
    from sentinel.survey.providers.gemini import OpenRouterGeminiDetector
    batch = OpenRouterGeminiDetector(
        api_key="k", model="google/gemini-2.5-flash",
        session=FakeSession(_openrouter_answer('{"objects": []}'))).detect(str(frame), ("computer",))
    assert batch.status == "OK" and batch.detections == ()


def test_a_prose_answer_is_still_a_provider_error_never_an_empty_scene(frame):
    """The spike's failure: prose is not parsed into 'nothing there'."""
    from sentinel.survey.providers.gemini import OpenRouterGeminiDetector
    batch = OpenRouterGeminiDetector(
        api_key="k", model="google/gemini-2.5-flash",
        session=FakeSession(_openrouter_answer("I did not find any bounding box detections."))
    ).detect(str(frame), ("computer",))
    assert batch.status == "PROVIDER_ERROR"


def test_gemini_confidence_word_bands_the_sighting_so_it_can_confirm(frame):
    from sentinel.survey.providers.gemini import OpenRouterGeminiDetector
    body = {"objects": [{"label": "laptop", "box_2d": [500, 250, 700, 350], "confidence": "high"}]}
    batch = OpenRouterGeminiDetector(
        api_key="k", model="google/gemini-2.5-flash",
        session=FakeSession(_openrouter_answer(json.dumps(body)))).detect(str(frame), ("computer",))
    assert batch.bands == ("high",)
    assert batch.detections[0]["score_kind"] == "VLM_CONFIDENCE_BAND"

