"""Gemini Flash adapter -- the fallback provider named in design spec
Sec 6.2, "a different architecture that fails differently."

Gemini's structured bounding-box output uses ``box_2d: [ymin, xmin, ymax,
xmax]`` scaled 0-1000 -- a different shape from Moondream's fractional
``(x, y, width, height)``. ``_to_fractional_box`` is the one conversion step
this adapter needs that ``moondream.py`` does not; everything else is
identical.
"""
from __future__ import annotations

import json

from ..detector import DetectionBatch, normalise
from . import ModelIdentityMismatch, ProviderError, post_json, read_image_bytes, to_detection_batch

#: Invented for this offline-only task: the real endpoint and payload shape
#: are confirmed, if they differ, by Task 12 step 5's gated spike, not
#: guessed here.
GENERATE_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent")


def _to_fractional_box(box_2d) -> dict[str, float] | None:
    """``box_2d`` is ``[ymin, xmin, ymax, xmax]`` on a 0-1000 scale. Returns
    the ``(x, y, width, height)`` fraction ``normalise`` expects, or
    ``None`` when the item carries no box at all -- a box is never
    manufactured from prose (pipeline spec 4.1)."""
    if not box_2d or len(box_2d) != 4:
        return None
    ymin, xmin, ymax, xmax = box_2d
    return {
        "x": xmin / 1000.0,
        "y": ymin / 1000.0,
        "width": (xmax - xmin) / 1000.0,
        "height": (ymax - ymin) / 1000.0,
    }


def _items_from(raw: dict) -> list[dict] | None:
    """Gemini answers in free-form text even when the prompt asks for JSON,
    so the detection list is nested inside the first candidate's text
    part. Returns ``None`` -- never raises -- for any shape this adapter
    does not recognise; the caller turns that into ``PROVIDER_ERROR``."""
    try:
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        items = parsed["objects"] if isinstance(parsed, dict) else parsed
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return items if isinstance(items, list) else None


class GeminiDetector:
    """Implements ``DetectorPort``. ``session`` is an injected HTTP client,
    exactly as ``MoondreamDetector``'s -- a fake in every offline test, a
    real client only at ``scripts/provider_spike.py``'s one gated call
    site.
    """

    def __init__(self, api_key: str, model: str, session=None) -> None:
        self.api_key = api_key
        self.model = model
        self.session = session

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        image_bytes = read_image_bytes(image_path)
        prompt = (
            "Detect every instance of: " + ", ".join(classes) + '. Return a JSON object '
            '{"objects": [...]} whose items each have "label", "box_2d" as '
            '[ymin, xmin, ymax, xmax] scaled 0 to 1000, and "description".')
        payload = {
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data_size_bytes": len(image_bytes)}},
            ]}],
        }
        url = GENERATE_URL_TEMPLATE.format(model=self.model)
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

        try:
            raw = post_json(self.session, url, headers=headers, payload=payload,
                            provider="gemini")
        except ProviderError:
            return DetectionBatch((), "PROVIDER_ERROR")

        resolved_model = raw.get("modelVersion")
        if resolved_model != self.model:
            raise ModelIdentityMismatch(
                f"gemini: pinned {self.model!r} but the provider resolved {resolved_model!r}")

        items = _items_from(raw)
        if items is None:
            return DetectionBatch((), "PROVIDER_ERROR", raw=raw)

        converted = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = {"label": item.get("label", ""), "description": item.get("description")}
            box = _to_fractional_box(item.get("box_2d"))
            if box is not None:
                entry.update(box)
            converted.append(entry)

        return to_detection_batch(normalise(converted), raw)
