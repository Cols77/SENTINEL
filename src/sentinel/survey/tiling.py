"""Small objects: detect on the full frame and on enlarged crops of it.

The 2026-09-22 demo flight saw the phone at the foot of the doors in 3 of the 5 frames it was in;
from the two far corners it was ~15 px wide and the full-frame answer had no box for it. A
``TiledDetector`` asks the same inner detector about the whole frame and about each cell of a grid
of overlapping crops, each crop enlarged to the full frame size before it is sent, so a small
object covers several times more pixels.

Merging is built around the survey's counting rule: two sightings of one class in one frame are
two objects (``merge`` only collapses in-frame pairs at IoU >= 0.5). So a crop can only ADD what
the full frame missed:

- every full-frame box is kept as it came;
- a crop box is added only if no full-frame box of its class overlaps it at all;
- crop boxes found by several overlapping crops are one object if they overlap enough;
- a crop box of a large class (person, computer) touching a crop edge that is not a frame edge is
  a fragment of something the crop cut, and is dropped.

A crop box keeps its own band (a zoomed crop is where a VLM hallucinates; nothing is upgraded).
Which crop each added box came from is recorded in ``raw["tile_only"]``, since a Detection
document has a fixed set of fields. A crop whose call fails is skipped. If the full-frame call
fails the frame fails (PROVIDER_ERROR, crop answers kept in raw): crops only add to a good
full-frame answer, never replace it, since dropping cut fragments would otherwise turn a person
spanning two crops into a silent "OK, 0 persons".
"""
from __future__ import annotations

import dataclasses
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from .detector import DetectionBatch
from .observability import IMAGE_HEIGHT, IMAGE_WIDTH

#: Classes too big to be seen whole in a crop: a box of these touching an inner crop edge is a cut.
LARGE_CLASSES = frozenset({"person", "computer"})
#: Share of the smaller box inside the larger for two crop boxes to be the same object.
SAME_OBJECT_CONTAINMENT = 0.6
#: A box within this many pixels of an inner crop edge touches it.
EDGE_PX = 4


@dataclasses.dataclass(frozen=True)
class Tile:
    name: str
    x0: int
    y0: int
    width: int
    height: int


def tiles_for(width: int, height: int, rows: int = 2, cols: int = 2,
              overlap: float = 0.15) -> list[Tile]:
    """A rows x cols grid of crops, each overlapping its neighbours by ``overlap`` of its size.
    Every crop has the frame's aspect ratio, so enlarging it to the frame size is uniform."""
    tile_w = round(width / (cols - (cols - 1) * overlap))
    tile_h = round(height / (rows - (rows - 1) * overlap))
    out = []
    for r in range(rows):
        for c in range(cols):
            x0 = 0 if cols == 1 else round(c * (width - tile_w) / (cols - 1))
            y0 = 0 if rows == 1 else round(r * (height - tile_h) / (rows - 1))
            out.append(Tile(f"r{r}c{c}", x0, y0, tile_w, tile_h))
    return out


def _area(box: dict) -> float:
    return max(0, box["width"]) * max(0, box["height"])


def _intersection(a: dict, b: dict) -> float:
    w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    return max(0, w) * max(0, h)


def _containment(a: dict, b: dict) -> float:
    smaller = min(_area(a), _area(b))
    return _intersection(a, b) / smaller if smaller else 0.0


def _to_frame(box: dict, tile: Tile, sent_w: int, sent_h: int) -> dict:
    sx, sy = tile.width / sent_w, tile.height / sent_h
    return {"x": round(tile.x0 + box["x"] * sx), "y": round(tile.y0 + box["y"] * sy),
            "width": max(1, round(box["width"] * sx)), "height": max(1, round(box["height"] * sy))}


def _touches_inner_edge(box: dict, tile: Tile, width: int, height: int) -> bool:
    left = tile.x0 > 0 and box["x"] <= tile.x0 + EDGE_PX
    top = tile.y0 > 0 and box["y"] <= tile.y0 + EDGE_PX
    right = (tile.x0 + tile.width < width
             and box["x"] + box["width"] >= tile.x0 + tile.width - EDGE_PX)
    bottom = (tile.y0 + tile.height < height
              and box["y"] + box["height"] >= tile.y0 + tile.height - EDGE_PX)
    return left or top or right or bottom


