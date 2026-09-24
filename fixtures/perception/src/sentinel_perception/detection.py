from __future__ import annotations

import inspect
from dataclasses import dataclass, asdict, field
from typing import Optional

import torch
from PIL import Image
from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

from .config import STUFF_QUERIES
from .models import pick_device, pick_dtype

DETECTION_VERSION = "detect/2026-09-16.2"


@dataclass
class Detection:
    label: str                 # the matched phrase, as returned
    query: str                 # the prompt phrase we asked for
    score: float               # RAW similarity. Not a probability.
    box_xyxy: tuple[float, float, float, float]
    frame_index: int
    sector: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetectionResult:
    model_id: str
    model_sha: str
    prompt: str
    queries: list[str]
    box_threshold: float
    text_threshold: float
    detections: list[Detection] = field(default_factory=list)
    latency_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["detections"] = [x.to_dict() for x in self.detections]
        return d


def build_prompt(queries: list[str]) -> str:
    """Normalise into Grounding DINO's expected form: lowercase, period-ended."""
    parts = [q.strip().lower().rstrip(".") for q in queries if q.strip()]
    return " ".join(f"{p}." for p in parts)


def _match_query(label: str, queries: list[str]) -> str:
    """Map a returned phrase back to the query that produced it.

    Grounding DINO returns the matched span of the prompt, which may be a
    fragment ('scree' from 'loose scree') or a merge of adjacent phrases. We
    need the original query for grouping and counting, so resolve by substring
    with the longest match winning.
    """
    lab = label.strip().lower()
    best, best_len = lab, 0
    for q in queries:
        ql = q.strip().lower()
        if (ql in lab or lab in ql) and len(ql) > best_len:
            best, best_len = q, len(ql)
    return best


class GroundingDinoDetector:
    """Local Grounding DINO. Loads on construction, freed by models.stage()."""

    def __init__(self, model_id: str, model_sha: str = "",
                 box_threshold: float = 0.15, text_threshold: float = 0.20,
                 *, revision: str | None = None, local_files_only: bool = False):
        self.model_id = model_id
        self.model_sha = model_sha
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold
        self.device = pick_device()
        self.dtype = pick_dtype(self.device)

        source_options = {"local_files_only": local_files_only}
        if revision:
            source_options["revision"] = revision
        self.processor = AutoProcessor.from_pretrained(model_id, **source_options)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(
            model_id, dtype=self.dtype, **source_options
        ).to(self.device).eval()

        # transformers renamed post-processing kwargs across versions. Resolve
        # once here rather than guessing at every call site.
        sig = inspect.signature(
            self.processor.post_process_grounded_object_detection
        ).parameters
        self._box_kw = "threshold" if "threshold" in sig else "box_threshold"

    def detect(self, image_path, queries, frame_index, sector):
        image = Image.open(image_path).convert("RGB")
        return self._detect_pil(image, queries, frame_index, sector)

    def detect_array(self, bgr: "np.ndarray", queries, frame_index, sector):
        """Same inference, from an in-memory OpenCV frame."""
        import cv2
        image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        return self._detect_pil(image, queries, frame_index, sector)
    
    @torch.inference_mode()
    def _detect_pil(self, image: Image.Image, queries: list[str],
                    frame_index: int, sector: int) -> DetectionResult:
        import time
        t0 = time.time()

        prompt = build_prompt(queries)
        result = DetectionResult(
            model_id=self.model_id, model_sha=self.model_sha, prompt=prompt,
            queries=queries, box_threshold=self.box_threshold,
            text_threshold=self.text_threshold,
        )

        try:
            inputs = self.processor(images=image, text=prompt, return_tensors="pt")
            inputs = {k: (v.to(self.device, self.dtype)
                          if torch.is_floating_point(v) else v.to(self.device))
                      for k, v in inputs.items()}
            outputs = self.model(**inputs)

            kwargs = {
                self._box_kw: self.box_threshold,
                "text_threshold": self.text_threshold,
                "target_sizes": [image.size[::-1]],   # (h, w)
            }
            post = self.processor.post_process_grounded_object_detection(
                outputs, inputs["input_ids"], **kwargs
            )[0]

            for score, label, box in zip(post["scores"], post["text_labels"]
                                         if "text_labels" in post else post["labels"],
                                         post["boxes"]):
                result.detections.append(Detection(
                    label=str(label),
                    query=_match_query(str(label), queries),
                    score=round(float(score), 4),
                    box_xyxy=tuple(round(float(v), 1) for v in box.tolist()),
                    frame_index=frame_index,
                    sector=sector,
                ))
        except Exception as exc:                        # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"

        result.latency_ms = int((time.time() - t0) * 1000)
        return result


