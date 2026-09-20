"""Moondream 3 (``detect``) adapter -- the primary provider named in design
spec Sec 6.2: purpose-built for detect/point/count, normalized
``(x, y, width, height)``. Moondream's item shape already matches what
``sentinel.survey.detector.normalise`` expects, so no per-item geometry
conversion happens here (contrast ``gemini.py``, whose ``box_2d`` needs
one).
"""
from __future__ import annotations

from pathlib import Path

from ..detector import DetectionBatch, normalise
from . import ModelIdentityMismatch, ProviderError, post_json, read_image_bytes, to_detection_batch

#: Invented for this offline-only task: the real endpoint is confirmed, if
#: it changes, by Task 12 step 5's gated spike, not guessed here.
DETECT_URL = "https://api.moondream.ai/v1/detect"


class MoondreamDetector:
    """Implements ``DetectorPort``. ``session`` is an injected HTTP client:
    a fake object in every offline test in ``tests/survey/test_providers.py``;
    a real client only at ``scripts/provider_spike.py``'s one gated call
    site, never constructed by this class itself.
    """

    def __init__(self, api_key: str, model: str, session=None) -> None:
        self.api_key = api_key
        self.model = model
        self.session = session

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        image_bytes = read_image_bytes(image_path)
        payload = {
            "image_name": Path(image_path).name,
            "image_size_bytes": len(image_bytes),
            "objects": list(classes),
        }
        headers = {"X-Moondream-Auth": self.api_key, "Content-Type": "application/json"}

        try:
            raw = post_json(self.session, DETECT_URL, headers=headers, payload=payload,
                            provider="moondream")
        except ProviderError:
            return DetectionBatch((), "PROVIDER_ERROR")

        resolved_model = raw.get("model")
        if resolved_model != self.model:
            raise ModelIdentityMismatch(
                f"moondream: pinned {self.model!r} but the provider resolved "
                f"{resolved_model!r}")

        items = raw.get("objects")
        if not isinstance(items, list):
            return DetectionBatch((), "PROVIDER_ERROR", raw=raw)

        return to_detection_batch(normalise(items), raw)