class TiledDetector:
    """Implements ``DetectorPort`` around any inner detector (5 calls per frame for 2x2)."""

    def __init__(self, inner, *, rows: int = 2, cols: int = 2, overlap: float = 0.15,
                 max_workers: int = 5, workdir: Path | None = None) -> None:
        self.inner = inner
        self.rows, self.cols, self.overlap = rows, cols, overlap
        self.max_workers = max_workers
        self.workdir = workdir

    def _crop(self, image, tile: Tile, folder: Path, stem: str) -> str:
        crop = image[tile.y0:tile.y0 + tile.height, tile.x0:tile.x0 + tile.width]
        crop = cv2.resize(crop, (IMAGE_WIDTH, IMAGE_HEIGHT), interpolation=cv2.INTER_CUBIC)
        path = folder / f"{stem}-{tile.name}.png"
        cv2.imwrite(str(path), crop)
        return str(path)

    def _safe(self, path: str, classes) -> DetectionBatch | Exception:
        try:
            return self.inner.detect(path, classes)
        except Exception as error:  # noqa: BLE001 -- one failed crop must not fail the frame
            return error

    def detect(self, image_path: str, classes: tuple[str, ...]) -> DetectionBatch:
        image = cv2.imread(str(image_path))
        if image is None:
            return self.inner.detect(image_path, classes)
        height, width = image.shape[:2]
        tiles = tiles_for(width, height, self.rows, self.cols, self.overlap)
        with tempfile.TemporaryDirectory(dir=self.workdir) as tmp:
            stem = Path(image_path).stem
            paths = [self._crop(image, tile, Path(tmp), stem) for tile in tiles]
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                full_future = pool.submit(self._safe, str(image_path), classes)
                tile_futures = [pool.submit(self._safe, p, classes) for p in paths]
                full = full_future.result()
                answers = [f.result() for f in tile_futures]
        return self._merge(full, tiles, answers, width, height)

    def _merge(self, full, tiles, answers, width: int, height: int) -> DetectionBatch:
        raw = {"full": full.raw if isinstance(full, DetectionBatch) else {"error": repr(full)},
               "tiles": {}, "tile_only": []}
        full_ok = isinstance(full, DetectionBatch) and full.status != "PROVIDER_ERROR"
        kept = list(zip(full.detections, full.bands)) if full_ok else []
        # the full frame's unmapped labels only: the same label seen by five calls is one miss
        dropped = list(full.dropped) if full_ok else []
        added: list[tuple[dict, str, str]] = []
        for tile, answer in zip(tiles, answers):
            if not isinstance(answer, DetectionBatch):
                raw["tiles"][tile.name] = {"error": repr(answer)}
                continue
            raw["tiles"][tile.name] = answer.raw
            if answer.status == "PROVIDER_ERROR":
                continue
            for detection, band in zip(answer.detections, answer.bands):
                if detection.get("bbox") is None:
                    continue
                box = _to_frame(detection["bbox"], tile, IMAGE_WIDTH, IMAGE_HEIGHT)
                cls = detection["class"]
                if cls in LARGE_CLASSES and _touches_inner_edge(box, tile, width, height):
                    continue
                if any(d["class"] == cls and d.get("bbox") and _intersection(d["bbox"], box) > 0
                       for d, _ in kept):
                    continue
                twin = next((i for i, (d, _, _) in enumerate(added)
                             if d["class"] == cls
                             and _containment(d["bbox"], box) >= SAME_OBJECT_CONTAINMENT), None)
                moved = {**detection, "bbox": box}
                if twin is None:
                    added.append((moved, band, tile.name))
                elif _area(box) < _area(added[twin][0]["bbox"]):
                    added[twin] = (moved, band, tile.name)     # the tighter view of one object
        if not full_ok:
            return DetectionBatch((), "PROVIDER_ERROR", raw=raw)
        for detection, band, tile_name in added:
            kept.append((detection, band))
            raw["tile_only"].append({"index": len(kept) - 1, "tile": tile_name,
                                     "class": detection["class"]})
        status = full.status if full_ok else "OK"
        return DetectionBatch(tuple(d for d, _ in kept), status, tuple(b for _, b in kept),
                              tuple(dropped), raw)
