# src/sentinel/survey/detector.py
"""The perception boundary for the survey flow.

Implements the shape of ``sentinel.coordinator.ports.PerceptionPort``: one
image in, provider-neutral detections out. Nothing here opens the store,
and nothing here decides anything.

rev 3 R6 and rev 4 V7 amend this module from the plan's original body:

- ``normalise`` never silently drops an unmapped label. It returns the
  labels it could not map alongside the detections it built, so a model
  that finds every laptop under an unrecognised label (e.g. "macbook")
  produces a loud, traceable miss instead of a quiet empty scene.
- The band a VLM reports is never folded into the ``Detection`` document.
  ``Detection``'s contract (``sentinel-survey-v1.schema.json``) is copied
  verbatim from the WP3 pipeline spec: eight fields, ``additionalProperties:
  false``. A ninth key would fail every detection against its own schema.
  ``normalise`` instead returns ``(detection, band)`` pairs; ``band`` lives
  only as a ``Sighting`` property once it reaches the store.
- ``"monitor"`` is removed from ``CLASS_MAP``. In a lecture room it turns
  every external display and the projector screen into a computer.

Plan phase 4 (D) adds one more source: ``scripts/survey_detect_gdino.py``
bridges Dzivha's real Grounding DINO detector into ``ReplayDetector``
response files, each item carrying a numeric ``score`` and
``"score_kind": "DETECTOR_RAW"`` instead of a VLM's ``"confidence"`` band
string. ``normalise`` bands those by two PROVISIONAL module constants (see
below) and preserves the raw float in ``Detection.raw_score`` -- design
spec 6.1's stated WP3-boundary exception, made true in code here ahead of
the design doc revision that reconciles it (plan workstream F).
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol, runtime_checkable

from .observability import IMAGE_HEIGHT, IMAGE_WIDTH

#: Provider vocabulary -> our controlled classes. Explicit and versioned:
#: an unmapped label is dropped (but traced -- see ``normalise``), never
#: guessed (pipeline spec 4.1). "monitor" deliberately absent: rev 3 R6.
CLASS_MAP = {
    "laptop": "computer", "computer": "computer",
    "notebook computer": "computer",
    # Phase 4 (D): the specific two-word phrase scripts/survey_detect_gdino.py
    # sends Grounding DINO, deliberately distinct from the bare "monitor"
    # rev 3 R6 removed above -- that removal was about a single ambiguous
    # word turning every lecture-room external display or projector screen
    # into a computer; "computer monitor" is the class-phrase this bridge
    # asks for on purpose and must resolve, not go silently dropped.
    "computer monitor": "computer",
    "person": "person", "people": "person", "man": "person", "woman": "person",
    "cell phone": "phone", "phone": "phone", "mobile phone": "phone", "smartphone": "phone",
    "mug": "mug", "coffee mug": "mug",
}

_BANDS = {"high", "medium", "low"}

#: PROVISIONAL until the provider spike (plan workstream E) measures real
#: detector score distributions and pins these for real. A DETECTOR_RAW
#: score at or above this is "high"; do not treat these as validated
#: thresholds -- they are a starting point for scripts/survey_detect_gdino.py
#: output, not a measured cutoff.
DETECTOR_RAW_HIGH_PROVISIONAL = 0.35
#: PROVISIONAL, same caveat as above -- the "medium" floor.
DETECTOR_RAW_MEDIUM_PROVISIONAL = 0.25


@dataclass(frozen=True)
class DetectionBatch:
    detections: tuple[dict, ...]
    status: str                    # "OK" | "PROVIDER_ERROR" | "CLASS_MAP_MISS"
    #: Parallel to ``detections`` -- the Sighting-only band, never folded
    #: into a Detection document (rev 4 V7).
    bands: tuple[str, ...] = ()
    #: Raw labels that had no ``CLASS_MAP`` entry (rev 3 R6).
    dropped: tuple[str, ...] = ()
    raw: dict | None = None


@runtime_checkable
class DetectorPort(Protocol):
    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch: ...


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _box(item: dict, width: int, height: int) -> dict | None:
    if not all(key in item for key in ("x", "y", "width", "height")):
        return None
    x = _clamp(int(round(item["x"] * width)), 0, width - 1)
    y = _clamp(int(round(item["y"] * height)), 0, height - 1)
    w = _clamp(int(round(item["width"] * width)), 1, width - x)
    h = _clamp(int(round(item["height"] * height)), 1, height - y)
    return {"x": x, "y": y, "width": w, "height": h}


class NormaliseResult(NamedTuple):
    """``pairs`` is ``(Detection, band)`` so the band never becomes a ninth
    Detection key (rev 4 V7). ``dropped`` is every raw label that had no
    ``CLASS_MAP`` entry, in the order seen, duplicates included (rev 3
    R6) -- the caller decides how loud to be about it."""
    pairs: tuple[tuple[dict, str], ...]
    dropped: tuple[str, ...]


def normalise(items, width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT, *,
              unscored_band: str = "low") -> NormaliseResult:
    """``unscored_band`` is the band of an item that carries neither a
    confidence word nor a raw score. It stays ``"low"`` for a provider that
    *could* have said how sure it was and did not. A provider whose API has
    no confidence at all (Moondream ``detect``) passes ``"medium"``: a
    ``"low"`` sighting can never confirm an object, so ``"low"`` there would
    mean nothing that provider sees is ever counted. ``score_kind`` stays
    ``NONE`` either way -- no confidence is claimed, and what confirms an
    object is agreement from three stops, not the model's word."""
    if unscored_band not in _BANDS:
        raise ValueError(f"unscored_band must be one of {sorted(_BANDS)}")
    pairs: list[tuple[dict, str]] = []
    dropped: list[str] = []
    for item in items:
        label = str(item.get("label", "")).strip().lower()
        mapped = CLASS_MAP.get(label)
        if mapped is None:
            dropped.append(label)
            continue
        # Phase 4 (D): a bridged detector item (scripts/survey_detect_gdino.py)
        # carries a numeric "score" and "score_kind": "DETECTOR_RAW" instead
        # of a VLM's "confidence" band string. Banded here by the PROVISIONAL
        # thresholds above -- this is the WP3-boundary exception design spec
        # 6.1 documents (see this module's docstring / the D handoff doc):
        # the survey flow now does claim DETECTOR_RAW for a real detector, in
        # code, ahead of the design doc being revised to match (plan
        # workstream F's job, not this one's).
        raw_score_kind = str(item.get("score_kind", "")).strip().upper()
        raw_score_value = item.get("score")
        if raw_score_kind == "DETECTOR_RAW" and isinstance(raw_score_value, (int, float)) \
                and not isinstance(raw_score_value, bool):
            raw_score = float(raw_score_value)
            score_kind = "DETECTOR_RAW"
            if raw_score >= DETECTOR_RAW_HIGH_PROVISIONAL:
                band = "high"
            elif raw_score >= DETECTOR_RAW_MEDIUM_PROVISIONAL:
                band = "medium"
            else:
                band = "low"
        else:
            band_str = str(item.get("confidence", "")).strip().lower()
            raw_score = None
            # A VLM band is never a detector score (WP3 section 2).
            score_kind = "VLM_CONFIDENCE_BAND" if band_str in _BANDS else "NONE"
            band = band_str if band_str in _BANDS else unscored_band
        detection = {
            "class": mapped,
            "matched_prompt": label,
            "raw_score": raw_score,
            "score_kind": score_kind,
            "bbox": _box(item, width, height),
            "bbox_coordinate_space": "PIXELS_TOP_LEFT",
            "description": item.get("description"),
            "provider_item_id": item.get("id"),
        }
        pairs.append((detection, band))
    return NormaliseResult(tuple(pairs), tuple(dropped))


