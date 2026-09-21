# WP5 Cube Navigation and Positioning Implementation Plan (for spec rev 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Host binding (operator preference): on Claude Code run it through the `Workflow` tool as dev → review → fix per task, on Sonnet; physical gates stay with the operator.

**Goal:** Fly the Tello through a mission of stops (position, height, look directions) inside an operating cube derived from a line of tag stands, with pose switchable per run between `off`, `passive` (measure, log, containment) and `reanchor` (correct at a hover).

**Architecture:** Part A hardens the pose stack (joint multi-tag PnP, burst localisation, containment, map check) and adds a single site file that generates the tag map, the flight envelope and the containment footprint, plus a Monte Carlo layout check. Part B adds a mission runner that subclasses the existing `FlightTestRunner`, so preflight, emergency latch and abort policy are inherited. The drone translates only while facing +y, which makes the existing `Position`/`FlightEnvelope` semantics (forward = +y, right = +x) correct in the room frame.

**Tech Stack:** Python 3, OpenCV (`cv2.aruco`, `solvePnP` SQPNP + ITERATIVE), NumPy, SQLite, pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-20-wp5-cube-positioning-design.md` (rev 3)

## Global Constraints

- Work only in the worktree `C:\coding\sentinel\.worktrees\wp5-drone-pose`, branch `feat/sentinel-wp5-drone-pose`. **Never push, never merge.**
- Every commit message ends with `Co-Authored-By: <the model actually running> <noreply@anthropic.com>`.
- Test command: `uv run python -m pytest tests -q`. Baseline **220 passed**; never fewer, never a failure. "Expected" counts below mean baseline + the new tests; zero failures is the real criterion.
- No change to `docs/superpowers/specs/contracts/*` or `src/sentinel/contracts/pose.py`.
- SR-063: `src/sentinel/runner.py` must never import from `sentinel.pose`. Pose may influence motion **only** inside `CubeMissionRunner` in `reanchor` mode. `requirements/SR-063.md` is edited only in Task 12, only on the operator's recorded consent.
- Flight commands: `takeoff`, `move` (20–300 cm), `rotate`, `stop`, `land`. No `go`, `curve`, RC.
- Room frame: 1 unit = 10 cm; heading 0° = +x, counter-clockwise positive; the drone faces +y (heading 90°) whenever it translates; tag `z_units` is **metres**; stand tags face −y (`yaw_deg = -90`).
- Reprojection gate 2.0 px (`MAX_REPROJECTION_ERROR_PX`), per tag and for the joint solve.
- Shell note for executors on this Windows host: bash heredocs containing apostrophes have failed here; create files with the file-write tool, not heredocs.

## File Structure

| File | Responsibility |
|---|---|
| `src/sentinel/pose/detector.py` (modify) | drop-not-fail gating; joint PnP; `joint=` switch |
| `src/sentinel/pose/scoring.py` (modify) | export `circular_median_deg` |
| `src/sentinel/pose/localise.py` (new) | burst of reports → fix / unreliable |
| `src/sentinel/pose/containment.py` (new) | SR-063 AC-2 advisory check |
| `src/sentinel/pose/mapcheck.py` (new) | single-vs-joint spread + residual per stand on recorded frames |
| `src/sentinel/pose/layout.py` (new) | predicted-error grid (Monte Carlo) |
| `src/sentinel/cube/site.py` (new) | `cube-site-v1` → tag map, envelope, footprint |
| `src/sentinel/cube/mission.py` (new) | mission file + `CubeMissionRunner` (3 pose modes) |
| `src/sentinel/cube/observe.py` (new) | observation candidates |
| `src/sentinel/runner.py` (modify) | injectable envelope; overridable `_fly_sequence` |
| `src/sentinel/cli.py` (modify) | `pose-map-check`, `cube-site-map`, `cube-layout-check`, `cube-run` |
| `tests/pose/rendering.py` (new) | shared synthetic multi-tag frame renderer |

A shared test fixture used by several tasks — copy it verbatim wherever a task says **SITE_DOC**:

```python
SITE_DOC = {
    "schema_version": "cube-site-v1",
    "line_y_m": 5.0,
    "stands": [{"x_m": x, "first_tag_id": 2 * i, "heights_m": [0.90, 1.45], "size_m": 0.192}
               for i, x in enumerate([0.25, 0.75, 2.3, 2.7, 3.1, 4.65, 5.15])],
    "cube": {"min_x_m": 0.3, "max_x_m": 5.1, "min_y_m": 2.0, "max_y_m": 4.2, "max_z_m": 2.0},
    "commanded": {"inset_m": 0.3, "min_z_cm": 80, "max_z_cm": 180},
}
CALIBRATION = {"width": 960, "height": 720, "fx": 893.0, "fy": 894.0, "cx": 468.0,
               "cy": 354.0, "dist_coeffs": [], "calibration_source": "chessboard"}
```

---

# Part A — pose stack and site tooling (no approvals needed)

### Task 1: Joint multi-tag PnP in the detector

The implementation in Step 4 was spiked against the full suite on 2026-09-20 (220 green, truth recovered to 1.7 cm, wrong-map fallback works). Transcribe it exactly.

**Files:**
- Create: `tests/pose/rendering.py`, `tests/pose/test_joint_pnp.py`
- Modify: `src/sentinel/pose/detector.py`

**Interfaces:**
- Produces: `estimate_pose(frame, tag_map, *, run_id, report_id, source_kind="SIMULATED", joint=True) -> DronePoseReport`. With ≥ 2 solved tags and `joint=True`, `tag_observations` lists every tag used and `reprojection_error_px` is the joint error. `_solve_mapped_tags` returns 5-tuples `(err, tag, rvec, tvec, corners_4x2)`. `_room_corners(tag, scale) -> np.ndarray (4×3, room metres)` is reused by Task 6.
- Test helper `tests/pose/rendering.py`: `K, DIST, SIZE_M, SCALE, make_doc(tags), render(tags, cam_pos_m, heading_deg)`.

- [ ] **Step 1: Write the shared renderer**

```python
# tests/pose/rendering.py
"""Synthetic multi-tag frames for detector tests (level camera, no distortion)."""
import math

import cv2
import numpy as np

K = np.array([[547.0, 0, 480.0], [0, 547.0, 360.0], [0, 0, 1]])
DIST = np.zeros((0,), dtype=np.float64)
SIZE_M = 0.16
SCALE = 0.1

_OBJ = np.array([[-SIZE_M / 2, SIZE_M / 2, 0], [SIZE_M / 2, SIZE_M / 2, 0],
                 [SIZE_M / 2, -SIZE_M / 2, 0], [-SIZE_M / 2, -SIZE_M / 2, 0]], dtype=np.float64)

# A splayed pair around (0.5 m, 4.0 m): normals at +25 and -25 degrees. Verified by the spike.
SPLAYED_PAIR = [
    {"tag_id": 0, "zone_id": "PAIR", "x_units": 4.4083, "y_units": 41.2688, "z_units": 1.0,
     "yaw_deg": 25.0, "size_m": SIZE_M, "mounting": "WALL"},
    {"tag_id": 1, "zone_id": "PAIR", "x_units": 4.4083, "y_units": 38.7312, "z_units": 1.0,
     "yaw_deg": -25.0, "size_m": SIZE_M, "mounting": "WALL"},
]


def make_doc(tags: list[dict]) -> dict:
    return {"schema_version": "tag-map-v1", "tag_family": "36h11",
            "room_scale_m_per_unit": SCALE,
            "room": {"width_units": 100, "height_units": 100, "origin": "LEFT_ALLEY_FRONT_CORNER"},
            "tags": tags,
            "camera_calibration": {"width": 960, "height": 720, "fx": 547.0, "fy": 547.0,
                                   "cx": 480.0, "cy": 360.0, "dist_coeffs": [],
                                   "calibration_source": "nominal"}}


def render(tags: list[dict], cam_pos_m, heading_deg: float) -> np.ndarray:
    """Render every tag as seen by a level camera at ``cam_pos_m`` facing ``heading_deg``."""
    h = math.radians(heading_deg)
    fwd = np.array([math.cos(h), math.sin(h), 0.0])
    x_cam = np.cross(fwd, [0.0, 0.0, 1.0])
    R_room_cam = np.column_stack([x_cam, [0.0, 0.0, -1.0], fwd])
    frame = np.full((720, 960, 3), 255, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    for tag in tags:
        c, s = math.cos(math.radians(tag["yaw_deg"])), math.sin(math.radians(tag["yaw_deg"]))
        # rendering tag frame: x right when facing the tag, y up, z into the wall
        R_room_tag = np.column_stack([[s, -c, 0.0], [0.0, 0.0, 1.0], [-c, -s, 0.0]])
        tag_pos = np.array([tag["x_units"] * SCALE, tag["y_units"] * SCALE, tag["z_units"]])
        tvec = R_room_cam.T @ (tag_pos - np.asarray(cam_pos_m, dtype=float))
        rvec, _ = cv2.Rodrigues(R_room_cam.T @ R_room_tag)
        pts, _ = cv2.projectPoints(_OBJ, rvec, tvec.reshape(3, 1), K, DIST)
        pts = pts.reshape(-1, 2).astype(np.float32)
        marker = cv2.aruco.generateImageMarker(dictionary, tag["tag_id"], 200)
        mh, mw = marker.shape[:2]
        src = np.array([[0, mh], [mw, mh], [mw, 0], [0, 0]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(src, pts)
        warped = cv2.warpPerspective(marker, M, (960, 720), flags=cv2.INTER_NEAREST,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        mask = cv2.warpPerspective(np.full_like(marker, 255), M, (960, 720),
                                   flags=cv2.INTER_NEAREST)
        frame[mask == 255] = warped[mask == 255][:, None]
    return frame
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/pose/test_joint_pnp.py
"""Joint multi-tag PnP."""
import math

import numpy as np
import pytest

from sentinel.pose import detector
from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import TagMap
from tests.pose.rendering import SCALE, SPLAYED_PAIR, make_doc, render


def _scene(range_m=1.5, off_axis_deg=20.0):
    a = math.radians(off_axis_deg)
    cam = np.array([0.5 + range_m * math.cos(a), 4.0 + range_m * math.sin(a), 1.0])
    heading = math.degrees(math.atan2(4.0 - cam[1], 0.5 - cam[0]))
    return cam, heading, render(SPLAYED_PAIR, cam, heading)


def _error_units(report, cam):
    return math.hypot(report.point[0] - cam[0] / SCALE, report.point[1] - cam[1] / SCALE)


def test_joint_solve_uses_both_tags_and_recovers_truth():
    cam, heading, frame = _scene()
    report = estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p")
    assert sorted(o.tag_id for o in report.tag_observations) == [0, 1]
    assert _error_units(report, cam) <= 0.5          # 5 cm; the spike measured 1.7 cm
    assert abs((report.heading_deg - heading + 180.0) % 360.0 - 180.0) <= 2.0
    assert report.reprojection_error_px <= 2.0
    assert report.method == "APRILTAG_36H11_PNP"


def test_joint_false_keeps_the_single_tag_path():
    cam, _, frame = _scene()
    report = estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p",
                           joint=False)
    assert len(report.tag_observations) == 1
    assert _error_units(report, cam) <= 1.0


def test_joint_gate_falls_back_to_the_best_single_tag_on_a_wrong_map():
    _, _, frame = _scene()
    wrong = [dict(SPLAYED_PAIR[0]),
             dict(SPLAYED_PAIR[1], y_units=SPLAYED_PAIR[1]["y_units"] - 3.0)]  # 30 cm map error
    report = estimate_pose(frame, TagMap(make_doc(wrong)), run_id="r", report_id="p")
    assert len(report.tag_observations) == 1
    assert report.reprojection_error_px <= 2.0


def test_a_tag_that_fails_its_gate_is_dropped_not_fatal(monkeypatch):
    _, _, frame = _scene()
    real = detector._solve_one
    monkeypatch.setattr(detector, "_solve_one",
                        lambda tag, corners, tm: None if tag.tag_id == 1 else real(tag, corners, tm))
    report = estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p")
    assert [o.tag_id for o in report.tag_observations] == [0]


def test_every_tag_dropped_fails_closed(monkeypatch):
    _, _, frame = _scene()
    monkeypatch.setattr(detector, "_solve_one", lambda tag, corners, tm: None)
    with pytest.raises(ValueError, match="no mapped tag passed"):
        estimate_pose(frame, TagMap(make_doc(SPLAYED_PAIR)), run_id="r", report_id="p")
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run python -m pytest tests/pose/test_joint_pnp.py -q`
Expected: FAIL — `TypeError: estimate_pose() got an unexpected keyword argument 'joint'`, `AttributeError: ... '_solve_one'`.

- [ ] **Step 4: Implement in `src/sentinel/pose/detector.py`**

4a. Replace the import block and module constant at the top of the file:

```python
"""AprilTag 36h11 detector + single-tag and joint multi-tag PnP pose estimation (fail-closed)."""
import itertools
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import cv2
import numpy as np

from sentinel.contracts.pose import (
    MAX_REPROJECTION_ERROR_PX, DronePoseReport, SourceKind, TagObservation,
)
from sentinel.pose.tagmap import TagMap

logger = logging.getLogger(__name__)

ROOM_SCALE_M_PER_UNIT = 0.1  # metres per room unit
```

4b. Replace the whole `_solve_mapped_tags` function with these five functions:

```python
def _reprojection_error(obj, image, rvec, tvec, tag_map: TagMap) -> float:
    proj, _ = cv2.projectPoints(obj, rvec, tvec, tag_map.K, tag_map.dist)
    return float(np.mean(np.linalg.norm(proj.reshape(-1, 2) - image.reshape(-1, 2), axis=1)))


def _solve_one(tag, corners, tag_map: TagMap):
    """PnP-solve one tag; ``(err, rvec, tvec)`` or ``None`` when it fails its gates."""
    obj = _tag_object_points(tag.size_m)
    image = corners.astype(np.float64)
    ok, rvs, tvs, _ = cv2.solvePnPGeneric(
        obj, image, tag_map.K, tag_map.dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    if not ok:
        return None
    # IPPE_SQUARE has a two-fold planar ambiguity; on frontal tags OpenCV 5.0
    # can return a front/back solution whose reprojection does not match the
    # detected corners. Pick the best candidate by actual reprojection error,
    # falling back to ITERATIVE when every candidate exceeds the gate.
    best = None
    for rvec_c, tvec_c in zip(rvs, tvs):
        err_c = _reprojection_error(obj, image, rvec_c, tvec_c, tag_map)
        if best is None or err_c < best[0]:
            best = (err_c, rvec_c, tvec_c)
    if best[0] > MAX_REPROJECTION_ERROR_PX:
        ok, rvec_i, tvec_i = cv2.solvePnP(obj, image, tag_map.K, tag_map.dist)
        if ok:
            err_i = _reprojection_error(obj, image, rvec_i, tvec_i, tag_map)
            if err_i < best[0]:
                best = (err_i, rvec_i, tvec_i)
    if best[0] > MAX_REPROJECTION_ERROR_PX:
        return None
    return best


def _solve_mapped_tags(frame, tag_map: TagMap):
    """Detect tags and PnP-solve every mapped one.

    Returns ``[(err, tag, rvec, tvec, corners), ...]`` in detection order,
    ``corners`` being the 4x2 image points of that tag. A mapped tag that fails
    PnP or the reprojection gate is dropped. Fail-closed: raises when no tags
    are detected, only unknown tags are detected, or every mapped tag is dropped.
    """
    aruco = cv2.aruco
    detector = aruco.ArucoDetector(
        aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11),
        aruco.DetectorParameters(),
    )
    corners, ids, _ = detector.detectMarkers(frame)
    if ids is None or len(ids) == 0:
        raise NoTagDetected("no AprilTag markers detected: pose fails closed")

    mapped = [i for i, mid in enumerate(ids.flatten()) if tag_map.has_tag(int(mid))]
    if not mapped:
        unknown = sorted(set(int(i) for i in ids.flatten()))
        raise ValueError(f"only unmapped tag(s) detected {unknown}: pose fails closed")

    solved = []
    for i in mapped:
        tag = tag_map.tag(int(ids.flatten()[i]))
        one = _solve_one(tag, corners[i], tag_map)
        if one is None:
            logger.debug("tag %d dropped: PnP failed or reprojection gate", tag.tag_id)
            continue
        err, rvec, tvec = one
        solved.append((err, tag, rvec, tvec, corners[i].reshape(-1, 2)))
    if not solved:
        raise ValueError("no mapped tag passed the PnP gates: pose fails closed")
    return solved


def _room_corners(tag, scale: float) -> np.ndarray:
    """Four tag corners in room metres (same frame composition as _report_point)."""
    yaw = np.deg2rad(tag.yaw_deg)
    R_room_tag = np.array([
        [np.sin(yaw), 0.0, np.cos(yaw)],
        [-np.cos(yaw), 0.0, np.sin(yaw)],
        [0.0, -1.0, 0.0],
    ])
    tag_pos = np.array([tag.x_units, tag.y_units, 0.0]) * scale + np.array([0.0, 0.0, tag.z_units])
    return (R_room_tag @ _tag_object_points(tag.size_m).T).T + tag_pos


def _solve_joint(solved, tag_map: TagMap):
    """One PnP over the corners of every solved tag, in the room frame.

    Returns ``(err, point, heading, distances)`` or ``None`` when the solve
    fails or its reprojection error exceeds the gate (a tag-map error symptom).
    """
    scale = tag_map.room_scale_m_per_unit
    obj = np.vstack([_room_corners(tag, scale) for _, tag, _, _, _ in solved])
    image = np.vstack([pts for _, _, _, _, pts in solved]).astype(np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, image, tag_map.K, tag_map.dist, flags=cv2.SOLVEPNP_SQPNP)
    if not ok:
        return None
    ok, rvec, tvec = cv2.solvePnP(
        obj, image, tag_map.K, tag_map.dist, rvec=rvec, tvec=tvec,
        useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        return None
    err = _reprojection_error(obj, image, rvec, tvec, tag_map)
    if err > MAX_REPROJECTION_ERROR_PX:
        return None
    R_cam_room, _ = cv2.Rodrigues(rvec)
    cam_pos = (-R_cam_room.T @ tvec).flatten()
    fwd = R_cam_room.T[:, 2]  # camera +z axis in the room = viewing direction
    heading = float(np.degrees(np.arctan2(fwd[1], fwd[0])) % 360.0)
    point = (round(float(cam_pos[0] / scale), 3), round(float(cam_pos[1] / scale), 3))
    distances = [float(np.linalg.norm(_room_corners(tag, scale).mean(axis=0) - cam_pos))
                 for _, tag, _, _, _ in solved]
    return err, point, heading, distances
```

4c. In `estimate_all`, change the loop header to unpack five values:

```python
    for err, tag, rvec, tvec, _ in _solve_mapped_tags(frame, tag_map):
```

4d. Replace `estimate_pose` entirely:

```python
def estimate_pose(
    frame,
    tag_map: TagMap,
    *,
    run_id: str,
    report_id: str,
    source_kind: SourceKind = "SIMULATED",
    joint: bool = True,
) -> DronePoseReport:
    """Detect AprilTag 36h11 tags, PnP-solve the mapped ones, emit one report.

    With two or more solved tags the pose comes from one joint PnP over all of
    their corners (``joint=False`` forces the single best tag). Fail-closed:
    raises ValueError when no tags are detected, only unknown tags are
    detected, or every mapped tag fails its gates.
    """
    solved = _solve_mapped_tags(frame, tag_map)
    joint_solution = _solve_joint(solved, tag_map) if joint and len(solved) >= 2 else None
    if joint_solution is None:
        if joint and len(solved) >= 2:
            logger.warning("joint PnP rejected (tag-map residual?); using the best single tag")
        err, tag, rvec, tvec, _ = min(solved, key=lambda item: item[0])
        point, heading = _report_point(rvec, tvec, tag, tag_map.room_scale_m_per_unit)
        used = [(tag, float(np.linalg.norm(tvec)), err)]
    else:
        err, point, heading, distances = joint_solution
        used = [(tag, dist, tag_err) for (tag_err, tag, _, _, _), dist in zip(solved, distances)]
    return DronePoseReport(
        report_id=report_id,
        run_id=run_id,
        observed_at=datetime.now(timezone.utc),
        source_kind=source_kind,
        point=point,
        heading_deg=round(normalize_heading_deg(heading), 2),
        altitude_m=None,
        method="APRILTAG_36H11_PNP",
        tag_observations=tuple(TagObservation(
            tag_id=tag.tag_id,
            zone_id=tag.zone_id,
            distance_m=round(dist, 4),
            reprojection_error_px=round(tag_err, 4),
        ) for tag, dist, tag_err in used),
        reprojection_error_px=round(err, 4),
    )
```

- [ ] **Step 5: Run the whole suite** — `uv run python -m pytest tests -q` → Expected: 225 passed

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/pose/detector.py tests/pose/rendering.py tests/pose/test_joint_pnp.py
git commit -m "feat(pose): joint multi-tag PnP; a failed tag is dropped, not fatal" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Stationary localisation burst

**Files:**
- Modify: `src/sentinel/pose/scoring.py`
- Create: `src/sentinel/pose/localise.py`
- Test: `tests/pose/test_localise.py`

**Interfaces:**
- Produces: `circular_median_deg(values) -> float` in `[-180, 180)` (in `scoring.py`);
  `LocalisationFix(x_units, y_units, heading_deg, n, spread_p95_cm)`; `LocalisationUnreliable(reason, n)`;
  `localise(reports, *, min_samples=8, max_spread_cm=5.0, cm_per_unit=10.0) -> LocalisationFix | LocalisationUnreliable`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/pose/test_localise.py
from datetime import datetime, timezone

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.pose.localise import LocalisationFix, LocalisationUnreliable, localise
from sentinel.pose.scoring import circular_median_deg


def _report(x, y, heading):
    return DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(x, y), heading_deg=heading, altitude_m=None,
        method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(0, "STAND_0", 1.0, 0.5),), reprojection_error_px=0.5)


def test_circular_median_is_wrap_safe():
    assert abs(abs(circular_median_deg([179.0, -179.0, 178.5, -178.5])) - 180.0) <= 0.5
    assert circular_median_deg([10.0, 12.0, 11.0]) == 11.0


def test_fix_is_the_componentwise_median_with_spread():
    fix = localise([_report(15.0 + 0.01 * i, 12.0, 90.0) for i in range(9)])
    assert isinstance(fix, LocalisationFix)
    assert fix.n == 9 and abs(fix.x_units - 15.04) < 1e-9 and fix.y_units == 12.0
    assert fix.heading_deg == 90.0 and fix.spread_p95_cm < 1.0


def test_one_outlier_does_not_move_the_fix():
    reports = [_report(15.0, 12.0, 90.0) for _ in range(19)] + [_report(40.0, 12.0, 90.0)]
    fix = localise(reports)
    assert isinstance(fix, LocalisationFix) and fix.x_units == 15.0


def test_too_few_samples_is_unreliable():
    result = localise([_report(15.0, 12.0, 90.0)] * 7)
    assert isinstance(result, LocalisationUnreliable) and "samples" in result.reason


def test_scattered_burst_is_unreliable():
    result = localise([_report(15.0 + i, 12.0, 90.0) for i in range(10)])  # 10 cm steps
    assert isinstance(result, LocalisationUnreliable) and "spread" in result.reason
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.pose.localise`

- [ ] **Step 3: Implement**

In `src/sentinel/pose/scoring.py`, directly below `heading_delta_deg`:

```python
def circular_median_deg(values) -> float:
    """Median heading in [-180, 180), safe across the +/-180 wrap."""
    values = [float(v) for v in values]
    deltas = [heading_delta_deg(v, values[0]) for v in values]
    return (values[0] + statistics.median(deltas) + 180.0) % 360.0 - 180.0
```

In `score_runs`, replace the two statements that compute `heading_deltas_from_first` and `median_heading` with `median_heading = circular_median_deg(hs)`.

```python
# src/sentinel/pose/localise.py
"""Aggregate one stationary hover burst of pose reports into a single fix.

Pose is only trusted at rest: stream latency and gaps then cost time, never
accuracy. Pure functions, no I/O.
"""
import math
import statistics
from dataclasses import dataclass
from typing import Sequence

from sentinel.contracts.pose import DronePoseReport
from sentinel.pose.scoring import circular_median_deg


@dataclass(frozen=True)
class LocalisationFix:
    x_units: float
    y_units: float
    heading_deg: float
    n: int
    spread_p95_cm: float


@dataclass(frozen=True)
class LocalisationUnreliable:
    reason: str
    n: int


def localise(
    reports: Sequence[DronePoseReport],
    *,
    min_samples: int = 8,
    max_spread_cm: float = 5.0,
    cm_per_unit: float = 10.0,
) -> LocalisationFix | LocalisationUnreliable:
    n = len(reports)
    if n < min_samples:
        return LocalisationUnreliable(f"{n} samples, need {min_samples}", n)
    x = statistics.median(r.point[0] for r in reports)
    y = statistics.median(r.point[1] for r in reports)
    dist = sorted(math.hypot(r.point[0] - x, r.point[1] - y) for r in reports)
    spread_cm = dist[int(0.95 * (n - 1))] * cm_per_unit
    if spread_cm > max_spread_cm:
        return LocalisationUnreliable(
            f"spread p95 {spread_cm:.1f} cm exceeds {max_spread_cm:.1f} cm", n)
    heading = circular_median_deg([r.heading_deg for r in reports])
    return LocalisationFix(x, y, heading, n, spread_cm)
```

- [ ] **Step 4: Run and commit** — suite → Expected: 230 passed

```bash
git add src/sentinel/pose/scoring.py src/sentinel/pose/localise.py tests/pose/test_localise.py
git commit -m "feat(pose): stationary burst localisation with a spread gate" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Containment check (SR-063 AC-2)

**Files:**
- Create: `src/sentinel/pose/containment.py`
- Test: `tests/pose/test_containment.py` (the marker SR-063 AC-2 already names)

**Interfaces:**
- Produces: `Containment` enum (`INSIDE`, `MARGIN`, `OUTSIDE`); `CubeFootprint(min_x_cm, max_x_cm, min_y_cm, max_y_cm)`; `check_containment(x_cm, y_cm, footprint, *, margin_cm=30.0) -> Containment`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/pose/test_containment.py
"""SR-063 AC-2: advisory containment; it classifies, it never moves anything."""
import inspect

import pytest

from sentinel.pose import containment
from sentinel.pose.containment import Containment, CubeFootprint, check_containment

CUBE = CubeFootprint(30.0, 510.0, 200.0, 420.0)


@pytest.mark.parametrize("x, y, expected", [
    (270.0, 300.0, Containment.INSIDE),
    (60.0, 300.0, Containment.INSIDE),       # exactly on the margin line counts as inside
    (59.9, 300.0, Containment.MARGIN),
    (270.0, 400.0, Containment.MARGIN),
    (30.0, 200.0, Containment.MARGIN),       # on the boundary is still in the cube
    (29.9, 300.0, Containment.OUTSIDE),
    (270.0, 420.1, Containment.OUTSIDE),
])
def test_classification(x, y, expected):
    assert check_containment(x, y, CUBE) is expected


def test_custom_margin():
    assert check_containment(45.0, 300.0, CUBE, margin_cm=10.0) is Containment.INSIDE


def test_module_cannot_command_motion():
    source = inspect.getsource(containment)
    for forbidden in ("client", ".move(", ".rotate(", "takeoff", "sentinel.runner"):
        assert forbidden not in source
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/sentinel/pose/containment.py
"""Advisory cube containment (SR-063 AC-2).

Classifies an estimated position against the operating cube footprint. The
result informs the operator and the existing abort policy; nothing here can
issue or modify a motion command.
"""
from dataclasses import dataclass
from enum import Enum


class Containment(str, Enum):
    INSIDE = "INSIDE"
    MARGIN = "MARGIN"
    OUTSIDE = "OUTSIDE"


@dataclass(frozen=True)
class CubeFootprint:
    min_x_cm: float
    max_x_cm: float
    min_y_cm: float
    max_y_cm: float


def check_containment(x_cm: float, y_cm: float, footprint: CubeFootprint, *,
                      margin_cm: float = 30.0) -> Containment:
    f = footprint
    if not (f.min_x_cm <= x_cm <= f.max_x_cm and f.min_y_cm <= y_cm <= f.max_y_cm):
        return Containment.OUTSIDE
    if (f.min_x_cm + margin_cm <= x_cm <= f.max_x_cm - margin_cm
            and f.min_y_cm + margin_cm <= y_cm <= f.max_y_cm - margin_cm):
        return Containment.INSIDE
    return Containment.MARGIN
```

- [ ] **Step 4: Run and commit** — suite → Expected: 239 passed

```bash
git add src/sentinel/pose/containment.py tests/pose/test_containment.py
git commit -m "feat(pose): advisory cube containment check (SR-063 AC-2)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `pose-map-check` — single vs joint on recorded frames

The Stage 1 instrument: "did joint PnP help on real frames?" and "is a stand mis-measured?", with no ground truth needed.

**Files:**
- Create: `src/sentinel/pose/mapcheck.py`
- Modify: `src/sentinel/cli.py`
- Test: `tests/pose/test_mapcheck.py`, `tests/test_cli_pose_stream.py` (append)

**Interfaces:**
- Consumes: `estimate_pose(..., joint=)` (Task 1).
- Produces: `compare_frames(frames, tag_map) -> dict` with keys `frames`, `multi_tag_frames`, `single`, `joint` (each `{"n", "spread_p95_cm", "median_x_units", "median_y_units"}`) and `zones` (`{zone_id: {"n", "median_joint_reprojection_px"}}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/pose/test_mapcheck.py
import math

import numpy as np

from sentinel.pose.mapcheck import compare_frames
from sentinel.pose.tagmap import TagMap
from tests.pose.rendering import SPLAYED_PAIR, make_doc, render


def test_compare_frames_reports_both_paths_and_the_zone_residual():
    cam = np.array([2.0, 4.0, 1.0])
    frames = [render(SPLAYED_PAIR, cam, 180.0) for _ in range(3)]
    frames.append(np.full((720, 960, 3), 255, dtype=np.uint8))  # blank: skipped
    result = compare_frames(frames, TagMap(make_doc(SPLAYED_PAIR)))
    assert result["frames"] == 4 and result["multi_tag_frames"] == 3
    assert result["single"]["n"] == 3 and result["joint"]["n"] == 3
    assert result["joint"]["spread_p95_cm"] == 0.0  # identical frames
    assert math.isclose(result["joint"]["median_x_units"], 20.0, abs_tol=0.5)
    assert result["zones"]["PAIR"]["n"] == 3
    assert result["zones"]["PAIR"]["median_joint_reprojection_px"] <= 2.0
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.pose.mapcheck`

- [ ] **Step 3: Implement**

```python
# src/sentinel/pose/mapcheck.py
"""Truth-free tag-map QA on recorded frames: single-tag vs joint PnP spread, and
the joint reprojection residual per zone (a mis-measured stand shows up as a
persistently high residual)."""
import math
import statistics
from typing import Iterable

import numpy as np

from sentinel.pose.detector import estimate_pose
from sentinel.pose.tagmap import TagMap


def _summary(points: list[tuple[float, float]], cm_per_unit: float) -> dict:
    if not points:
        return {"n": 0, "spread_p95_cm": None, "median_x_units": None, "median_y_units": None}
    mx = statistics.median(p[0] for p in points)
    my = statistics.median(p[1] for p in points)
    dist = sorted(math.hypot(x - mx, y - my) for x, y in points)
    return {"n": len(points), "spread_p95_cm": dist[int(0.95 * (len(dist) - 1))] * cm_per_unit,
            "median_x_units": mx, "median_y_units": my}


def compare_frames(frames: Iterable[np.ndarray], tag_map: TagMap) -> dict:
    cm_per_unit = tag_map.room_scale_m_per_unit * 100.0
    total = 0
    single, joint = [], []
    residuals: dict[str, list[float]] = {}
    for frame in frames:
        total += 1
        try:
            j = estimate_pose(frame, tag_map, run_id="mapcheck", report_id="j")
        except ValueError:
            continue
        if len(j.tag_observations) < 2:
            continue  # only frames where the joint path actually ran are comparable
        s = estimate_pose(frame, tag_map, run_id="mapcheck", report_id="s", joint=False)
        joint.append(j.point)
        single.append(s.point)
        for zone in {o.zone_id for o in j.tag_observations}:
            residuals.setdefault(zone, []).append(j.reprojection_error_px)
    return {
        "frames": total,
        "multi_tag_frames": len(joint),
        "single": _summary(single, cm_per_unit),
        "joint": _summary(joint, cm_per_unit),
        "zones": {zone: {"n": len(v), "median_joint_reprojection_px": statistics.median(v)}
                  for zone, v in sorted(residuals.items())},
    }
```

- [ ] **Step 4: CLI.** In `cli.py` add `from .pose.mapcheck import compare_frames`; register inside `build_parser()` next to `pose_score`:

```python
    map_check = subparsers.add_parser(
        "pose-map-check", help="offline: single-tag vs joint PnP spread and per-stand residual")
    map_check.add_argument("--frames", type=Path, required=True,
                           help="directory of recorded PNG frames")
    map_check.add_argument("--tag-map", type=Path, required=True)
```

handler above `main`:

```python
def _run_pose_map_check(args: Any) -> int:
    import cv2

    paths = sorted(args.frames.glob("*.png"))
    if not paths:
        print(f"pose-map-check: no .png frames in {args.frames}", flush=True)
        return 1
    result = compare_frames((cv2.imread(str(p)) for p in paths), load_tag_map(str(args.tag_map)))
    print(f"pose-map-check: {result['multi_tag_frames']} of {result['frames']} frames saw 2+ tags")
    for name in ("single", "joint"):
        row = result[name]
        if row["n"]:
            print(f"  {name:<6} n={row['n']} spread p95={row['spread_p95_cm']:.2f}cm "
                  f"median=({row['median_x_units']:.2f}, {row['median_y_units']:.2f}) units")
    for zone, row in result["zones"].items():
        flag = "  <- re-measure this stand" if row["median_joint_reprojection_px"] > 1.0 else ""
        print(f"  {zone}: joint reprojection median "
              f"{row['median_joint_reprojection_px']:.2f}px over {row['n']} frames{flag}")
    return 0 if result["multi_tag_frames"] else 1
```

dispatch in `main()` directly after the `pose-score` branch:

```python
    if args.command == "pose-map-check":
        return _run_pose_map_check(args)
```

Append to `tests/test_cli_pose_stream.py`:

```python
def test_pose_map_check_fails_cleanly_on_an_empty_directory(tmp_path, capsys):
    from sentinel.cli import main
    code = main(["pose-map-check", "--frames", str(tmp_path),
                 "--tag-map", "calibration/tag-map-calibrated.json"])
    assert code == 1 and "no .png frames" in capsys.readouterr().out
```

- [ ] **Step 5: Run and commit** — suite → Expected: 241 passed

```bash
git add src/sentinel/pose/mapcheck.py src/sentinel/cli.py tests/pose/test_mapcheck.py tests/test_cli_pose_stream.py
git commit -m "feat(pose): pose-map-check compares single vs joint PnP and flags bad stands" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Site model — one file yields tag map, envelope and footprint

**Files:**
- Create: `src/sentinel/cube/__init__.py` (empty), `src/sentinel/cube/site.py`, `tests/cube/__init__.py` (empty)
- Modify: `src/sentinel/cli.py` (`cube-site-map`)
- Test: `tests/cube/test_site.py`

**Interfaces:**
- Consumes: `CubeFootprint` (Task 3); `FlightEnvelope` (existing, `sentinel.envelope`).
- Produces: `Stand(x_m, first_tag_id, heights_m: tuple, size_m)`; `Site` with attributes `line_y_m, stands, cube_min_x_m, cube_max_x_m, cube_min_y_m, cube_max_y_m, cube_max_z_m, inset_m, min_z_cm, max_z_cm` and methods `tag_map_doc(camera_calibration: dict) -> dict`, `envelope() -> FlightEnvelope`, `footprint() -> CubeFootprint`; `parse_site(doc: dict) -> Site`; `load_site(path) -> Site`.

- [ ] **Step 1: Write the failing tests** (paste **SITE_DOC** and **CALIBRATION** from the top of this plan at module level)

```python
# tests/cube/test_site.py
import copy

import pytest

from sentinel.cube.site import parse_site
from sentinel.pose.tagmap import TagMap

# SITE_DOC = {...}; CALIBRATION = {...}   <- paste the shared fixture here


def test_tag_map_has_two_tags_per_stand_facing_minus_y():
    tag_map = TagMap(parse_site(SITE_DOC).tag_map_doc(CALIBRATION))
    assert all(tag_map.has_tag(i) for i in range(14)) and not tag_map.has_tag(14)
    low, high = tag_map.tag(4), tag_map.tag(5)          # third stand, x = 2.3 m
    assert (low.x_units, low.y_units, low.z_units, low.yaw_deg) == (23.0, 50.0, 0.90, -90.0)
    assert (high.x_units, high.z_units, high.zone_id) == (23.0, 1.45, "STAND_2")
    assert tag_map.calibration_source == "chessboard"


def test_envelope_is_the_cube_inset_by_the_drift_margin():
    env = parse_site(SITE_DOC).envelope()
    assert (env.min_x_cm, env.max_x_cm, env.min_y_cm, env.max_y_cm) == (60, 480, 230, 390)
    assert (env.min_z_cm, env.max_z_cm, env.max_move_cm) == (80, 180, 300)


def test_footprint_is_the_cube_itself():
    f = parse_site(SITE_DOC).footprint()
    assert (f.min_x_cm, f.max_x_cm, f.min_y_cm, f.max_y_cm) == (30.0, 510.0, 200.0, 420.0)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(schema_version="nope"), "unsupported"),
    (lambda d: d.update(stands=[]), "stand"),
    (lambda d: d["stands"][1].update(first_tag_id=0), "duplicate"),
    (lambda d: d["cube"].update(max_y_m=4.8), "0.5 m"),     # cube must stop short of the tag line
    (lambda d: d["cube"].update(max_z_m=1.5), "altitude"),  # band top above the cube
])
def test_invalid_sites_are_refused(mutate, message):
    doc = copy.deepcopy(SITE_DOC)
    mutate(doc)
    with pytest.raises(ValueError, match=message):
        parse_site(doc)
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.cube`

- [ ] **Step 3: Implement**

```python
# src/sentinel/cube/site.py
"""cube-site-v1: one description of the physical setup.

A straight line of tag stands at y = line_y_m, tags facing -y, and the operating
cube in front of it. The tag map, the flight envelope and the containment
footprint are all derived from this one file so they cannot drift apart.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from sentinel.envelope import FlightEnvelope
from sentinel.pose.containment import CubeFootprint

ROOM_SCALE_M_PER_UNIT = 0.1
MIN_LINE_STANDOFF_M = 0.5
MAX_LEG_CM = 300


@dataclass(frozen=True)
class Stand:
    x_m: float
    first_tag_id: int
    heights_m: tuple[float, ...]
    size_m: float


@dataclass(frozen=True)
class Site:
    line_y_m: float
    stands: tuple[Stand, ...]
    cube_min_x_m: float
    cube_max_x_m: float
    cube_min_y_m: float
    cube_max_y_m: float
    cube_max_z_m: float
    inset_m: float
    min_z_cm: int
    max_z_cm: int

    def tag_map_doc(self, camera_calibration: dict) -> dict:
        tags = []
        for index, stand in enumerate(self.stands):
            for k, height in enumerate(stand.heights_m):
                tags.append({
                    "tag_id": stand.first_tag_id + k, "zone_id": f"STAND_{index}",
                    "x_units": round(stand.x_m / ROOM_SCALE_M_PER_UNIT, 4),
                    "y_units": round(self.line_y_m / ROOM_SCALE_M_PER_UNIT, 4),
                    "z_units": height, "yaw_deg": -90.0, "size_m": stand.size_m,
                    "mounting": "WALL"})
        return {"schema_version": "tag-map-v1", "tag_family": "36h11",
                "room": {"width_units": 100, "height_units": 100,
                         "origin": "LEFT_ALLEY_FRONT_CORNER"},
                "room_scale_m_per_unit": ROOM_SCALE_M_PER_UNIT, "tags": tags,
                "camera_calibration": camera_calibration}

    def envelope(self) -> FlightEnvelope:
        """Commanded bounds: the cube inset by the open-loop drift margin."""
        return FlightEnvelope(
            min_x_cm=round((self.cube_min_x_m + self.inset_m) * 100),
            max_x_cm=round((self.cube_max_x_m - self.inset_m) * 100),
            min_y_cm=round((self.cube_min_y_m + self.inset_m) * 100),
            max_y_cm=round((self.cube_max_y_m - self.inset_m) * 100),
            min_z_cm=self.min_z_cm, max_z_cm=self.max_z_cm, max_move_cm=MAX_LEG_CM)

    def footprint(self) -> CubeFootprint:
        # round: 0.3 * 100 is 30.000000000000004 in binary floating point
        return CubeFootprint(round(self.cube_min_x_m * 100, 1), round(self.cube_max_x_m * 100, 1),
                             round(self.cube_min_y_m * 100, 1), round(self.cube_max_y_m * 100, 1))


def parse_site(doc: dict) -> Site:
    if doc.get("schema_version") != "cube-site-v1":
        raise ValueError(f"unsupported site file: {doc.get('schema_version')!r}")
    stands = tuple(Stand(float(s["x_m"]), int(s["first_tag_id"]),
                         tuple(float(h) for h in s["heights_m"]), float(s["size_m"]))
                   for s in doc["stands"])
    if not stands or any(not s.heights_m for s in stands):
        raise ValueError("a site needs at least one stand, each with at least one tag height")
    ids = [s.first_tag_id + k for s in stands for k in range(len(s.heights_m))]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate tag ids across stands: {sorted(ids)}")
    cube, commanded = doc["cube"], doc["commanded"]
    site = Site(float(doc["line_y_m"]), stands, float(cube["min_x_m"]), float(cube["max_x_m"]),
                float(cube["min_y_m"]), float(cube["max_y_m"]), float(cube["max_z_m"]),
                float(commanded["inset_m"]), int(commanded["min_z_cm"]), int(commanded["max_z_cm"]))
    if site.cube_max_y_m > site.line_y_m - MIN_LINE_STANDOFF_M:
        raise ValueError(f"the cube must end at least {MIN_LINE_STANDOFF_M} m before the tag line")
    if site.max_z_cm > site.cube_max_z_m * 100 or site.min_z_cm >= site.max_z_cm:
        raise ValueError("the commanded altitude band must lie inside the cube")
    env = site.envelope()
    if env.min_x_cm >= env.max_x_cm or env.min_y_cm >= env.max_y_cm or env.min_x_cm < 0:
        raise ValueError("the inset leaves no commanded area inside the cube")
    return site


def load_site(path) -> Site:
    return parse_site(json.loads(Path(path).read_text(encoding="utf-8")))
```

- [ ] **Step 4: CLI `cube-site-map`.** In `cli.py` add `from .cube.site import load_site`; register:

```python
    site_map = subparsers.add_parser(
        "cube-site-map", help="generate the tag-map-v1 file from a cube-site-v1 file")
    site_map.add_argument("--site", type=Path, required=True)
    site_map.add_argument("--calibration-from", type=Path, required=True,
                          help="existing tag map whose camera_calibration is reused")
    site_map.add_argument("--out", type=Path, required=True)
```

handler and dispatch (`if args.command == "cube-site-map": return _run_cube_site_map(args)`):

```python
def _run_cube_site_map(args: Any) -> int:
    try:
        site = load_site(args.site)
        calibration = json.loads(
            args.calibration_from.read_text(encoding="utf-8"))["camera_calibration"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"cube-site-map failed: {exc}", flush=True)
        return 1
    doc = site.tag_map_doc(calibration)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    env = site.envelope()
    print(f"cube-site-map: wrote {len(doc['tags'])} tags to {args.out}; commanded bounds "
          f"x {env.min_x_cm}-{env.max_x_cm} y {env.min_y_cm}-{env.max_y_cm} "
          f"z {env.min_z_cm}-{env.max_z_cm} cm")
    return 0
```

Ensure `import json` exists at the top of `cli.py`. Append to `tests/cube/test_site.py`:

```python
def test_cube_site_map_cli_writes_a_loadable_tag_map(tmp_path):
    import json
    from sentinel.cli import main
    from sentinel.pose.tagmap import load_tag_map

    site = tmp_path / "site.json"
    site.write_text(json.dumps(SITE_DOC))
    out = tmp_path / "cube-map.json"
    assert main(["cube-site-map", "--site", str(site), "--calibration-from",
                 "calibration/tag-map-calibrated.json", "--out", str(out)]) == 0
    assert load_tag_map(str(out)).has_tag(13)
```

- [ ] **Step 5: Run and commit** — suite → Expected: 250 passed

```bash
git add src/sentinel/cube tests/cube src/sentinel/cli.py
git commit -m "feat(cube): site file derives tag map, flight envelope and containment footprint" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Layout check — predicted error grid

**Files:**
- Create: `src/sentinel/pose/layout.py`
- Modify: `src/sentinel/cli.py` (`cube-layout-check`)
- Test: `tests/pose/test_layout.py`

**Interfaces:**
- Consumes: `_room_corners` (Task 1); `TagMap`; `parse_site`/`load_site` (Task 5, CLI and tests only).
- Produces: `predict_cell(tag_map, x_m, y_m, alt_m, *, rng, trials=60, sigma_px=0.35, reality=2.5) -> dict` with keys `x_m, y_m, alt_m, tags_in_frame, p95_cm` (`p95_cm` is `None` when blind); `predict_grid(tag_map, xs_m, ys_m, alts_m, *, trials=60, seed=0) -> list[dict]`. The camera faces +y, pitched 8.6° down.

- [ ] **Step 1: Write the failing tests** (paste **SITE_DOC** and **CALIBRATION**)

```python
# tests/pose/test_layout.py
import numpy as np

from sentinel.cube.site import parse_site
from sentinel.pose.layout import predict_cell, predict_grid
from sentinel.pose.tagmap import TagMap

# SITE_DOC = {...}; CALIBRATION = {...}   <- paste the shared fixture here

TAG_MAP = TagMap(parse_site(SITE_DOC).tag_map_doc(CALIBRATION))


def test_in_front_of_the_centre_block_many_tags_give_a_tight_fix():
    cell = predict_cell(TAG_MAP, 2.7, 3.0, 1.4, rng=np.random.default_rng(0), trials=40)
    assert cell["tags_in_frame"] >= 4          # the design simulation saw 6
    assert cell["p95_cm"] is not None and cell["p95_cm"] < 10.0


def test_close_in_front_of_an_open_alley_mouth_is_blind():
    cell = predict_cell(TAG_MAP, 1.5, 4.0, 1.0, rng=np.random.default_rng(0), trials=10)
    assert cell["tags_in_frame"] == 0 and cell["p95_cm"] is None


def test_grid_is_deterministic_for_a_seed_and_covers_every_cell():
    a = predict_grid(TAG_MAP, [0.6, 2.7], [2.5, 3.5], [1.0], trials=10, seed=3)
    b = predict_grid(TAG_MAP, [0.6, 2.7], [2.5, 3.5], [1.0], trials=10, seed=3)
    assert a == b and len(a) == 4
    assert {(c["x_m"], c["y_m"]) for c in a} == {(0.6, 2.5), (0.6, 3.5), (2.7, 2.5), (2.7, 3.5)}
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.pose.layout`

- [ ] **Step 3: Implement**

```python
# src/sentinel/pose/layout.py
"""Design-time prediction of pose accuracy for a tag layout (Monte Carlo).

For a camera facing +y, pitched like the Tello, it projects every tag that is
fully in frame, adds corner noise, re-solves the joint PnP, and reports the p95
horizontal error scaled by a simulation-to-reality factor. It predicts; the
static stand test measures.
"""
import math

import cv2
import numpy as np

from sentinel.pose.detector import _room_corners
from sentinel.pose.tagmap import TagMap

CAMERA_PITCH_DOWN_DEG = 8.6
FRAME_W, FRAME_H, BORDER_PX = 960, 720, 15
MAX_OBLIQUITY_DEG = 65.0


def _camera(pos, yaw_err_deg: float = 0.0):
    """(R_cam_room, tvec) for a camera at ``pos`` facing +y, pitched down."""
    a = math.radians(90.0 + yaw_err_deg)
    pitch = math.radians(CAMERA_PITCH_DOWN_DEG)
    fwd = np.array([math.cos(a), math.sin(a), 0.0]) * math.cos(pitch) \
        + np.array([0.0, 0.0, -1.0]) * math.sin(pitch)
    right = np.cross(fwd, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    R = np.vstack([right, np.cross(fwd, right), fwd])
    return R, -R @ np.asarray(pos, dtype=float)


def _visible(tag_map: TagMap, pos, R, t):
    scale = tag_map.room_scale_m_per_unit
    out = []
    for raw in tag_map.doc["tags"]:
        tag = tag_map.tag(raw["tag_id"])
        corners = _room_corners(tag, scale)
        to_cam = np.asarray(pos, dtype=float) - corners.mean(axis=0)
        to_cam[2] = 0.0
        normal = np.array([math.cos(math.radians(tag.yaw_deg)), math.sin(math.radians(tag.yaw_deg)), 0.0])
        cosine = float(normal @ to_cam) / (np.linalg.norm(to_cam) or 1.0)
        if cosine <= math.cos(math.radians(MAX_OBLIQUITY_DEG)):
            continue
        if ((R @ corners.T).T[:, 2] + t[2] <= 0.05).any():
            continue
        px = cv2.projectPoints(corners, cv2.Rodrigues(R)[0], t, tag_map.K, tag_map.dist)[0].reshape(-1, 2)
        if ((px[:, 0] < BORDER_PX).any() or (px[:, 0] > FRAME_W - BORDER_PX).any()
                or (px[:, 1] < BORDER_PX).any() or (px[:, 1] > FRAME_H - BORDER_PX).any()):
            continue
        out.append((corners, px))
    return out


def predict_cell(tag_map: TagMap, x_m: float, y_m: float, alt_m: float, *, rng,
                 trials: int = 60, sigma_px: float = 0.35, reality: float = 2.5) -> dict:
    pos = np.array([x_m, y_m, alt_m], dtype=float)
    R, t = _camera(pos)
    seen = _visible(tag_map, pos, R, t)
    cell = {"x_m": x_m, "y_m": y_m, "alt_m": alt_m, "tags_in_frame": len(seen), "p95_cm": None}
    if not seen:
        return cell
    obj = np.vstack([c for c, _ in seen])
    clean = np.vstack([p for _, p in seen])
    errors = []
    for _ in range(trials):
        image = clean + rng.normal(0.0, sigma_px, clean.shape)
        R0, t0 = _camera(pos + rng.normal(0.0, 0.10, 3), rng.normal(0.0, 4.0))
        ok, rvec, tvec = cv2.solvePnP(
            obj, image, tag_map.K, tag_map.dist, rvec=cv2.Rodrigues(R0)[0],
            tvec=t0.reshape(3, 1), useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            continue
        est = (-cv2.Rodrigues(rvec)[0].T @ tvec).flatten()
        errors.append(math.hypot(est[0] - pos[0], est[1] - pos[1]))
    if errors:
        cell["p95_cm"] = round(float(np.percentile(errors, 95)) * 100.0 * reality, 1)
    return cell


def predict_grid(tag_map: TagMap, xs_m, ys_m, alts_m, *, trials: int = 60, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [predict_cell(tag_map, x, y, alt, rng=rng, trials=trials)
            for alt in alts_m for y in ys_m for x in xs_m]
```

- [ ] **Step 4: CLI `cube-layout-check`.** Add `from .pose.layout import predict_grid`; register:

```python
    layout = subparsers.add_parser(
        "cube-layout-check", help="predict pose accuracy over the cube for a site file")
    layout.add_argument("--site", type=Path, required=True)
    layout.add_argument("--calibration-from", type=Path, required=True)
    layout.add_argument("--alt", type=float, action="append",
                        help="altitude in metres; repeatable (default 1.0 1.4 1.8)")
    layout.add_argument("--step", type=float, default=0.5)
    layout.add_argument("--trials", type=int, default=60)
```

handler and dispatch (`if args.command == "cube-layout-check": return _run_cube_layout_check(args)`):

```python
def _run_cube_layout_check(args: Any) -> int:
    from .pose.tagmap import TagMap

    try:
        site = load_site(args.site)
        calibration = json.loads(
            args.calibration_from.read_text(encoding="utf-8"))["camera_calibration"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"cube-layout-check failed: {exc}", flush=True)
        return 1
    env = site.envelope()

    def span(lo_cm: int, hi_cm: int) -> list[float]:
        count = max(1, int((hi_cm - lo_cm) / (args.step * 100.0)))
        return [round((lo_cm + i * (hi_cm - lo_cm) / count) / 100.0, 2) for i in range(count + 1)]

    xs, ys = span(env.min_x_cm, env.max_x_cm), span(env.min_y_cm, env.max_y_cm)
    alts = args.alt or [1.0, 1.4, 1.8]
    cells = predict_grid(TagMap(site.tag_map_doc(calibration)), xs, ys, alts, trials=args.trials)
    print("cube-layout-check: predicted real p95 horizontal error in cm (simulation x 2.5); "
          "'--' = no tag fully in frame")
    blind = 0
    for alt in alts:
        print(f" altitude {alt:.1f} m, standoff from the tag line shown per row")
        print("          " + "".join(f"x={x:<5.2f}" for x in xs))
        for y in reversed(ys):
            row = [c for c in cells if c["alt_m"] == alt and c["y_m"] == y]
            blind += sum(1 for c in row if c["p95_cm"] is None)
            text = "".join(f"{'--' if c['p95_cm'] is None else c['p95_cm']:<7}" for c in row)
            print(f"   {site.line_y_m - y:4.2f} m  {text}")
    print(f" {blind} blind cell(s): stops there need \"localise\": false")
    return 0
```

Append to `tests/pose/test_layout.py`:

```python
def test_cube_layout_check_cli_runs(tmp_path, capsys):
    import json
    from sentinel.cli import main

    site = tmp_path / "site.json"
    site.write_text(json.dumps(SITE_DOC))
    code = main(["cube-layout-check", "--site", str(site), "--calibration-from",
                 "calibration/tag-map-calibrated.json", "--alt", "1.4", "--step", "1.5",
                 "--trials", "5"])
    out = capsys.readouterr().out
    assert code == 0 and "altitude 1.4 m" in out and "blind cell" in out
```

- [ ] **Step 5: Run and commit** — suite → Expected: 254 passed

```bash
git add src/sentinel/pose/layout.py src/sentinel/cli.py tests/pose/test_layout.py
git commit -m "feat(cube): cube-layout-check predicts pose accuracy for a site layout" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Stage 1 "stand test" protocol

**Files:**
- Modify: `docs/wp5-rehearsal-manifest.md` (append one section)

- [ ] **Step 1: Append this section verbatim to the end of the manifest**

```markdown
## Cube navigation — Stage 1 "stand test" (2026-09-20 design, rev 3)

Design: `docs/superpowers/specs/2026-09-20-wp5-cube-positioning-design.md`. Motors off
throughout; `pose-stream` never takes off, so this stage needs no G3/G4 approval. It fits a small
room.

**Setup.** Two vertical supports ("stands") about 0.5 m apart on one straight line, one or two
tags each, tag faces square to the line. Lay one tape along the line: each stand's `x_m` is a
tape reading; tag-centre heights by ruler; tag edge length by ruler (printers scale ~96%). Write
`artifacts/site.json` (`cube-site-v1`) and generate the map:

    uv run sentinel cube-site-map --site artifacts/site.json --calibration-from calibration/tag-map-calibrated.json --out artifacts/cube-map.json

**Capture.** Drone on a support about 1.1 m back from the line, lens facing it: on the centre
line and about 0.5 m to one side, each at two lens heights (for example 1.0 and 1.4 m). Truth is
measured to the lens. Per point:

    uv run sentinel pose-stream --real --confirm --tag-map artifacts/cube-map.json --db artifacts/cube_s1.db --duration 20 --record-dir artifacts/frames_s1_<point> --preview

**Score.** `pose-score` against a truth file, ladder p95 <= 15 cm / 10 deg, then 10 / 5, then
5 / 3, with `--min-samples 15`. Then per recording, the truth-free comparison:

    uv run sentinel pose-map-check --frames artifacts/frames_s1_<point> --tag-map artifacts/cube-map.json

The design claim holds if the joint spread is no worse than the single-tag spread at every point
and clearly better at the off-centre points. A stand whose joint reprojection median exceeds 1 px
has been mis-measured: fix the site file, not the detector. Compare the measured spreads with
`cube-layout-check` for the same site and note the ratio; it recalibrates the 2.5x factor.
```

- [ ] **Step 2: Commit**

```bash
git add docs/wp5-rehearsal-manifest.md
git commit -m "docs(wp5): Stage 1 stand-test protocol" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

**Part A exit gate (operator):** run the stand test. Part B's `off` mode does not depend on it; `passive` and `reanchor` do.

---

# Part B — cube missions

### Task 8: Runner refactor — overridable flight sequence, injectable envelope

Pure refactor: zero behaviour change; every existing runner test stays green untouched.

**Files:**
- Modify: `src/sentinel/runner.py`
- Test: `tests/test_runner.py` (append)

**Interfaces:**
- Produces: `FlightTestRunner(..., envelope: FlightEnvelope | None = None)`; `FlightTestRunner._fly_sequence(self) -> None`, called by `run()` between takeoff and hover/stop.

- [ ] **Step 1: Write the failing tests** (append; `_FakeClient` exists in that file and records moves as `"forward 50"`)

```python
def test_envelope_is_injectable():
    from sentinel.envelope import FlightEnvelope
    envelope = FlightEnvelope(min_z_cm=80, max_z_cm=180)
    runner = FlightTestRunner(_FakeClient(), "dry-run", envelope=envelope)
    assert runner.envelope is envelope


def test_fly_sequence_is_the_override_point():
    calls = []

    class _Custom(FlightTestRunner):
        def _fly_sequence(self):
            calls.append("custom")

    client = _FakeClient()
    report = _Custom(client, "dry-run").run(confirm=True)
    assert calls == ["custom"] and report.status == "PASS"
    commands = [r.command for r in client.records]
    assert "forward 50" not in commands and "land" in commands
```

- [ ] **Step 2: Run to verify failure** — `TypeError: ... unexpected keyword argument 'envelope'`

- [ ] **Step 3: Implement**

In `__init__`, add the keyword-only parameter `envelope: FlightEnvelope | None = None` after `video_only_duration_s`, and replace `self.envelope = FlightEnvelope()` with:

```python
        self.envelope = envelope if envelope is not None else FlightEnvelope()
```

Add directly above `run`:

```python
    def _fly_sequence(self) -> None:
        """The approved airborne sequence, between takeoff and hover/stop."""

        self._enter_altitude_band()
        for direction in ("forward", "right", "back", "left", "up", "down"):
            self.move_checked(direction, 50)

        self._emit("flight", "sending clockwise rotation 90 degrees")
        response = self._call(lambda: self.client.rotate("cw", 90))
        if not self._is_ok(response):
            raise RuntimeError(f"clockwise rotation failed: {response}")
        self._emit("flight", "sending counter-clockwise rotation 90 degrees")
        response = self._call(lambda: self.client.rotate("ccw", 90))
        if not self._is_ok(response):
            raise RuntimeError(f"counter-clockwise rotation failed: {response}")
```

In `run()`, replace the block from `self._enter_altitude_band()` through the counter-clockwise rotation failure check with the single line `self._fly_sequence()` (same indentation). Hover/stop and landing stay in `run()`.

- [ ] **Step 4: Run and commit** — suite → Expected: 256 passed; `grep -n "sentinel.pose" src/sentinel/runner.py` prints nothing.

```bash
git add src/sentinel/runner.py tests/test_runner.py
git commit -m "refactor(runner): overridable _fly_sequence and injectable envelope" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Mission file and `CubeMissionRunner`

**Files:**
- Create: `src/sentinel/cube/mission.py`
- Test: `tests/cube/test_mission.py`

**Interfaces:**
- Consumes: Task 8 (`_fly_sequence`, `envelope=`); `Site` (Task 5); `localise`, `LocalisationFix` (Task 2); `check_containment`, `Containment` (Task 3); `heading_delta_deg` (existing, `sentinel.pose.scoring`); `run_pose_stream` (existing); `FrameRecorder` (existing, `sentinel.pose.replay`).
- Produces:
  - `Waypoint(name, x_cm, y_cm, z_cm, looks_deg: tuple[int, ...] = (0,), localise: bool = True)`; `Mission(start_x_cm, start_y_cm, waypoints)`.
  - `load_mission(path) -> Mission` for `{"schema_version": "cube-mission-v1", "start": {"x_cm", "y_cm"}, "waypoints": [{"name", "x_cm", "y_cm", "z_cm", "looks_deg"?, "localise"?}]}`; 1–8 waypoints; each look offset in `[-170, 170]`.
  - `axis_move(delta_cm: float) -> int` — 0 when `|delta| <= 10`, else `max(20, round(|delta|))`.
  - `CubeMissionRunner(client, mode, minimum_battery=40, *, mission, site, pose_mode="off", tag_map=None, conn=None, run_id="", mission_log, capture_dir=None, burst_fn=None, sleep_fn=time.sleep, **runner_kwargs)`; `pose_mode ∈ {"off", "passive", "reanchor"}`. Raises `PlanError` (a `ValueError`) from the constructor when the mission does not fit the envelope.
  - `ContainmentAbort`, `PoseImplausible` (both `RuntimeError`).
  - Log line per waypoint: `{"waypoint", "commanded_cm", "pose_mode", "status", "fix_cm", "heading_error_deg", "n", "spread_p95_cm", "arrival_error_cm", "containment", "proposed_trims", "trim_rounds_sent", "looks_deg"}`; `status ∈ {"OPEN_LOOP", "LOCALISED", "UNLOCALISED", "IMPLAUSIBLE"}`.

- [ ] **Step 1: Write the failing tests** (paste **SITE_DOC**)

```python
# tests/cube/test_mission.py
import json
from datetime import datetime, timezone

import pytest

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.cube.mission import (
    CubeMissionRunner, Mission, PlanError, Waypoint, axis_move, load_mission,
)
from sentinel.cube.site import parse_site
from sentinel.dry_run import DryRunClient

# SITE_DOC = {...}   <- paste the shared fixture here

SITE = parse_site(SITE_DOC)
MISSION = Mission(270, 250, (
    Waypoint("WP1", 270, 300, 100),
    Waypoint("WP2", 150, 300, 140, looks_deg=(-45, 0, 45)),
))


def _report(x_cm, y_cm, heading=90.0):
    return DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(x_cm / 10.0, y_cm / 10.0), heading_deg=heading,
        altitude_m=None, method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(4, "STAND_2", 2.0, 0.5),), reprojection_error_px=0.5)


def _runner(tmp_path, pose_mode, bursts, mission=MISSION):
    queue = list(bursts)

    def burst():
        if pose_mode == "off":
            raise AssertionError("pose_mode off must never localise")
        return queue.pop(0)

    client = DryRunClient()
    runner = CubeMissionRunner(client, "dry-run", mission=mission, site=SITE, pose_mode=pose_mode,
                               mission_log=tmp_path / "mission.jsonl", burst_fn=burst,
                               sleep_fn=lambda seconds: None)
    return runner, client


def _log(tmp_path):
    return [json.loads(line) for line in (tmp_path / "mission.jsonl").read_text().splitlines()]


def test_axis_move_rule():
    assert [axis_move(d) for d in (0, 10, -10, 11, -14, 20, -135.4)] == [0, 0, 0, 20, 20, 20, 135]


def test_off_mode_navigates_open_loop_with_height_and_looks(tmp_path):
    runner, client = _runner(tmp_path, "off", [])
    assert runner.run(confirm=True).status == "PASS"
    commands = [c for c in client.commands if c.split()[0] in
                {"forward", "back", "left", "right", "up", "down", "cw", "ccw", "land"}]
    assert commands == ["forward 50", "left 120", "up 40",
                        "cw 45", "ccw 45", "ccw 45", "cw 45", "land"]
    assert [e["status"] for e in _log(tmp_path)] == ["OPEN_LOOP", "OPEN_LOOP"]


def test_passive_mode_measures_and_proposes_but_never_sends(tmp_path):
    bursts = [[_report(270, 282)] * 10, [_report(151, 300)] * 10]   # WP1 is 18 cm short in y
    runner, client = _runner(tmp_path, "passive", bursts)
    assert runner.run(confirm=True).status == "PASS"
    wp1, wp2 = _log(tmp_path)
    assert wp1["status"] == "LOCALISED" and wp1["containment"] == "INSIDE"
    assert wp1["arrival_error_cm"] == pytest.approx(18.0, abs=0.1)
    assert wp1["proposed_trims"] == [["forward", 20]] and wp1["trim_rounds_sent"] == 0
    assert wp2["proposed_trims"] == []
    assert "forward 20" not in client.commands          # SR-063 as written: nothing sent
    assert "left 120" in client.commands                # next leg still planned open-loop


def test_reanchor_mode_trims_then_plans_the_next_leg_from_the_fix(tmp_path):
    bursts = [[_report(270, 282)] * 10,                  # WP1 first fix: 18 cm short
              [_report(275, 301)] * 10,                  # after the trim: good enough
              [_report(150, 300)] * 10]                  # WP2
    runner, client = _runner(tmp_path, "reanchor", bursts)
    assert runner.run(confirm=True).status == "PASS"
    assert "forward 20" in client.commands
    assert "left 125" in client.commands and "left 120" not in client.commands
    wp1 = _log(tmp_path)[0]
    assert wp1["trim_rounds_sent"] == 1 and wp1["arrival_error_cm"] == pytest.approx(5.1, abs=0.1)


def test_reanchor_mode_trims_a_yaw_error(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100),))
    runner, client = _runner(tmp_path, "reanchor", [[_report(270, 300, heading=104.0)] * 10],
                             mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    assert "cw 14" in client.commands
    assert _log(tmp_path)[0]["heading_error_deg"] == pytest.approx(14.0)


def test_unreliable_burst_degrades_the_stop_to_open_loop(tmp_path):
    runner, _ = _runner(tmp_path, "reanchor", [[_report(270, 300)] * 3, [_report(150, 300)] * 10])
    assert runner.run(confirm=True).status == "PASS"
    assert [e["status"] for e in _log(tmp_path)] == ["UNLOCALISED", "LOCALISED"]


def test_two_implausible_fixes_in_a_row_land_the_drone(tmp_path):
    bursts = [[_report(200, 300)] * 10, [_report(80, 380)] * 10]    # both > 60 cm from dead reckoning
    runner, client = _runner(tmp_path, "reanchor", bursts)
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "implausible" in report.error.lower()
    assert "land" in client.commands
    assert [e["status"] for e in _log(tmp_path)] == ["IMPLAUSIBLE", "IMPLAUSIBLE"]


def test_fix_outside_the_cube_lands_the_drone(tmp_path):
    runner, client = _runner(tmp_path, "passive", [[_report(270, 430)] * 10])
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "containment" in report.error.lower()
    assert "land" in client.commands and "left 120" not in client.commands
    assert _log(tmp_path)[0]["containment"] == "OUTSIDE"


def test_a_stop_flagged_localise_false_is_not_measured(tmp_path):
    mission = Mission(270, 250, (Waypoint("WP1", 270, 300, 100, localise=False),))
    runner, _ = _runner(tmp_path, "passive", [], mission=mission)
    assert runner.run(confirm=True).status == "PASS"
    assert _log(tmp_path)[0]["status"] == "OPEN_LOOP"


def test_mission_outside_the_commanded_bounds_is_refused_before_takeoff(tmp_path):
    with pytest.raises(PlanError, match="WP1"):
        _runner(tmp_path, "off", [], mission=Mission(270, 250, (Waypoint("WP1", 270, 410, 100),)))
    with pytest.raises(PlanError, match="start"):
        _runner(tmp_path, "off", [], mission=Mission(20, 250, (Waypoint("WP1", 270, 300, 100),)))


def test_load_mission(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "schema_version": "cube-mission-v1", "start": {"x_cm": 270, "y_cm": 250},
        "waypoints": [{"name": "WP1", "x_cm": 270, "y_cm": 300, "z_cm": 100},
                      {"name": "WP2", "x_cm": 150, "y_cm": 300, "z_cm": 140,
                       "looks_deg": [-45, 0, 45], "localise": False}]}))
    mission = load_mission(path)
    assert mission.waypoints[0] == Waypoint("WP1", 270, 300, 100, (0,), True)
    assert mission.waypoints[1] == Waypoint("WP2", 150, 300, 140, (-45, 0, 45), False)
    path.write_text(json.dumps({"schema_version": "cube-mission-v1",
                                "start": {"x_cm": 270, "y_cm": 250}, "waypoints": []}))
    with pytest.raises(ValueError):
        load_mission(path)
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.cube.mission`

- [ ] **Step 3: Implement**

```python
# src/sentinel/cube/mission.py
"""Waypoint missions inside the operating cube, with three pose modes.

off      - pure open-loop navigation; no tags needed.
passive  - pose is measured at each stop, logged, and may trip the existing
           abort path through the containment check. Nothing pose-derived is
           sent (SR-063 as written); the implied trim is logged only.
reanchor - additionally, at a stationary hover, a fix that passes the quality
           and plausibility gates replaces the open-loop position estimate and
           may trigger bounded trims (requires the SR-063 amendment).

The drone translates only while facing +y, so forward = +y and right = +x and
the inherited envelope bookkeeping is valid in the room frame.
"""
import json
import math
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from sentinel.envelope import EnvelopeViolation
from sentinel.models import Position
from sentinel.pose.containment import Containment, check_containment
from sentinel.pose.localise import LocalisationFix, localise
from sentinel.pose.replay import FrameRecorder
from sentinel.pose.scoring import heading_delta_deg
from sentinel.pose.stream import run_pose_stream
from sentinel.runner import FlightTestRunner

POSE_MODES = ("off", "passive", "reanchor")
MAX_WAYPOINTS = 8
MAX_LOOK_DEG = 170
FACING_DEG = 90.0
DEAD_BAND_CM = 10
MIN_MOVE_CM = 20
MAX_TRIM_ROUNDS = 2
YAW_TRIM_THRESHOLD_DEG = 10.0
PLAUSIBILITY_CM = 60.0
SETTLE_S = 1.5
BURST_MAX_S = 12.0
BURST_TARGET_REPORTS = 12
CM_PER_UNIT = 10.0


class PlanError(ValueError):
    """The mission does not fit the commanded bounds."""


class ContainmentAbort(RuntimeError):
    """The measured position left the operating cube."""


class PoseImplausible(RuntimeError):
    """Two consecutive fixes disagreed with dead reckoning."""


@dataclass(frozen=True)
class Waypoint:
    name: str
    x_cm: int
    y_cm: int
    z_cm: int
    looks_deg: tuple[int, ...] = (0,)
    localise: bool = True


@dataclass(frozen=True)
class Mission:
    start_x_cm: int
    start_y_cm: int
    waypoints: tuple[Waypoint, ...]


def load_mission(path) -> Mission:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema_version") != "cube-mission-v1":
        raise ValueError(f"unsupported mission file: {doc.get('schema_version')!r}")
    waypoints = tuple(
        Waypoint(w["name"], int(w["x_cm"]), int(w["y_cm"]), int(w["z_cm"]),
                 tuple(int(v) for v in w.get("looks_deg", (0,))), bool(w.get("localise", True)))
        for w in doc["waypoints"])
    if not 1 <= len(waypoints) <= MAX_WAYPOINTS:
        raise ValueError(f"a mission has 1-{MAX_WAYPOINTS} waypoints, got {len(waypoints)}")
    return Mission(int(doc["start"]["x_cm"]), int(doc["start"]["y_cm"]), waypoints)


def axis_move(delta_cm: float) -> int:
    """Distance to command for one axis: nothing inside the dead band, else >= the SDK minimum."""
    if abs(delta_cm) <= DEAD_BAND_CM:
        return 0
    return max(MIN_MOVE_CM, round(abs(delta_cm)))


_AXES = (("x_cm", "left", "right"), ("y_cm", "back", "forward"), ("z_cm", "down", "up"))


class _StreamHeld:
    """Client view whose video lifecycle calls are no-ops: one stream stays open all flight."""

    def __init__(self, client) -> None:
        self._client = client

    def start_video(self) -> None:
        return None

    def stop_video(self) -> None:
        return None

    def read_frame(self, timeout=None):
        return self._client.read_frame(timeout=timeout)


class CubeMissionRunner(FlightTestRunner):
    def __init__(self, client, mode, minimum_battery: int = 40, *, mission: Mission, site,
                 pose_mode: str = "off", tag_map=None, conn=None, run_id: str = "",
                 mission_log: Path, capture_dir: Path | None = None,
                 burst_fn: Callable[[], list] | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep, **runner_kwargs) -> None:
        if pose_mode not in POSE_MODES:
            raise ValueError(f"pose_mode must be one of {POSE_MODES}, got {pose_mode!r}")
        if pose_mode != "off" and burst_fn is None and (tag_map is None or conn is None):
            raise ValueError("pose modes other than 'off' need a tag map and a pose database")
        runner_kwargs["retain_video_stream"] = True
        runner_kwargs["envelope"] = site.envelope()
        super().__init__(client, mode, minimum_battery, **runner_kwargs)
        self.mission, self.site, self.pose_mode = mission, site, pose_mode
        self.tag_map, self.conn, self.run_id = tag_map, conn, run_id
        self.mission_log = Path(mission_log)
        self.recorder = FrameRecorder(capture_dir) if capture_dir is not None and mode == "real" else None
        self.burst_fn = burst_fn if burst_fn is not None else self._live_burst
        self.sleep_fn = sleep_fn
        self.footprint = site.footprint()
        self._look_offset = 0
        self._implausible_streak = 0
        self._validate_mission()

    # -- planning -----------------------------------------------------------
    def _validate_mission(self) -> None:
        env = self.envelope
        in_xy = lambda x, y: env.min_x_cm <= x <= env.max_x_cm and env.min_y_cm <= y <= env.max_y_cm
        if not in_xy(self.mission.start_x_cm, self.mission.start_y_cm):
            raise PlanError("the start pad is outside the commanded bounds")
        for wp in self.mission.waypoints:
            if not (in_xy(wp.x_cm, wp.y_cm) and env.min_z_cm <= wp.z_cm <= env.max_z_cm):
                raise PlanError(f"waypoint {wp.name} is outside the commanded bounds")
            if any(abs(look) > MAX_LOOK_DEG for look in wp.looks_deg):
                raise PlanError(f"waypoint {wp.name} has a look offset beyond {MAX_LOOK_DEG} degrees")

    # -- commands -----------------------------------------------------------
    def _rotate(self, direction: str, degrees: int) -> None:
        self._emit("flight", f"sending rotation {direction} {degrees} degrees")
        response = self._call(lambda: self.client.rotate(direction, degrees))
        if not self._is_ok(response):
            raise RuntimeError(f"rotation {direction} {degrees} failed: {response}")

    def _look_to(self, offset_deg: int) -> None:
        delta = offset_deg - self._look_offset
        if delta:
            self._rotate("ccw" if delta > 0 else "cw", abs(delta))
        self._look_offset = offset_deg

    def _step(self, direction: str, distance_cm: int, *, optional: bool) -> bool:
        """One checked move. An optional step that would leave the bounds is skipped."""
        try:
            self.move_checked(direction, distance_cm)
        except EnvelopeViolation:
            if not optional:
                raise
            self._emit("flight", f"skipped {direction} {distance_cm}: would leave the bounds")
            return False
        return True

    def _moves_toward(self, target: dict, origin: dict) -> list[tuple[str, int]]:
        moves = []
        for axis, negative, positive in _AXES:
            if axis not in target:
                continue
            delta = target[axis] - origin[axis]
            distance = axis_move(delta)
            if distance:
                moves.append((positive if delta > 0 else negative, distance))
        return moves

    def _go_to(self, wp: Waypoint) -> None:
        if self._look_offset != 0:
            raise RuntimeError("translation requires the drone to face +y")
        target = {"x_cm": wp.x_cm, "y_cm": wp.y_cm, "z_cm": wp.z_cm}
        for direction, distance in self._moves_toward(target, self.envelope.position.as_dict()):
            # a minimum-move overshoot (delta under 20 cm) is optional; a real leg is not
            overshoot = distance == MIN_MOVE_CM
            self._step(direction, distance, optional=overshoot)

    # -- pose ---------------------------------------------------------------
    def _live_burst(self) -> list:
        if self.mode != "real":
            # DryRunClient frames are an undecodable placeholder, so a dry run is
            # honestly UNLOCALISED at every stop.
            return []
        count = {"n": 0}

        def on_report(_report) -> None:
            count["n"] += 1

        return run_pose_stream(
            _StreamHeld(self.client), self.tag_map, self.conn, run_id=self.run_id,
            max_frames=None, duration_s=BURST_MAX_S, source_kind="LIVE",
            on_frame=lambda _frame: count["n"] < BURST_TARGET_REPORTS, on_report=on_report)

    def _localise(self, wp: Waypoint, entry: dict) -> None:
        rounds = 0
        while True:
            self.sleep_fn(SETTLE_S)
            result = localise(self.burst_fn())
            if not isinstance(result, LocalisationFix):
                if entry["status"] == "OPEN_LOOP":
                    entry["status"] = "UNLOCALISED"
                entry["n"] = result.n
                self._emit("pose", f"{wp.name}: unlocalised ({result.reason})")
                return
            fix = (result.x_units * CM_PER_UNIT, result.y_units * CM_PER_UNIT)
            dead = self.envelope.position
            heading_error = heading_delta_deg(result.heading_deg, FACING_DEG)
            containment = check_containment(fix[0], fix[1], self.footprint)
            trims = self._moves_toward({"x_cm": wp.x_cm, "y_cm": wp.y_cm},
                                       {"x_cm": fix[0], "y_cm": fix[1]})
            entry.update({
                "fix_cm": [round(fix[0], 1), round(fix[1], 1)],
                "heading_error_deg": round(heading_error, 1), "n": result.n,
                "spread_p95_cm": round(result.spread_p95_cm, 2),
                "arrival_error_cm": round(math.dist(fix, (wp.x_cm, wp.y_cm)), 1),
                "containment": containment.value,
                "proposed_trims": [list(t) for t in trims], "trim_rounds_sent": rounds,
            })
            if containment is Containment.OUTSIDE:
                entry["status"] = "LOCALISED"
                raise ContainmentAbort(f"containment: measured position {entry['fix_cm']} cm "
                                       "is outside the cube")
            if containment is Containment.MARGIN:
                self._emit("alert", f"{wp.name}: measured position is in the cube margin")
            if math.dist(fix, (dead.x_cm, dead.y_cm)) > PLAUSIBILITY_CM:
                entry["status"] = "IMPLAUSIBLE"
                self._implausible_streak += 1
                self._emit("alert", f"{wp.name}: fix {entry['fix_cm']} cm is implausible "
                                    f"against dead reckoning ({dead.x_cm}, {dead.y_cm})")
                if self._implausible_streak >= 2:
                    raise PoseImplausible("pose implausible at two consecutive stops")
                return
            self._implausible_streak = 0
            entry["status"] = "LOCALISED"
            if self.pose_mode != "reanchor":
                return
            self.envelope.position = replace(dead, x_cm=round(fix[0]), y_cm=round(fix[1]))
            if rounds == 0 and abs(heading_error) > YAW_TRIM_THRESHOLD_DEG:
                self._rotate("cw" if heading_error > 0 else "ccw", round(abs(heading_error)))
            if not trims or rounds >= MAX_TRIM_ROUNDS:
                return
            sent = [self._step(direction, distance, optional=True) for direction, distance in trims]
            if not any(sent):
                return
            rounds += 1

    # -- observation --------------------------------------------------------
    def _look(self, wp: Waypoint) -> None:
        for offset in wp.looks_deg:
            self._look_to(offset)
            self.sleep_fn(SETTLE_S)
            frame = self._call(lambda: self.client.read_frame(timeout=10))
            if self.recorder is not None:
                self.recorder.save(frame)
            self._emit("observe", f"{wp.name}: captured at look offset {offset}")
        self._look_to(0)

    def _append_log(self, entry: dict) -> None:
        self.mission_log.parent.mkdir(parents=True, exist_ok=True)
        with self.mission_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _fly_sequence(self) -> None:
        self._enter_altitude_band()
        height = self.envelope.position.z_cm
        self.envelope.position = Position(self.mission.start_x_cm, self.mission.start_y_cm, height)
        for wp in self.mission.waypoints:
            self._go_to(wp)
            entry = {"waypoint": wp.name, "commanded_cm": [wp.x_cm, wp.y_cm, wp.z_cm],
                     "pose_mode": self.pose_mode, "status": "OPEN_LOOP", "fix_cm": None,
                     "heading_error_deg": None, "n": 0, "spread_p95_cm": None,
                     "arrival_error_cm": None, "containment": None, "proposed_trims": None,
                     "trim_rounds_sent": 0, "looks_deg": list(wp.looks_deg)}
            try:
                if self.pose_mode != "off" and wp.localise:
                    self._localise(wp, entry)
                self._look(wp)
            finally:
                self._append_log(entry)
```

Notes for the implementer:
- `ContainmentAbort` and `PoseImplausible` are ordinary exceptions, so `FlightTestRunner.run()` handles them through its existing generic failure path — controlled landing, `FAIL` report. That **is** the "existing abort policy" SR-063 permits. Do not add a landing path.
- `Position.as_dict()` returns `{"x_cm", "y_cm", "z_cm"}` (see `src/sentinel/models.py`).
- In `test_reanchor_mode_trims_then_plans_the_next_leg_from_the_fix` the second fix is (275, 301): the dead-band rule proposes no further trim (5 cm), the tracked position becomes (275, 301), so the next leg is `left 125`, and the y delta of −1 cm is skipped.

- [ ] **Step 4: Run and commit** — suite → Expected: 267 passed; `grep -n "sentinel.pose" src/sentinel/runner.py` prints nothing.

```bash
git add src/sentinel/cube/mission.py tests/cube/test_mission.py
git commit -m "feat(cube): waypoint missions with off, passive and reanchor pose modes" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `cube-run` CLI and the safety checklist

**Files:**
- Modify: `src/sentinel/cli.py`, `docs/safety.md`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `CubeMissionRunner`, `load_mission` (Task 9); `load_site` (Task 5).
- Produces: `sentinel cube-run (--dry-run | --real --confirm) --site S --mission M [--pose off|passive|reanchor] [--sr063-amended] [--tag-map T] [--db D] [--mission-log L] [--capture-dir C]` plus every flag `run` accepts.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`; paste **SITE_DOC** near the top of the file if not already there)

```python
def _cube_files(tmp_path):
    import json
    site = tmp_path / "site.json"
    site.write_text(json.dumps(SITE_DOC))
    mission = tmp_path / "mission.json"
    mission.write_text(json.dumps({
        "schema_version": "cube-mission-v1", "start": {"x_cm": 270, "y_cm": 250},
        "waypoints": [{"name": "WP1", "x_cm": 270, "y_cm": 300, "z_cm": 120,
                       "looks_deg": [-30, 30]}]}))
    return site, mission


def test_cube_run_dry_run_off_mode(tmp_path, monkeypatch):
    import json
    from sentinel.cli import main

    monkeypatch.setattr("sentinel.cube.mission.SETTLE_S", 0.0)
    site, mission = _cube_files(tmp_path)
    log = tmp_path / "mission.jsonl"
    code = main(["cube-run", "--dry-run", "--site", str(site), "--mission", str(mission),
                 "--mission-log", str(log), "--log", str(tmp_path / "run.jsonl")])
    assert code == 0
    assert [json.loads(line)["status"] for line in log.read_text().splitlines()] == ["OPEN_LOOP"]


def test_cube_run_passive_dry_run_is_honestly_unlocalised(tmp_path, monkeypatch):
    import json
    from sentinel.cli import main

    monkeypatch.setattr("sentinel.cube.mission.SETTLE_S", 0.0)
    site, mission = _cube_files(tmp_path)
    log = tmp_path / "mission.jsonl"
    code = main(["cube-run", "--dry-run", "--site", str(site), "--mission", str(mission),
                 "--pose", "passive", "--tag-map", "calibration/tag-map-calibrated.json",
                 "--db", str(tmp_path / "pose.db"), "--mission-log", str(log),
                 "--log", str(tmp_path / "run.jsonl")])
    assert code == 0
    assert [json.loads(line)["status"] for line in log.read_text().splitlines()] == ["UNLOCALISED"]


def test_cube_run_reanchor_requires_the_amendment_flag(tmp_path, capsys):
    from sentinel.cli import main

    site, mission = _cube_files(tmp_path)
    code = main(["cube-run", "--dry-run", "--site", str(site), "--mission", str(mission),
                 "--pose", "reanchor", "--tag-map", "calibration/tag-map-calibrated.json",
                 "--log", str(tmp_path / "run.jsonl")])
    assert code == 1 and "SR-063" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure** — argparse exits: `invalid choice: 'cube-run'`

- [ ] **Step 3: Implement**

3a. In `build_parser()`, move every existing `run.add_argument(...)` call and the `mode` mutually-exclusive group into a helper, reproducing them **exactly** (diff against the current `run` parser; carry over any flag or help text not shown here):

```python
def _add_flight_arguments(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--real", action="store_true")
    parser.add_argument("--log", type=Path, default=Path("artifacts/run.jsonl"))
    parser.add_argument("--progress-log", type=Path)
    parser.add_argument("--tello-ip", default="192.168.10.1")
    parser.add_argument("--timeout", type=float, default=7.0)
    parser.add_argument("--video-path", type=Path,
                        help="record the raw H.264 stream and retain video through the run")
    parser.add_argument("--video-only", action="store_true",
                        help="capture video without takeoff or movement (requires --real)")
    parser.add_argument("--video-duration", type=float, default=30.0)
    parser.add_argument("--local-ip")
    parser.add_argument("--interface-name")
    parser.add_argument("--interface-index", type=int)
    parser.add_argument("--min-battery", type=int, default=40)
    parser.add_argument("--confirm", action="store_true")
```

then:

```python
    run = subparsers.add_parser("run", help="run the approved command sequence")
    _add_flight_arguments(run)

    cube_run = subparsers.add_parser(
        "cube-run", help="fly a waypoint mission inside the operating cube")
    _add_flight_arguments(cube_run)
    cube_run.add_argument("--site", type=Path, required=True)
    cube_run.add_argument("--mission", type=Path, required=True)
    cube_run.add_argument("--pose", choices=("off", "passive", "reanchor"), default="off")
    cube_run.add_argument("--sr063-amended", action="store_true",
                          help="assert that SR-063 has been amended to permit re-anchoring")
    cube_run.add_argument("--tag-map", type=Path)
    cube_run.add_argument("--db", type=Path, default=Path("artifacts/pose.db"))
    cube_run.add_argument("--mission-log", type=Path, default=Path("artifacts/cube-mission.jsonl"))
    cube_run.add_argument("--capture-dir", type=Path)
```

3b. In `main()`, just after the `--video-duration` check:

```python
    cube_conn: sqlite3.Connection | None = None
    if args.command == "cube-run":
        if args.video_only:
            print("cube-run does not support --video-only.")
            return 1
        if args.pose == "reanchor" and not args.sr063_amended:
            print("--pose reanchor lets pose correct motion, which SR-063 forbids as written. "
                  "Amend SR-063 first, then pass --sr063-amended.")
            return 1
        if args.pose != "off" and args.tag_map is None:
            print(f"--pose {args.pose} requires --tag-map.")
            return 1
```

Replace the runner-type selection:

```python
        if args.command == "cube-run":
            from .cube.mission import CubeMissionRunner, load_mission

            runner_type = CubeMissionRunner
        elif FlightTestRunner is None:
            from .runner import FlightTestRunner as runner_type
        else:
            runner_type = FlightTestRunner
```

and immediately after the `runner_kwargs` block for `args.real`:

```python
        if args.command == "cube-run":
            for key in ("retain_video_stream", "video_only", "video_only_duration_s"):
                runner_kwargs.pop(key, None)
            tag_map = None
            if args.pose != "off":
                tag_map = load_tag_map(str(args.tag_map))
                if args.real and tag_map.calibration_source == "nominal":
                    raise RuntimeError("cube-run --real refuses a nominal (uncalibrated) tag map")
                args.db.parent.mkdir(parents=True, exist_ok=True)
                cube_conn = sqlite3.connect(args.db)
                ensure_schema(cube_conn)
            runner_kwargs.update({
                "mission": load_mission(args.mission), "site": load_site(args.site),
                "pose_mode": args.pose, "tag_map": tag_map, "conn": cube_conn,
                "run_id": str(uuid.uuid4()), "mission_log": args.mission_log,
                "capture_dir": args.capture_dir,
            })
```

In the existing outermost `finally:` add `if cube_conn is not None: cube_conn.close()`. Ensure `import uuid` is present.

3c. Append to `docs/safety.md`:

```markdown
## Cube mission variant (`cube-run`)

- The operating cube and the commanded bounds come from the site file; print them with
  `cube-site-map`. The room must be substantially larger than the cube, and the space around the
  tag stands must be clear.
- Place the drone on the marked takeoff pad at the mission file's `start` point, **facing the
  tag line**. The whole mission is validated against the commanded bounds before takeoff.
- Commands are `move` and `rotate` only. The drone translates only while facing the tag line;
  look rotations happen at a stop and are undone before the next move.
- `--pose off`: open-loop only. `--pose passive`: pose is measured and logged at each stop and
  can trigger a controlled landing if the measured position leaves the cube; it sends nothing.
  `--pose reanchor`: pose may replace the position estimate at a hover and send at most two
  bounded trims; permitted only after SR-063 has been amended (`--sr063-amended`).
- Always run `cube-run --dry-run` on the exact site and mission files first.
```

- [ ] **Step 4: Run and commit** — suite → Expected: 270 passed

```bash
git add src/sentinel/cli.py docs/safety.md tests/test_cli.py
git commit -m "feat(cube): cube-run CLI with selectable pose mode; cube safety checklist" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Observation candidates

**Files:**
- Create: `src/sentinel/cube/observe.py`
- Test: `tests/cube/test_observe.py`

**Interfaces:**
- Consumes: `FlightEnvelope` (from `Site.envelope()`).
- Produces: `Candidate(kind, x_cm, y_cm, z_cm, look_deg, standoff_cm, width_fraction, near_blind_cm)`; `observation_candidates(mouth_x_cm, mouth_y_cm, width_cm, envelope, *, z_cm=None) -> list[Candidate]`. The alley runs along +y beyond the mouth. `kind ∈ {"close_and_deep", "back_and_wide", "oblique"}`; `z_cm` defaults to `envelope.min_z_cm`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/cube/test_observe.py
import pytest

from sentinel.cube.observe import observation_candidates
from sentinel.envelope import FlightEnvelope

ENV = FlightEnvelope(min_x_cm=60, max_x_cm=480, min_y_cm=230, max_y_cm=390,
                     min_z_cm=80, max_z_cm=180, max_move_cm=300)


def test_a_narrow_alley_needs_only_the_close_candidate():
    (c,) = observation_candidates(150, 500, 120, ENV)
    assert (c.kind, c.x_cm, c.y_cm, c.z_cm, c.look_deg) == ("close_and_deep", 150, 390, 80, 0)
    assert c.standoff_cm == 110 and c.width_fraction == 1.0
    assert c.near_blind_cm == pytest.approx(80 / 0.7373, abs=1.0)


def test_a_wide_alley_adds_a_back_and_wide_candidate():
    close, wide = observation_candidates(150, 500, 200, ENV)
    assert close.width_fraction == pytest.approx(1.406 * 110 / 200, abs=0.01)
    assert (wide.kind, wide.x_cm, wide.y_cm) == ("back_and_wide", 150, 357)
    assert wide.width_fraction == 1.0


def test_an_alley_beside_the_bounds_gets_an_oblique_look():
    (c,) = observation_candidates(20, 500, 120, ENV)
    assert (c.kind, c.x_cm, c.y_cm, c.look_deg) == ("oblique", 60, 390, 20)
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.cube.observe`

- [ ] **Step 3: Implement**

```python
# src/sentinel/cube/observe.py
"""Feasible observation stops for one alley mouth, with computed view metrics.

Deterministic geometry: the agent chooses among candidates, it never does this
arithmetic, and every candidate is inside the commanded bounds. The alley runs
along +y beyond its mouth; look offsets are relative to facing +y, + = left.
"""
import math
from dataclasses import dataclass

from sentinel.envelope import FlightEnvelope

WIDTH_PER_RANGE = 2.0 * math.tan(math.radians(70.2 / 2.0))   # 1.406 of width per unit range
BELOW_HORIZON_DEG = 55.6 / 2.0 + 8.6                          # 36.4: bottom edge of the pitched view


@dataclass(frozen=True)
class Candidate:
    kind: str
    x_cm: int
    y_cm: int
    z_cm: int
    look_deg: int
    standoff_cm: int
    width_fraction: float
    near_blind_cm: float


def _candidate(kind, x, y, z, mouth_x, mouth_y, width) -> Candidate:
    standoff = math.dist((x, y), (mouth_x, mouth_y))
    look = round(math.degrees(math.atan2(mouth_y - y, mouth_x - x)) - 90.0)
    return Candidate(kind, x, y, z, look, round(standoff),
                     round(min(1.0, WIDTH_PER_RANGE * standoff / width), 3),
                     round(z / math.tan(math.radians(BELOW_HORIZON_DEG)), 1))


def observation_candidates(mouth_x_cm: float, mouth_y_cm: float, width_cm: float,
                           envelope: FlightEnvelope, *, z_cm: int | None = None) -> list[Candidate]:
    z = envelope.min_z_cm if z_cm is None else z_cm
    x = round(min(max(mouth_x_cm, envelope.min_x_cm), envelope.max_x_cm))
    y_close = envelope.max_y_cm
    if x != round(mouth_x_cm):
        return [_candidate("oblique", x, y_close, z, mouth_x_cm, mouth_y_cm, width_cm)]
    candidates = [_candidate("close_and_deep", x, y_close, z, mouth_x_cm, mouth_y_cm, width_cm)]
    if candidates[0].width_fraction < 1.0:
        y_wide = math.floor(mouth_y_cm - width_cm / WIDTH_PER_RANGE)
        if y_wide >= envelope.min_y_cm:
            candidates.append(
                _candidate("back_and_wide", x, y_wide, z, mouth_x_cm, mouth_y_cm, width_cm))
    return candidates
```

- [ ] **Step 4: Run and commit** — suite → Expected: 273 passed

```bash
git add src/sentinel/cube/observe.py tests/cube/test_observe.py
git commit -m "feat(cube): observation candidates for best-effort alley viewing" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: SR-063 amendment — **operator-consent gated, documentation only**

Do **not** execute this task unless the operator has explicitly said, in this project's record, that SR-063 is to be amended. It changes no code.

**Files:**
- Modify: `requirements/SR-063.md`

- [ ] **Step 1:** Replace the `statement:` value with the text in spec §5 C1 (keep YAML folding style), and append below the existing closing note:

```markdown
Revised at operator consent (<date of consent>) to permit stationary re-anchoring: at a hover, a
pose fix passing the quality and plausibility gates may replace the open-loop position estimate
and trigger at most two bounded trim moves and one trim rotation toward the already-approved
waypoint. Pose still never originates a target and never acts while the drone is moving.
`src/sentinel/runner.py` still consumes no pose data; the only pose consumer in a motion path is
`CubeMissionRunner` in `reanchor` mode (`src/sentinel/cube/mission.py`).
```

Also update AC-1's `reason` text to name `src/sentinel/cube/mission.py` as the single, mode-gated exception.

- [ ] **Step 2: Commit**

```bash
git add requirements/SR-063.md
git commit -m "docs(req): amend SR-063 to permit stationary re-anchoring (operator consent)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Operator gates

| Gate | Needs | Unlocks |
|---|---|---|
| Stand test (Stage 1) | two stands, motors off | trust in `passive`/`reanchor` numbers |
| Real dimensions | alley width, table-block widths, open-floor depth → `site.json` → `cube-layout-check` | final stand positions and cube size |
| Stage 2 flight | G3 owner approval + G4 live-rehearsal consent | `--pose off`, then `--pose passive` |
| SR-063 amendment | operator consent (Task 12) | Stage 3, `--pose reanchor` |

## Self-review record

- **Spec coverage:** §3.1 → Task 5 · §3.2 → Task 6 · §3.3 → Task 1 · §3.4 → Task 2 · §3.5 → Task 3 · §3.6 → Tasks 8–10 · §3.7 → Task 11 · §4 failure table → Tasks 1 (fallback), 9 (UNLOCALISED, IMPLAUSIBLE, MARGIN/OUTSIDE, pre-takeoff refusal, skipped overshoot/trim) · §5 C1 → Tasks 10 (`--sr063-amended` guard) and 12 · C2 → Task 5 (`commanded` band) · C3 → `localise: false` in Task 9 and the blind-cell count in Task 6 · §7 Stage 1 → Tasks 4, 7.
- **Verified vs unverified code:** Task 1's implementation was spiked against the repo. The expected values in Tasks 5, 9 and 11 were hand-computed; the Task 6 expectations come from the design simulation (6 tags / ~2 cm at that cell; blind at the alley-mouth cell) with loose bounds. Everything else is unexecuted — trust the tests, not the listings.
- **Type consistency:** `_solve_mapped_tags` 5-tuple is consumed identically in `estimate_all`, `estimate_pose`, `_solve_joint`; `CubeFootprint(min_x_cm, max_x_cm, min_y_cm, max_y_cm)` matches between Tasks 3, 5 and 9; `Site.envelope()/footprint()/tag_map_doc()` match between Tasks 5, 6, 9, 10; `Waypoint`/`Mission`/`axis_move` match between Tasks 9 and 10.