def sweep_thresholds(detections: list[Detection],
                     values: list[float]) -> list[dict]:
    """How many detections survive at each candidate threshold.

    Run this on real output before choosing a confirmation threshold. Picking a
    number first and discovering nothing clears it is the most common way these
    pipelines fail.
    """
    rows = []
    for t in values:
        kept = [d for d in detections if d.score >= t]
        rows.append({
            "threshold": t,
            "n_detections": len(kept),
            "n_sectors": len({d.sector for d in kept}),
            "by_query": {q: sum(1 for d in kept if d.query == q)
                         for q in sorted({d.query for d in detections})},
        })
    return rows

# ---------------------------------------------------------------------------
# Post-processing. Measured behaviour on real footage this handles:
#
#   Degenerate boxes -- when the model cannot ground a phrase it returns boxes
#   spanning most of the frame, or near-identical overlapping boxes.
#
#   Tokenizer fragments -- returned labels are spans of the prompt, so failed
#   grounding yields '##ree', 'looseree', '', or merges like 'rock boulder'.
#
#   Stuff versus objects -- material regions have no object boundary, so a
#   box around them is the whole region or noise. See config.STUFF_QUERIES.
# ---------------------------------------------------------------------------


def is_degenerate(box, img_w: int, img_h: int,
                  max_area_frac: float = 0.55,
                  min_side_px: float = 12.0) -> Optional[str]:
    """Reject boxes that indicate failed grounding rather than a detection."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    if w < min_side_px or h < min_side_px:
        return "TOO_SMALL"
    if (w * h) / (img_w * img_h) > max_area_frac:
        # A box covering most of the frame localises nothing. Legitimate for
        # stuff queries, which is exactly why those go to segmentation.
        return "FRAME_SPANNING"
    if x2 <= 0 or y2 <= 0 or x1 >= img_w or y1 >= img_h:
        return "OUT_OF_BOUNDS"
    return None


def is_valid_label(label: str, queries: list[str]) -> Optional[str]:
    """Reject labels that are tokenizer artifacts rather than grounded classes."""
    lab = label.strip().lower()
    if not lab:
        return "EMPTY_LABEL"
    if lab.startswith("##"):
        return "SUBWORD_FRAGMENT"
    ql = [q.strip().lower() for q in queries]
    if any(lab == q for q in ql):
        return None
    # Accept a clean substring of one query ('scree' from 'loose scree'), but
    # reject fragments that match nothing ('looseree') and merges spanning two
    # queries ('rock boulder').
    contained = [q for q in ql if lab in q or q in lab]
    if not contained:
        return "UNMATCHED_FRAGMENT"
    if sum(1 for q in ql if q in lab) > 1:
        return "MERGED_QUERIES"
    return None


def clean(detections: list[Detection], img_w: int, img_h: int,
          queries: list[str], drop_stuff: bool = True) -> tuple[list[Detection], dict]:
    """Filter a detection list. Returns (kept, rejection_counts).
    """
    from collections import Counter

    kept, reasons = [], Counter()
    for d in detections:
        if drop_stuff and d.query.strip().lower() in STUFF_QUERIES:
            reasons["STUFF_QUERY"] += 1
            continue
        why = is_valid_label(d.label, queries) or is_degenerate(d.box_xyxy, img_w, img_h)
        if why:
            reasons[why] += 1
            continue
        kept.append(d)
    return kept, dict(reasons)


def deduplicate(detections: list[Detection], iou_thresh: float = 0.65) -> list[Detection]:
    """Class-wise NMS over the cleaned set.
    """
    def iou(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        ua = (ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter
        return inter / ua if ua > 0 else 0.0

    out = []
    for group in {(d.sector, d.query) for d in detections}:
        cand = sorted([d for d in detections if (d.sector, d.query) == group],
                      key=lambda d: -d.score)
        keep = []
        for d in cand:
            if all(iou(d.box_xyxy, k.box_xyxy) < iou_thresh for k in keep):
                keep.append(d)
        out.extend(keep)
    return sorted(out, key=lambda d: -d.score)


def detect_multipass(detector, image_path, query_groups: list[list[str]],
                     frame_index: int, sector: int) -> list[DetectionResult]:
    """Run several short prompts rather than one long one.
    """
    return [detector.detect(image_path, g, frame_index, sector)
            for g in query_groups]
