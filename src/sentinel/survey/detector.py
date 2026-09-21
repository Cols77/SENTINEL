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
    "person": "person", "people": "person", "man": "person", "woman": "person",
    "cell phone": "phone", "phone": "phone", "mobile phone": "phone", "smartphone": "phone",
    "pen": "pen", "pencil": "pen",
}

_BANDS = {"high", "medium", "low"}


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


def normalise(items, width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> NormaliseResult:
    pairs: list[tuple[dict, str]] = []
    dropped: list[str] = []
    for item in items:
        label = str(item.get("label", "")).strip().lower()
        mapped = CLASS_MAP.get(label)
        if mapped is None:
            dropped.append(label)
            continue
        band = str(item.get("confidence", "")).strip().lower()
        detection = {
            "class": mapped,
            "matched_prompt": label,
            # A VLM band is never a detector score (WP3 section 2).
            "raw_score": None,
            "score_kind": "VLM_CONFIDENCE_BAND" if band in _BANDS else "NONE",
            "bbox": _box(item, width, height),
            "bbox_coordinate_space": "PIXELS_TOP_LEFT",
            "description": item.get("description"),
            "provider_item_id": item.get("id"),
        }
        pairs.append((detection, band if band in _BANDS else "low"))
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
