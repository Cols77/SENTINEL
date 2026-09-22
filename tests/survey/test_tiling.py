"""TiledDetector: crops find what the full frame missed, and never double-count."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from sentinel.survey.detector import DetectionBatch
from sentinel.survey.tiling import TiledDetector, tiles_for


def _det(cls, x, y, w, h):
    return {"class": cls, "matched_prompt": cls, "raw_score": None,
            "score_kind": "VLM_CONFIDENCE_BAND", "bbox": {"x": x, "y": y, "width": w, "height": h},
            "bbox_coordinate_space": "PIXELS_TOP_LEFT", "description": None,
            "provider_item_id": None}


class Scripted:
    """Answers by which crop it is sent: 'full' or a tile name such as 'r1c0'."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def detect(self, image_path, classes):
        stem = Path(image_path).stem
        key = stem.rsplit("-", 1)[1] if "-" in stem else "full"
        self.calls.append(key)
        answer = self.answers.get(key, [])
        if isinstance(answer, Exception):
            raise answer
        if answer == "FAIL":
            return DetectionBatch((), "PROVIDER_ERROR")
        return DetectionBatch(tuple(d for d, _ in answer), "OK", tuple(b for _, b in answer),
                              raw={"key": key})


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "frame.png"
    cv2.imwrite(str(path), np.zeros((720, 960, 3), dtype=np.uint8))
    return str(path)


def _tile(name):
    return next(t for t in tiles_for(960, 720) if t.name == name)


def test_the_grid_covers_the_frame_with_overlapping_crops_of_its_shape():
    tiles = tiles_for(960, 720)
    assert [t.name for t in tiles] == ["r0c0", "r0c1", "r1c0", "r1c1"]
    assert all(abs(t.width / t.height - 960 / 720) < 0.01 for t in tiles)
    assert tiles[-1].x0 + tiles[-1].width == 960 and tiles[-1].y0 + tiles[-1].height == 720
    assert tiles[1].x0 < tiles[0].width                       # neighbours overlap


def test_a_small_object_only_a_crop_sees_is_added_in_frame_pixels(frame):
    inner = Scripted({"r1c1": [(_det("phone", 480, 600, 40, 40), "medium")]})
    batch = TiledDetector(inner).detect(frame, ("phone",))
    t = _tile("r1c1")
    scale = t.width / 960
    (phone,) = batch.detections
    assert phone["bbox"] == {"x": round(t.x0 + 480 * scale), "y": round(t.y0 + 600 * scale),
                             "width": round(40 * scale), "height": round(40 * scale)}
    assert batch.bands == ("medium",) and batch.status == "OK"
    assert batch.raw["tile_only"] == [{"index": 0, "tile": "r1c1", "class": "phone"}]
    assert sorted(inner.calls) == ["full", "r0c0", "r0c1", "r1c0", "r1c1"]


def test_an_object_the_full_frame_found_is_not_counted_again_from_a_crop(frame):
    t = _tile("r1c1")
    # the same phone: full frame at (828, 667); the crop sees it at the matching enlarged spot
    in_crop = ((828 - t.x0) * 960 / t.width, (667 - t.y0) * 960 / t.width)
    inner = Scripted({"full": [(_det("phone", 828, 667, 36, 45), "high")],
                      "r1c1": [(_det("phone", round(in_crop[0]), round(in_crop[1]), 60, 70), "high")]})
    batch = TiledDetector(inner).detect(frame, ("phone",))
    assert len(batch.detections) == 1 and batch.raw["tile_only"] == []


def test_one_object_seen_by_two_overlapping_crops_is_one_object(frame):
    a, b = _tile("r1c0"), _tile("r1c1")
    x_frame, y_frame = b.x0 + 10, 650                  # inside both crops' overlap
    to_crop = lambda t: (round((x_frame - t.x0) * 960 / t.width), round((y_frame - t.y0) * 960 / t.width))
    inner = Scripted({"r1c0": [(_det("phone", *to_crop(a), 30, 30), "high")],
                      "r1c1": [(_det("phone", *to_crop(b), 30, 30), "high")]})
    batch = TiledDetector(inner).detect(frame, ("phone",))
    assert [d["class"] for d in batch.detections] == ["phone"]


def test_a_person_cut_by_a_crop_edge_is_a_fragment_not_a_second_person(frame):
    # full frame has the person; the crop r0c1 shows a slice of them against its left edge
    inner = Scripted({"full": [(_det("person", 376, 144, 219, 576), "high")],
                      "r0c0": [(_det("person", 700, 300, 260, 420), "high")],   # touches right edge
                      "r1c1": [(_det("person", 0, 100, 120, 500), "high")]})    # touches left edge
    batch = TiledDetector(inner).detect(frame, ("person",))
    assert [d["class"] for d in batch.detections] == ["person"]


def test_a_failed_crop_is_skipped_and_the_frame_still_answers(frame):
    inner = Scripted({"full": [(_det("phone", 828, 667, 36, 45), "high")],
                      "r0c0": RuntimeError("429"), "r0c1": "FAIL"})
    batch = TiledDetector(inner).detect(frame, ("phone",))
    assert batch.status == "OK" and len(batch.detections) == 1
    assert "error" in batch.raw["tiles"]["r0c0"]


def test_crops_still_answer_when_the_full_frame_call_failed(frame):
    inner = Scripted({"full": "FAIL", "r1c1": [(_det("phone", 480, 600, 40, 40), "low")]})
    batch = TiledDetector(inner).detect(frame, ("phone",))
    assert batch.status == "OK" and batch.bands == ("low",)


def test_the_frame_fails_only_when_every_call_failed(frame):
    inner = Scripted({k: "FAIL" for k in ("full", "r0c0", "r0c1", "r1c0", "r1c1")})
    assert TiledDetector(inner).detect(frame, ("phone",)).status == "PROVIDER_ERROR"


def test_crops_are_sent_enlarged_to_the_frame_size(frame, tmp_path):
    sizes = []

    class Measuring(Scripted):
        def detect(self, image_path, classes):
            sizes.append(cv2.imread(image_path).shape[:2])
            return super().detect(image_path, classes)

    TiledDetector(Measuring({})).detect(frame, ("phone",))
    assert sizes and all(size == (720, 960) for size in sizes)
