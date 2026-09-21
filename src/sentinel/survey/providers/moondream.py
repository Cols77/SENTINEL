"""Moondream 3 (``detect``) adapter -- the primary provider named in design
spec Sec 6.2: purpose-built for detect/point/count.

C1 (plan section C, 2026-09-21) -- verified against the official Moondream
docs, read-only, no API call:
- https://docs.moondream.ai/skills/detect/
- https://docs.moondream.ai/api/
- https://docs.moondream.ai/quickstart/ (full curl example)
- https://docs.moondream.ai/finetuning/http-api-reference/ (checked for a
  response-side model-identity field; none found)

Confirmed:
- **Endpoint**: ``POST https://api.moondream.ai/v1/detect`` (unchanged).
- **Auth header**: ``X-Moondream-Auth: <api key>`` (unchanged).
- **Image encoding**: a base64 **data URL**, request field ``image_url``,
  shape ``data:{mime_type};base64,{base64_bytes}``. This was the defect --
  the previous code sent ``image_size_bytes`` (a byte count) instead of the
  image itself.
- **Request shape**: ``{"model": ..., "image_url": "data:image/jpeg;base64,...",
  "object": "<one class phrase>"}``. ``object`` is **singular** -- one class
  per call, confirmed by the quickstart's full curl example. ``detect()``
  below therefore makes one request per requested class and merges the
  results (also the previous defect: a single call with
  ``"objects": list(classes)`` was the wrong shape as well as the wrong
  image payload).
- **Response shape**: ``{"request_id": ..., "objects": [{"x_min": ..., "y_min":
  ..., "x_max": ..., "y_max": ...}]}`` -- fractional (0-1) corners, **not**
  the ``x, y, width, height`` shape ``normalise`` expects (hence
  ``_fractional_box`` below), and **not** the ``label``/``confidence``/
  ``description`` per-item fields the previous code and its tests assumed.
- **Model identity**: not present anywhere in the response across all four
  fetched pages -- no ``model`` field, no echo of what was requested.
  Moondream's docs never published a complete response schema page, so
  this is UNVERIFIED beyond the examples actually shown; the identity
  guard below stays (spec 6.3) and is simply never tripped by a real
  response today.

Net consequence of the real response shape: Moondream never returns its
own label for a detection, so ``detect()`` supplies the requested class
name itself as each item's label before handing it to ``normalise``. Every
class this adapter is ever called with is one of
``survey.observability.resolvable_classes``'s keys (``"computer"``,
``"person"``, ``"phone"``, ``"pen"``), and ``CLASS_MAP`` self-maps all four
-- so ``normalise``'s drop-unmapped-label path is structurally unreachable
for a real Moondream call. ``dropped`` is always ``()`` for a Moondream-only
run; this is a real consequence of the real API, not an oversight.
"""
from __future__ import annotations

from ..detector import DetectionBatch, normalise
from . import (ModelIdentityMismatch, ProviderError, encode_image_base64, guess_mime_type,
              post_json, read_image_bytes, to_detection_batch)

#: Confirmed correct by C1 -- see module docstring.
DETECT_URL = "https://api.moondream.ai/v1/detect"


def _fractional_box(obj: dict) -> dict | None:
    """Moondream's box is ``x_min, y_min, x_max, y_max`` fractional (C1) --
    a different shape from the ``x, y, width, height`` ``normalise``
    expects. ``None`` -- never a guess -- when any corner is missing."""
    keys = ("x_min", "y_min", "x_max", "y_max")
    if not all(key in obj for key in keys):
        return None
    x_min, y_min, x_max, y_max = (obj[key] for key in keys)
    return {"x": x_min, "y": y_min, "width": x_max - x_min, "height": y_max - y_min}


class MoondreamDetector:
    """Implements ``DetectorPort``. ``session`` is an injected HTTP client:
    a fake object in every offline test in ``tests/survey/test_providers.py``;
    a real client only at ``scripts/provider_spike.py``'s gated spike or
    ``scripts/survey_run.py``'s ``--detector``/``--fallback moondream``,
    never constructed by this class itself.
    """

    def __init__(self, api_key: str, model: str, session=None) -> None:
        self.api_key = api_key
        self.model = model
        self.session = session

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        image_bytes = read_image_bytes(image_path)
        encoded = encode_image_base64(image_bytes)
        mime = guess_mime_type(image_path)
        image_url = f"data:{mime};base64,{encoded}"
        headers = {"X-Moondream-Auth": self.api_key, "Content-Type": "application/json"}

        items: list[dict] = []
        per_class_raw: dict[str, dict] = {}
        for class_name in classes:
            payload = {"model": self.model, "image_url": image_url, "object": class_name}
            try:
                raw = post_json(self.session, DETECT_URL, headers=headers, payload=payload,
                                provider="moondream")
            except ProviderError:
                # C3/plan: "a failure of one class fails the batch."
                return DetectionBatch((), "PROVIDER_ERROR", raw={"failed_class": class_name})

            # C1: a real response never carries a model field. This guard
            # stays for spec 6.3 ("fail loudly if it differs") and simply
            # never fires against today's documented shape -- it is
            # defensive, not proof the field can never appear.
            resolved_model = raw.get("model")
            if resolved_model is not None and resolved_model != self.model:
                raise ModelIdentityMismatch(
                    f"moondream: pinned {self.model!r} but the provider resolved "
                    f"{resolved_model!r}")

            objects = raw.get("objects")
            if not isinstance(objects, list):
                return DetectionBatch((), "PROVIDER_ERROR", raw=raw)
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                # Moondream never returns its own label (C1) -- the
                # requested class is the only label there is.
                entry = {"label": class_name, "description": None}
                box = _fractional_box(obj)
                if box is not None:
                    entry.update(box)
                items.append(entry)
            per_class_raw[class_name] = raw

        return to_detection_batch(normalise(items), {"requests": per_class_raw})
