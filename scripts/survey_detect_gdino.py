#!/usr/bin/env python
"""Run Dzivha's Grounding DINO detector over a frames directory and write one
response JSON per frame in the shape ``sentinel.survey.detector.ReplayDetector``
reads (plan phase 4, section D).

Runs in ``fixtures/perception``'s own uv project, NOT the main environment:

    uv run --project fixtures/perception python scripts/survey_detect_gdino.py \\
        --frames <dir> --out <responses-dir> [--box-threshold ..] [--text-threshold ..]

Self-contained: does **not** import ``sentinel`` anywhere (the main package
is not installed in that environment). Only ``to_response()`` and the
module-level constants below are meant to be unit-tested from the main
environment, without torch -- every ``sentinel_perception``/torch import is
deferred into ``main()``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: class -> the phrase(s) sent to Grounding DINO as open-vocabulary queries.
#: Matches sentinel.survey.detector.CLASS_MAP's recognised labels exactly, so
#: every phrase here, once matched, is mappable downstream without a silent
#: CLASS_MAP_MISS. "computer monitor" is a CLASS_MAP addition from this same
#: phase -- deliberately more specific than the bare "monitor" rev 3 R6
#: removed there (see detector.py's CLASS_MAP comment).
CLASS_PHRASES = {
    "computer": ("laptop", "computer monitor"),
    "person": ("person",),
    "phone": ("mobile phone",),
    "pen": ("pen",),
}

#: phrase -> class, built from CLASS_PHRASES; used only for a sanity assert
#: in main() (every GDINO-matched query must be one we sent).
PHRASE_TO_CLASS = {phrase: cls for cls, phrases in CLASS_PHRASES.items() for phrase in phrases}

ALL_QUERIES = tuple(PHRASE_TO_CLASS)


def to_response(detections: list[dict], width: int, height: int,
                model_id: str, model_sha: str) -> dict:
    """Pure, torch-free: already-cleaned/deduped plain-dict detections
    (keys ``query``/``score``/``box_xyxy``, absolute pixels) -> the
    ReplayDetector-shaped response dict for one frame. Kept a plain dict in,
    not a ``sentinel_perception.detection.Detection`` dataclass, so this is
    testable without importing ``sentinel_perception`` (or torch) at all.

    Each object carries the *raw* Grounding DINO similarity score
    unmodified in "score", plus "score_kind": "DETECTOR_RAW" so
    ``sentinel.survey.detector.normalise`` bands it instead of treating it
    as a VLM confidence string.
    """
    objects = []
    for d in detections:
        x1, y1, x2, y2 = d["box_xyxy"]
        objects.append({
            "label": d["query"],
            "x": x1 / width, "y": y1 / height,
            "width": (x2 - x1) / width, "height": (y2 - y1) / height,
            "score": d["score"],
            "score_kind": "DETECTOR_RAW",
        })
    return {"objects": objects, "model_id": model_id, "model_sha": model_sha}


def main(argv=None) -> int:
    # Heavy imports deferred here -- see module docstring. This environment
    # (fixtures/perception's own uv project) has torch and sentinel_perception
    # installed; the main sentinel environment does not, and must never need
    # them to run this file's tests.
    from PIL import Image
    from sentinel_perception.detection import GroundingDinoDetector, clean, deduplicate, detect_multipass
    from sentinel_perception.models import load_manifest, stage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True, help="directory of frame images")
    parser.add_argument("--out", required=True, help="directory to write one JSON per frame")
    parser.add_argument("--box-threshold", type=float, default=0.15)
    parser.add_argument("--text-threshold", type=float, default=0.20)
    args = parser.parse_args(argv)

    frames_dir = Path(args.frames)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_paths = sorted(p for p in frames_dir.iterdir()
                         if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not frame_paths:
        print(f"no frames in {frames_dir}")
        return 1

    manifest = load_manifest()
    spec = manifest["detector_openvocab"]
    # One short pass covers every phrase (5 total) -- mirrors cli.py's
    # cmd_detect group-size default behaviour when the phrase count is small.
    groups = [list(ALL_QUERIES)]

    with stage("grounding-dino"):
        det = GroundingDinoDetector(spec["id"], spec["sha"],
                                    box_threshold=args.box_threshold,
                                    text_threshold=args.text_threshold)
        # sector=0 for every frame: the survey rail has no GDINO yaw sectors.
        # frame_index is the loop index only; unused downstream of this script.
        for index, path in enumerate(frame_paths):
            width, height = Image.open(path).size
            raw = []
            for result in detect_multipass(det, path, groups, index, 0):
                if result.error:
                    print(f"  {path.name}: ERROR {result.error}")
                    continue
                raw.extend(result.detections)
            for d in raw:
                assert d.query in PHRASE_TO_CLASS, (
                    f"detect_multipass matched an unsent query {d.query!r}")
            kept, _rejected = clean(raw, width, height, list(ALL_QUERIES))
            kept = deduplicate(kept)
            plain = [{"query": d.query, "score": d.score, "box_xyxy": d.box_xyxy} for d in kept]
            response = to_response(plain, width, height, spec["id"], spec["sha"])
            out_path = out_dir / (path.stem + ".json")
            out_path.write_text(json.dumps(response), encoding="utf-8")
            print(f"  {path.name}: {len(raw)} raw -> {len(kept)} kept -> {out_path.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