def _status_for(pairs: tuple, dropped: tuple) -> str:
    """rev 3 R6: a frame whose dropped count exceeds half its returned
    (successfully mapped) items is a class-map miss, not a clean scene."""
    if len(dropped) > len(pairs) / 2:
        return "CLASS_MAP_MISS"
    return "OK"


def _batch_from(result: NormaliseResult, raw: dict | None = None) -> DetectionBatch:
    detections = tuple(detection for detection, _band in result.pairs)
    bands = tuple(band for _detection, band in result.pairs)
    return DetectionBatch(detections, _status_for(result.pairs, result.dropped),
                          bands, result.dropped, raw)


class ScriptedDetector:
    """Returns whatever the test scripted for that image, normalised the
    same way a real provider's response would be. Keyed by image basename."""

    def __init__(self, script: dict[str, list[dict]]) -> None:
        self.script = script

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        key = Path(image_path).name
        if key not in self.script:
            return DetectionBatch((), "PROVIDER_ERROR")
        return _batch_from(normalise(self.script[key]))


class ReplayDetector:
    """Serves a previously recorded provider response. Never calls out, so
    every replay and every evaluation run is free and repeatable."""

    def __init__(self, responses_dir) -> None:
        self.responses_dir = Path(responses_dir)

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        path = self.responses_dir / (Path(image_path).stem + ".json")
        if not path.exists():
            return DetectionBatch((), "PROVIDER_ERROR")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _batch_from(normalise(raw.get("objects", [])), raw)


