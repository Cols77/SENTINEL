from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: int
    query: str
    sector: int
    observations: list = field(default_factory=list)   # (frame, box, score)
    misses: int = 0

    @property
    def best(self):
        return max(self.observations, key=lambda o: o[2])

    @property
    def peak_score(self) -> float:
        return float(self.best[2])

    @property
    def mean_score(self) -> float:
        return float(np.mean([o[2] for o in self.observations]))

    @property
    def n_obs(self) -> int:
        return len(self.observations)

    def box_at(self, frame: int):
        """Box for rendering: exact if observed, else the most recent one.

        Detection runs on a stride, so most frames have no observation. Holding
        the last box keeps the overlay stable instead of flickering, but it is
        interpolation, not measurement -- render.py marks held frames so the
        video never implies a detection that did not happen.
        """
        exact = [o for o in self.observations if o[0] == frame]
        if exact:
            return exact[0][1], True
        prior = [o for o in self.observations if o[0] <= frame]
        return (prior[-1][1], False) if prior else (None, False)


class SectorTracker:
    """Hungarian assignment within a dwell, reset at each sector boundary.

    Within a dwell the airframe is near-static, so IoU association is reliable
    and no camera-motion compensation is needed. Across a rotation the view
    changes entirely, so the tracker resets rather than being asked to solve a
    re-identification problem it cannot.
    """

    def __init__(self, iou_thresh: float = 0.3, max_misses: int = 3):
        self.iou_thresh = iou_thresh
        self.max_misses = max_misses
        self.tracks: list[Track] = []
        self._active: list[Track] = []
        self._next_id = 0

    def new_sector(self) -> None:
        self._active = []

    def update(self, detections: list, frame: int, sector: int) -> None:
        if not detections:
            for t in self._active:
                t.misses += 1
            self._active = [t for t in self._active if t.misses <= self.max_misses]
            return

        if not self._active:
            for d in detections:
                self._spawn(d, frame, sector)
            return

        cost = np.ones((len(self._active), len(detections)), dtype=np.float32)
        for i, t in enumerate(self._active):
            last_box = t.observations[-1][1]
            for j, d in enumerate(detections):
                if t.query == d["query"]:
                    cost[i, j] = 1.0 - iou(last_box, d["box_xyxy"])

        rows, cols = linear_sum_assignment(cost)
        matched_t, matched_d = set(), set()
        for r, c in zip(rows, cols):
            if cost[r, c] <= 1.0 - self.iou_thresh:
                d = detections[c]
                self._active[r].observations.append((frame, d["box_xyxy"], d["score"]))
                self._active[r].misses = 0
                matched_t.add(r)
                matched_d.add(c)

        for i, t in enumerate(self._active):
            if i not in matched_t:
                t.misses += 1
        for j, d in enumerate(detections):
            if j not in matched_d:
                self._spawn(d, frame, sector)

        self._active = [t for t in self._active if t.misses <= self.max_misses]

    def _spawn(self, d, frame, sector) -> None:
        t = Track(track_id=self._next_id, query=d["query"], sector=sector)
        t.observations.append((frame, d["box_xyxy"], d["score"]))
        self._next_id += 1
        self.tracks.append(t)
        self._active.append(t)

    def active_at(self, frame: int) -> list[Track]:
        return [t for t in self._active
                if t.observations and t.observations[-1][0] >= frame - 30]

def merge_tracks(tracks: list[Track], iou_thresh: float = 0.45) -> list[Track]:
    """Merge tracks within a sector that describe the same object.

    Per-frame NMS cannot catch this: the duplicate boxes are in different
    frames, so they never meet. A detector whose box wanders between frames
    drops IoU below the association threshold and spawns a second track for
    an object that never moved.
    """
    by_group: dict[tuple, list[Track]] = {}
    for t in tracks:
        by_group.setdefault((t.sector, t.query), []).append(t)

    out: list[Track] = []
    for group in by_group.values():
        group.sort(key=lambda t: -t.peak_score)
        kept: list[Track] = []
        for t in group:
            merged_into = None
            for k in kept:
                if iou(t.best[1], k.best[1]) >= iou_thresh:
                    merged_into = k
                    break
            if merged_into is None:
                kept.append(t)
            else:
                merged_into.observations.extend(t.observations)
                merged_into.observations.sort(key=lambda o: o[0])
        out.extend(kept)
    return out