def _stamp(batch: DetectionBatch, provider: str, *, primary_error: str | None = None
          ) -> DetectionBatch:
    """Returns ``batch`` with ``raw`` carrying who answered (C3: "the
    returned batch's raw records which provider answered") and, when the
    fallback ran, the primary's error class name."""
    raw = dict(batch.raw or {})
    raw["provider"] = provider
    if primary_error is not None:
        raw["primary_error"] = primary_error
    return dataclasses.replace(batch, raw=raw)


class FallbackDetector:
    """Implements ``DetectorPort``. Tries ``primary`` first; falls back to
    ``fallback`` only when ``primary`` cannot answer at all -- it raised a
    ``ProviderError`` (including ``ModelIdentityMismatch``: a caller built
    specifically to catch and reroute on provider failure is exactly the
    kind of deliberate catcher that error's own docstring requires) or
    returned a batch whose status is ``"PROVIDER_ERROR"`` (this codebase's
    only failure status; the plan prose's "ANALYSIS_FAILED" is this status
    under a different name -- no such literal exists anywhere else in this
    codebase). A ``CLASS_MAP_MISS`` batch is a substantive answer, not a
    failure, and is never a fallback trigger.

    The returned batch's ``raw`` always carries ``"provider"``: which
    detector answered. When the fallback had to run, it also carries
    ``"primary_error"``: the primary's exception class name, or the literal
    string ``"PROVIDER_ERROR"`` when the primary failed by returning a
    failed batch rather than raising (its own adapter already swallowed
    the underlying exception -- see ``providers.post_json``)."""

    def __init__(self, primary, fallback, *, primary_name: str | None = None,
                fallback_name: str | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_name = primary_name or type(primary).__name__
        self.fallback_name = fallback_name or type(fallback).__name__

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        # Local import: providers/__init__.py imports this module
        # (DetectionBatch, NormaliseResult) at package-init time, so a
        # module-level import of providers here would cycle.
        from .providers import ProviderError

        primary_error_name: str | None = None
        try:
            batch = self.primary.detect(image_path, classes)
        except ProviderError as exc:
            primary_error_name = type(exc).__name__
        else:
            if batch.status != "PROVIDER_ERROR":
                return _stamp(batch, self.primary_name)
            primary_error_name = "PROVIDER_ERROR"

        try:
            fallback_batch = self.fallback.detect(image_path, classes)
        except ProviderError as exc:
            return DetectionBatch((), "PROVIDER_ERROR",
                                  raw={"provider": self.fallback_name,
                                       "primary_error": primary_error_name,
                                       "fallback_error": type(exc).__name__})
        return _stamp(fallback_batch, self.fallback_name, primary_error=primary_error_name)
