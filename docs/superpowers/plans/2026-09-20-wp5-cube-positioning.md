# WP5 Cube Positioning Implementation Plan

> **STALE AGAINST SPEC REV 2 (2026-09-20).** The spec moved from six V-panels with
> yaw-multiplexed localisation to a single flat tag line the drone always faces. Tasks **2, 3, 4,
> 5 and 7 remain valid as written.** Tasks **1** (panels), **6** (protocol text), **8**
> (navigator), **9–10** (mission/CLI) and **11** (observe) are superseded and must be rewritten
> once the operator approves rev 2. Do not execute the superseded tasks. Note that the test
> fixtures of Tasks 2 and 5 import Task 1's `panel_tags`; the rewrite replaces those with
> explicit tag dictionaries.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Host binding (operator preference): on Claude Code run it through the `Workflow` tool as dev → review → fix per task, on Sonnet; physical gates stay with the operator.

**Goal:** Position the Tello at commanded `(x, y)` points inside a 3.0 × 2.5 × 2.0 m operating cube and measure arrival error, using rigid two-tag panels, joint multi-tag PnP, stationary localisation bursts, and an SR-063-compliant open-loop mission with shadow correction.

**Architecture:** Part A hardens the pose stack (panel → tag-map generator, joint PnP in the detector, burst aggregation, containment check, a map-QA tool) and needs no approvals. Part B adds a cube navigator and a mission runner that subclasses the existing `FlightTestRunner`, so preflight, emergency latch and abort policy are inherited unchanged. Pose never commands motion: corrections are computed and logged, never sent.

**Tech Stack:** Python 3, OpenCV (`cv2.aruco`, `solvePnP` SQPNP + ITERATIVE), NumPy, SQLite, pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-20-wp5-cube-positioning-design.md`

## Global Constraints

- Work only in the worktree `C:\coding\sentinel\.worktrees\wp5-drone-pose`, branch `feat/sentinel-wp5-drone-pose`. **Never push, never merge.**
- Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (use the model actually running).
- Test command: `uv run python -m pytest tests -q`. Baseline is **220 passed**; it must never go down.
- No change to `docs/superpowers/specs/contracts/*` (wire schema, tag-map schema) or `src/sentinel/contracts/pose.py`.
- SR-063: `src/sentinel/runner.py` must never import from `sentinel.pose`. No code path may turn a pose value into a `move`/`rotate` argument.
- Flight commands are limited to `takeoff`, `move` (20–500 cm), `rotate`, `stop`, `land`. No `go`, `curve`, RC.
- Room frame: 1 unit = 10 cm; heading 0° = +x, counter-clockwise positive; tag `z_units` is **metres**.
- Cube: `x ∈ [0, 300] cm`, `y ∈ [0, 250] cm`, `z ∈ [0, 200] cm`. Commanded bounds: `x ∈ [40, 260]`, `y ∈ [40, 210]`, `z ∈ [80, 150]`.
- Reprojection gate: 2.0 px (`MAX_REPROJECTION_ERROR_PX`), per tag and for the joint solve.
- Part B (Tasks 7–11) must not start until the operator has accepted spec decisions **C1(a)** and **C2**.

## File Structure

| File | Responsibility |
|---|---|
| `src/sentinel/pose/panels.py` (new) | panel description → tag-map tag entries |
| `src/sentinel/pose/detector.py` (modify) | drop-not-fail per-tag gating; joint PnP; `joint=` switch |
| `src/sentinel/pose/scoring.py` (modify) | export `circular_median_deg` |
| `src/sentinel/pose/localise.py` (new) | burst of reports → fix / unreliable |
| `src/sentinel/pose/containment.py` (new) | SR-063 AC-2 advisory check |
| `src/sentinel/pose/mapcheck.py` (new) | single-vs-joint comparison + per-panel residual on recorded frames |
| `src/sentinel/runner.py` (modify) | extract `_fly_sequence`; injectable envelope (no behaviour change) |
| `src/sentinel/cube/__init__.py`, `navigator.py` (new) | cardinal-yaw dead reckoning, leg planning, shadow correction |
| `src/sentinel/cube/mission.py` (new) | mission file loader + `CubeMissionRunner` |
| `src/sentinel/cube/observe.py` (new) | observation-pose candidates |
| `src/sentinel/cli.py` (modify) | `pose-panel-map`, `pose-map-check`, `cube-run` |
| `tests/pose/rendering.py` (new) | shared synthetic multi-tag frame renderer |

---

# Part A — pose stack (no approvals needed)

### Task 1: Panel → tag-map generator

**Files:**
- Create: `src/sentinel/pose/panels.py`
- Modify: `src/sentinel/cli.py` (new `pose-panel-map` subcommand)
- Test: `tests/pose/test_panels.py`

**Interfaces:**
- Produces: `panel_tags(panel: dict) -> list[dict]` (two tag-map-v1 tag entries); `build_tag_map(panels_doc: dict, camera_calibration: dict) -> dict` (a full tag-map-v1 document).
- Panel dict keys: `name, kind ("convex"|"concave"), x_m, y_m, z_m, bisector_deg, splay_deg, offset_m, first_tag_id, size_m`. `(x_m, y_m)` is the fold line (ridge for convex, inner corner for concave); `bisector_deg` points from the panel into the cube; `offset_m` is the distance from the fold to each tag centre, measured along the face.

- [ ] **Step 1: Write the failing tests**

```python
# tests/pose/test_panels.py
import pytest

from sentinel.pose.panels import build_tag_map, panel_tags
from sentinel.pose.tagmap import TagMap

CALIBRATION = {"width": 960, "height": 720, "fx": 893.0, "fy": 894.0, "cx": 468.0,
               "cy": 354.0, "dist_coeffs": [], "calibration_source": "chessboard"}


def _panel(**over):
    base = {"name": "PANEL_W", "kind": "convex", "x_m": 0.5, "y_m": 4.0, "z_m": 1.0,
            "bisector_deg": 0.0, "splay_deg": 25.0, "offset_m": 0.14,
            "first_tag_id": 0, "size_m": 0.192}
    base.update(over)
    return base


def test_convex_panel_faces_recede_from_the_ridge():
    a, b = panel_tags(_panel())
    assert (a["tag_id"], b["tag_id"]) == (0, 1)
    assert (a["yaw_deg"], b["yaw_deg"]) == (25.0, -25.0)
    # ridge at x = 5.0 units; both faces sit behind it (smaller x), left/right of it in y
    assert a["x_units"] == pytest.approx(4.4083, abs=1e-3)
    assert a["y_units"] == pytest.approx(41.2688, abs=1e-3)
    assert b["x_units"] == pytest.approx(4.4083, abs=1e-3)
    assert b["y_units"] == pytest.approx(38.7312, abs=1e-3)
    assert a["zone_id"] == b["zone_id"] == "PANEL_W"
    assert a["z_units"] == 1.0 and a["size_m"] == 0.192 and a["mounting"] == "WALL"


def test_concave_panel_faces_advance_from_the_corner():
    a, b = panel_tags(_panel(kind="concave"))
    assert a["x_units"] == pytest.approx(5.5917, abs=1e-3)
    assert a["y_units"] == pytest.approx(38.7312, abs=1e-3)
    assert b["y_units"] == pytest.approx(41.2688, abs=1e-3)


def test_room_corner_is_a_45_degree_concave_panel():
    a, b = panel_tags(_panel(kind="concave", x_m=0.0, y_m=0.0, bisector_deg=45.0,
                             splay_deg=45.0, offset_m=0.30))
    # tag a lies on the y=0 wall facing +y; tag b lies on the x=0 wall facing +x
    assert (a["x_units"], a["y_units"], a["yaw_deg"]) == (pytest.approx(3.0), pytest.approx(0.0), 90.0)
    assert (b["x_units"], b["y_units"], b["yaw_deg"]) == (pytest.approx(0.0), pytest.approx(3.0), 0.0)


def test_unknown_kind_and_out_of_room_are_refused():
    with pytest.raises(ValueError, match="kind"):
        panel_tags(_panel(kind="flat"))
    with pytest.raises(ValueError, match="outside"):
        panel_tags(_panel(x_m=0.0, y_m=0.0))  # convex at the origin puts faces at negative x


def test_build_tag_map_is_loadable_and_rejects_duplicate_ids():
    doc = build_tag_map({"schema_version": "cube-panels-v1",
                         "panels": [_panel(), _panel(name="PANEL_E", first_tag_id=2, x_m=2.5,
                                                     bisector_deg=180.0)]}, CALIBRATION)
    tag_map = TagMap(doc)
    assert [tag_map.has_tag(i) for i in range(4)] == [True] * 4
    assert tag_map.calibration_source == "chessboard"
    with pytest.raises(ValueError, match="duplicate"):
        build_tag_map({"schema_version": "cube-panels-v1",
                       "panels": [_panel(), _panel(name="PANEL_E")]}, CALIBRATION)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/pose/test_panels.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sentinel.pose.panels'`

- [ ] **Step 3: Implement**

```python
# src/sentinel/pose/panels.py
"""Rigid two-tag panels -> tag-map-v1 tag entries.

A panel is a pair of tags splayed +/- ``splay_deg`` about a vertical fold.
Its internal geometry is measured once on a table; only the fold position
and the bisector direction are measured in the room.
"""
import math

ROOM_SCALE_M_PER_UNIT = 0.1
_KINDS = ("convex", "concave")


def panel_tags(panel: dict) -> list[dict]:
    """The two tag-map-v1 tag entries of one panel (ids first_tag_id, +1)."""
    kind = panel["kind"]
    if kind not in _KINDS:
        raise ValueError(f"panel kind must be one of {_KINDS}, got {kind!r}")
    tags = []
    for k, sign in enumerate((1.0, -1.0)):
        normal = panel["bisector_deg"] + sign * panel["splay_deg"]
        # convex faces recede from the ridge; concave faces advance from the corner
        along = normal + sign * 90.0 if kind == "convex" else normal - sign * 90.0
        x_m = panel["x_m"] + panel["offset_m"] * math.cos(math.radians(along))
        y_m = panel["y_m"] + panel["offset_m"] * math.sin(math.radians(along))
        x_units = round(x_m / ROOM_SCALE_M_PER_UNIT, 4)
        y_units = round(y_m / ROOM_SCALE_M_PER_UNIT, 4)
        if not (0.0 <= x_units <= 100.0 and 0.0 <= y_units <= 100.0):
            raise ValueError(
                f"panel {panel['name']!r} tag {panel['first_tag_id'] + k} lands outside the "
                f"room at ({x_units}, {y_units}) units")
        tags.append({
            "tag_id": panel["first_tag_id"] + k,
            "zone_id": panel["name"],
            "x_units": x_units,
            "y_units": y_units,
            "z_units": panel["z_m"],
            "yaw_deg": round((normal + 180.0) % 360.0 - 180.0, 4),
            "size_m": panel["size_m"],
            "mounting": "WALL",
        })
    return tags


def build_tag_map(panels_doc: dict, camera_calibration: dict) -> dict:
    """A complete tag-map-v1 document from a cube-panels-v1 document."""
    if panels_doc.get("schema_version") != "cube-panels-v1":
        raise ValueError(f"unsupported panels doc: {panels_doc.get('schema_version')!r}")
    tags = [tag for panel in panels_doc["panels"] for tag in panel_tags(panel)]
    ids = [tag["tag_id"] for tag in tags]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate tag ids across panels: {sorted(ids)}")
    return {
        "schema_version": "tag-map-v1",
        "tag_family": "36h11",
        "room": {"width_units": 100, "height_units": 100, "origin": "LEFT_ALLEY_FRONT_CORNER"},
        "room_scale_m_per_unit": ROOM_SCALE_M_PER_UNIT,
        "tags": tags,
        "camera_calibration": camera_calibration,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run python -m pytest tests/pose/test_panels.py -q` → Expected: 5 passed

- [ ] **Step 5: Add the CLI subcommand (test first)**

Append to `tests/test_cli_pose_stream.py`:

```python
def test_pose_panel_map_writes_a_loadable_tag_map(tmp_path):
    import json
    from sentinel.cli import main
    from sentinel.pose.tagmap import load_tag_map

    panels = tmp_path / "panels.json"
    panels.write_text(json.dumps({"schema_version": "cube-panels-v1", "panels": [{
        "name": "PANEL_SW", "kind": "concave", "x_m": 0.0, "y_m": 0.0, "z_m": 0.85,
        "bisector_deg": 45.0, "splay_deg": 45.0, "offset_m": 0.30,
        "first_tag_id": 0, "size_m": 0.192}]}))
    out = tmp_path / "cube-map.json"
    code = main(["pose-panel-map", "--panels", str(panels),
                 "--calibration-from", "calibration/tag-map-calibrated.json", "--out", str(out)])
    assert code == 0
    tag_map = load_tag_map(str(out))
    assert tag_map.has_tag(0) and tag_map.has_tag(1)
    assert tag_map.calibration_source == "chessboard"
```

In `src/sentinel/cli.py`, add the import `from .pose.panels import build_tag_map`, register the parser next to `pose_score` inside `build_parser()`:

```python
    panel_map = subparsers.add_parser(
        "pose-panel-map", help="generate a tag-map-v1 file from a cube-panels-v1 file")
    panel_map.add_argument("--panels", type=Path, required=True)
    panel_map.add_argument("--calibration-from", type=Path, required=True,
                           help="existing tag map whose camera_calibration is reused")
    panel_map.add_argument("--out", type=Path, required=True)
```

add the handler above `main`:

```python
def _run_pose_panel_map(args: Any) -> int:
    try:
        panels_doc = json.loads(args.panels.read_text(encoding="utf-8"))
        calibration = json.loads(
            args.calibration_from.read_text(encoding="utf-8"))["camera_calibration"]
        doc = build_tag_map(panels_doc, calibration)
    except (OSError, ValueError, KeyError) as exc:
        print(f"pose-panel-map failed: {exc}", flush=True)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"pose-panel-map: wrote {len(doc['tags'])} tags to {args.out}")
    return 0
```

and dispatch in `main()` directly after the `pose-score` branch:

```python
    if args.command == "pose-panel-map":
        return _run_pose_panel_map(args)
```

(`json` is already imported in `cli.py`; if not, add `import json`.)

- [ ] **Step 6: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 226 passed

```bash
git add src/sentinel/pose/panels.py src/sentinel/cli.py tests/pose/test_panels.py tests/test_cli_pose_stream.py
git commit -m "feat(pose): rigid two-tag panels generate the tag map" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Joint multi-tag PnP in the detector

The code in Step 3 was spiked against the full suite on 2026-09-20 (220 green, truth recovered to 1.7 cm on a rendered V-panel, wrong-map fallback works). Transcribe it exactly.

**Files:**
- Create: `tests/pose/rendering.py`, `tests/pose/test_joint_pnp.py`
- Modify: `src/sentinel/pose/detector.py` (imports; replace `_solve_mapped_tags`; add `_reprojection_error`, `_solve_one`, `_room_corners`, `_solve_joint`; rewrite the body of `estimate_pose`; one-line change in `estimate_all`)

**Interfaces:**
- Consumes: `panel_tags` from Task 1 (tests only).
- Produces: `estimate_pose(frame, tag_map, *, run_id, report_id, source_kind="SIMULATED", joint=True) -> DronePoseReport`. With ≥ 2 solved tags and `joint=True`, `tag_observations` lists every tag used and `reprojection_error_px` is the joint error. `_solve_mapped_tags` now returns 5-tuples `(err, tag, rvec, tvec, corners_4x2)`.
- Test helper: `tests/pose/rendering.py` exports `K, DIST, SIZE_M, SCALE, make_doc(tags), render(tags, cam_pos_m, heading_deg)`.

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
        # tag frame for rendering: x right when facing the tag, y up, z into the wall
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
"""Joint multi-tag PnP over a splayed two-tag panel."""
import math

import numpy as np
import pytest

from sentinel.pose import detector
from sentinel.pose.detector import estimate_pose
from sentinel.pose.panels import panel_tags
from sentinel.pose.tagmap import TagMap
from tests.pose.rendering import SCALE, SIZE_M, make_doc, render

PANEL = {"name": "PANEL_W", "kind": "convex", "x_m": 0.5, "y_m": 4.0, "z_m": 1.0,
         "bisector_deg": 0.0, "splay_deg": 25.0, "offset_m": 0.14,
         "first_tag_id": 0, "size_m": SIZE_M}


def _scene(range_m=1.5, off_axis_deg=20.0):
    tags = panel_tags(PANEL)
    a = math.radians(off_axis_deg)
    cam = np.array([0.5 + range_m * math.cos(a), 4.0 + range_m * math.sin(a), 1.0])
    heading = math.degrees(math.atan2(4.0 - cam[1], 0.5 - cam[0]))
    return tags, cam, heading, render(tags, cam, heading)


def _error_units(report, cam):
    return math.hypot(report.point[0] - cam[0] / SCALE, report.point[1] - cam[1] / SCALE)


def test_joint_solve_uses_both_tags_and_recovers_truth():
    tags, cam, heading, frame = _scene()
    report = estimate_pose(frame, TagMap(make_doc(tags)), run_id="r", report_id="p")
    assert sorted(o.tag_id for o in report.tag_observations) == [0, 1]
    assert _error_units(report, cam) <= 0.5          # 5 cm; the spike measured 1.7 cm
    assert abs((report.heading_deg - heading + 180.0) % 360.0 - 180.0) <= 2.0
    assert report.reprojection_error_px <= 2.0
    assert report.method == "APRILTAG_36H11_PNP"


def test_joint_false_keeps_the_single_tag_path():
    tags, cam, _, frame = _scene()
    report = estimate_pose(frame, TagMap(make_doc(tags)), run_id="r", report_id="p", joint=False)
    assert len(report.tag_observations) == 1
    assert _error_units(report, cam) <= 1.0


def test_joint_gate_falls_back_to_the_best_single_tag_on_a_wrong_map():
    tags, cam, _, frame = _scene()
    wrong = [dict(tags[0]), dict(tags[1], y_units=tags[1]["y_units"] - 3.0)]  # 30 cm map error
    report = estimate_pose(frame, TagMap(make_doc(wrong)), run_id="r", report_id="p")
    assert len(report.tag_observations) == 1
    assert report.reprojection_error_px <= 2.0


def test_a_tag_that_fails_its_gate_is_dropped_not_fatal(monkeypatch):
    tags, cam, _, frame = _scene()
    real = detector._solve_one
    monkeypatch.setattr(detector, "_solve_one",
                        lambda tag, corners, tm: None if tag.tag_id == 1 else real(tag, corners, tm))
    report = estimate_pose(frame, TagMap(make_doc(tags)), run_id="r", report_id="p")
    assert [o.tag_id for o in report.tag_observations] == [0]


def test_every_tag_dropped_fails_closed(monkeypatch):
    tags, _, _, frame = _scene()
    monkeypatch.setattr(detector, "_solve_one", lambda tag, corners, tm: None)
    with pytest.raises(ValueError, match="no mapped tag passed"):
        estimate_pose(frame, TagMap(make_doc(tags)), run_id="r", report_id="p")
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run python -m pytest tests/pose/test_joint_pnp.py -q`
Expected: FAIL — `TypeError: estimate_pose() got an unexpected keyword argument 'joint'` and `AttributeError: ... '_solve_one'`. (If `tests.pose.rendering` cannot be imported, confirm `tests/__init__.py` and `tests/pose/__init__.py` exist — they do.)

- [ ] **Step 4: Implement in `src/sentinel/pose/detector.py`**

4a. Imports and logger — replace the top of the file's import block:

```python
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

Also update the module docstring's first line to `"""AprilTag 36h11 detector + single-tag and joint multi-tag PnP pose estimation (fail-closed)."""`.

- [ ] **Step 5: Run to verify pass, whole suite**

Run: `uv run python -m pytest tests -q` → Expected: 231 passed

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/pose/detector.py tests/pose/rendering.py tests/pose/test_joint_pnp.py
git commit -m "feat(pose): joint multi-tag PnP; a failed tag is dropped, not fatal" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Stationary localisation burst

**Files:**
- Modify: `src/sentinel/pose/scoring.py` (add `circular_median_deg`, use it in `score_runs`)
- Create: `src/sentinel/pose/localise.py`
- Test: `tests/pose/test_localise.py`

**Interfaces:**
- Produces: `circular_median_deg(values: Sequence[float]) -> float` in `[-180, 180)`;
  `LocalisationFix(x_units: float, y_units: float, heading_deg: float, n: int, spread_p95_cm: float)`;
  `LocalisationUnreliable(reason: str, n: int)`;
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
        tag_observations=(TagObservation(0, "PANEL_W", 1.0, 0.5),), reprojection_error_px=0.5)


def test_circular_median_is_wrap_safe():
    assert abs(abs(circular_median_deg([179.0, -179.0, 178.5, -178.5])) - 180.0) <= 0.5
    assert circular_median_deg([10.0, 12.0, 11.0]) == 11.0


def test_fix_is_the_componentwise_median_with_spread():
    reports = [_report(15.0 + 0.01 * i, 12.0, 90.0) for i in range(9)]
    fix = localise(reports)
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
    reports = [_report(15.0 + i, 12.0, 90.0) for i in range(10)]  # 10 cm steps
    result = localise(reports)
    assert isinstance(result, LocalisationUnreliable) and "spread" in result.reason
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run python -m pytest tests/pose/test_localise.py -q` → Expected: FAIL — `ModuleNotFoundError: sentinel.pose.localise`

- [ ] **Step 3: Implement**

In `src/sentinel/pose/scoring.py`, add directly below `heading_delta_deg`:

```python
def circular_median_deg(values) -> float:
    """Median heading in [-180, 180), safe across the +/-180 wrap."""
    values = [float(v) for v in values]
    deltas = [heading_delta_deg(v, values[0]) for v in values]
    return (values[0] + statistics.median(deltas) + 180.0) % 360.0 - 180.0
```

and in `score_runs` replace the two lines computing `heading_deltas_from_first` and `median_heading` with:

```python
            median_heading = circular_median_deg(hs)
```

Create the module:

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

- [ ] **Step 4: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 236 passed

```bash
git add src/sentinel/pose/scoring.py src/sentinel/pose/localise.py tests/pose/test_localise.py
git commit -m "feat(pose): stationary burst localisation with a spread gate" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Cube containment check (SR-063 AC-2)

**Files:**
- Create: `src/sentinel/pose/containment.py`
- Test: `tests/pose/test_containment.py` (the marker SR-063 AC-2 already names)

**Interfaces:**
- Produces: `Containment` enum (`INSIDE`, `MARGIN`, `OUTSIDE`); `CubeFootprint(width_cm=300.0, length_cm=250.0)`; `check_containment(x_cm: float, y_cm: float, footprint: CubeFootprint = CubeFootprint(), *, margin_cm: float = 30.0) -> Containment`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/pose/test_containment.py
"""SR-063 AC-2: advisory containment; it classifies, it never moves anything."""
import inspect

import pytest

from sentinel.pose import containment
from sentinel.pose.containment import Containment, CubeFootprint, check_containment


@pytest.mark.parametrize("x, y, expected", [
    (150.0, 125.0, Containment.INSIDE),
    (30.0, 125.0, Containment.INSIDE),      # exactly on the margin line counts as inside
    (29.9, 125.0, Containment.MARGIN),
    (150.0, 230.0, Containment.MARGIN),
    (0.0, 0.0, Containment.MARGIN),         # on the boundary is still in the cube
    (-0.1, 125.0, Containment.OUTSIDE),
    (150.0, 250.1, Containment.OUTSIDE),
])
def test_classification(x, y, expected):
    assert check_containment(x, y) is expected


def test_custom_footprint_and_margin():
    footprint = CubeFootprint(width_cm=100.0, length_cm=100.0)
    assert check_containment(50.0, 50.0, footprint, margin_cm=10.0) is Containment.INSIDE
    assert check_containment(95.0, 50.0, footprint, margin_cm=10.0) is Containment.MARGIN


def test_module_cannot_command_motion():
    source = inspect.getsource(containment)
    for forbidden in ("client", ".move(", ".rotate(", "takeoff", "sentinel.runner"):
        assert forbidden not in source
```

- [ ] **Step 2: Run to verify failure** — `uv run python -m pytest tests/pose/test_containment.py -q` → `ModuleNotFoundError`

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
    width_cm: float = 300.0   # room x
    length_cm: float = 250.0  # room y


def check_containment(
    x_cm: float,
    y_cm: float,
    footprint: CubeFootprint = CubeFootprint(),
    *,
    margin_cm: float = 30.0,
) -> Containment:
    if not (0.0 <= x_cm <= footprint.width_cm and 0.0 <= y_cm <= footprint.length_cm):
        return Containment.OUTSIDE
    if (margin_cm <= x_cm <= footprint.width_cm - margin_cm
            and margin_cm <= y_cm <= footprint.length_cm - margin_cm):
        return Containment.INSIDE
    return Containment.MARGIN
```

- [ ] **Step 4: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 245 passed

```bash
git add src/sentinel/pose/containment.py tests/pose/test_containment.py
git commit -m "feat(pose): advisory cube containment check (SR-063 AC-2)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `pose-map-check` — single vs joint on recorded frames, per-panel residual

This is the Stage 1 instrument: it answers "did joint PnP help on *real* frames?" and "is a panel mis-measured?" without any ground truth.

**Files:**
- Create: `src/sentinel/pose/mapcheck.py`
- Modify: `src/sentinel/cli.py`
- Test: `tests/pose/test_mapcheck.py`

**Interfaces:**
- Consumes: `estimate_pose(..., joint=)` (Task 2).
- Produces: `compare_frames(frames: Iterable[np.ndarray], tag_map: TagMap) -> dict` with keys `frames`, `multi_tag_frames`, `single` and `joint` (each `{"n", "spread_p95_cm", "median_x_units", "median_y_units"}`), and `panels` (`{zone_id: {"n", "median_joint_reprojection_px"}}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/pose/test_mapcheck.py
import math

import numpy as np

from sentinel.pose.mapcheck import compare_frames
from sentinel.pose.panels import panel_tags
from sentinel.pose.tagmap import TagMap
from tests.pose.rendering import SIZE_M, make_doc, render

PANEL = {"name": "PANEL_W", "kind": "convex", "x_m": 0.5, "y_m": 4.0, "z_m": 1.0,
         "bisector_deg": 0.0, "splay_deg": 25.0, "offset_m": 0.14,
         "first_tag_id": 0, "size_m": SIZE_M}


def test_compare_frames_reports_both_paths_and_the_panel_residual():
    tags = panel_tags(PANEL)
    cam = np.array([2.0, 4.0, 1.0])
    frames = [render(tags, cam, 180.0) for _ in range(3)]
    frames.append(np.full((720, 960, 3), 255, dtype=np.uint8))  # blank: skipped
    result = compare_frames(frames, TagMap(make_doc(tags)))
    assert result["frames"] == 4 and result["multi_tag_frames"] == 3
    assert result["single"]["n"] == 3 and result["joint"]["n"] == 3
    assert result["joint"]["spread_p95_cm"] == 0.0  # identical frames
    assert math.isclose(result["joint"]["median_x_units"], 20.0, abs_tol=0.5)
    assert result["panels"]["PANEL_W"]["n"] == 3
    assert result["panels"]["PANEL_W"]["median_joint_reprojection_px"] <= 2.0
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.pose.mapcheck`

- [ ] **Step 3: Implement**

```python
# src/sentinel/pose/mapcheck.py
"""Truth-free tag-map QA on recorded frames: single-tag vs joint PnP spread,
and the joint reprojection residual per panel (a mis-measured panel shows up
as a persistently high residual)."""
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
        "panels": {zone: {"n": len(v), "median_joint_reprojection_px": statistics.median(v)}
                   for zone, v in sorted(residuals.items())},
    }
```

- [ ] **Step 4: CLI.** In `cli.py` import `from .pose.mapcheck import compare_frames`, register:

```python
    map_check = subparsers.add_parser(
        "pose-map-check", help="offline: single-tag vs joint PnP spread and per-panel residual")
    map_check.add_argument("--frames", type=Path, required=True, help="directory of recorded PNG frames")
    map_check.add_argument("--tag-map", type=Path, required=True)
```

handler:

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
    for zone, row in result["panels"].items():
        flag = "  <- re-measure this panel" if row["median_joint_reprojection_px"] > 1.0 else ""
        print(f"  panel {zone}: joint reprojection median "
              f"{row['median_joint_reprojection_px']:.2f}px over {row['n']} frames{flag}")
    return 0 if result["multi_tag_frames"] else 1
```

dispatch in `main()`: `if args.command == "pose-map-check": return _run_pose_map_check(args)`.

Add to `tests/test_cli_pose_stream.py`:

```python
def test_pose_map_check_fails_cleanly_on_an_empty_directory(tmp_path, capsys):
    from sentinel.cli import main
    code = main(["pose-map-check", "--frames", str(tmp_path),
                 "--tag-map", "calibration/tag-map-calibrated.json"])
    assert code == 1 and "no .png frames" in capsys.readouterr().out
```

- [ ] **Step 5: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 247 passed

```bash
git add src/sentinel/pose/mapcheck.py src/sentinel/cli.py tests/pose/test_mapcheck.py tests/test_cli_pose_stream.py
git commit -m "feat(pose): pose-map-check compares single vs joint PnP and flags bad panels" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Stage 1 protocol and panel build notes

**Files:**
- Modify: `docs/wp5-rehearsal-manifest.md` (append one section)

- [ ] **Step 1: Append this section verbatim to the end of the manifest**

```markdown
## Cube positioning — Stage 1 static protocol (2026-09-20 design)

Design: `docs/superpowers/specs/2026-09-20-wp5-cube-positioning-design.md`. Motors off throughout;
`pose-stream` never takes off, so no G3/G4 approval is needed for this stage.

**Build one panel first.** Two tags on a rigid fold (foam board). On a table, measure: tag edge
length (`size_m`, printers scale ~96%), fold-to-tag-centre distance along the face (`offset_m`),
and the splay half-angle (`splay_deg`, target 25 for a wedge; a room corner is a concave panel
with splay 45). In the room, measure only the fold position (`x_m`, `y_m`), the tag-centre height
(`z_m`, target 0.85) and the bisector direction (`bisector_deg`, 0 = +x, counter-clockwise).
Write `artifacts/panels.json` (`cube-panels-v1`) and generate the map:

    uv run sentinel pose-panel-map --panels artifacts/panels.json --calibration-from calibration/tag-map-calibrated.json --out artifacts/cube-map.json

**Capture.** Drone on a stand, lens height 0.8-1.2 m, at 6-8 taped points spanning 0.5-2.0 m from
the panel and 0-30 degrees off its bisector; truth measured to the lens. Per point:

    uv run sentinel pose-stream --real --confirm --tag-map artifacts/cube-map.json --db artifacts/cube_s1.db --duration 20 --record-dir artifacts/frames_s1_<point> --preview

**Score.** `pose-score` against a truth file, ladder p95 <= 15 cm / 10 deg, then 10 / 5, then 5 / 3,
with `--min-samples 15`. Then, per recorded directory, the truth-free comparison:

    uv run sentinel pose-map-check --frames artifacts/frames_s1_<point> --tag-map artifacts/cube-map.json

Record both spreads. The design's central claim (spec E5) holds if the joint spread is no worse
than the single-tag spread at every point and clearly better off-bisector. A panel whose joint
reprojection median exceeds 1 px has been mis-measured: fix the map, not the detector.

**Then build the remaining five panels** (four cube corners + the midpoints of the two 3.0 m
sides) and repeat `pose-map-check` once per panel.
```

- [ ] **Step 2: Commit**

```bash
git add docs/wp5-rehearsal-manifest.md
git commit -m "docs(wp5): Stage 1 static protocol for the cube panels" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

**Part A exit gate (operator):** Stage 1 physical run. Do not start Part B on the strength of synthetic tests alone.

---

# Part B — cube flight (requires operator acceptance of spec C1(a) and C2)

### Task 7: Runner refactor — overridable flight sequence, injectable envelope

Pure refactor: zero behaviour change, every existing runner test must stay green untouched.

**Files:**
- Modify: `src/sentinel/runner.py` (`__init__` signature; extract `_fly_sequence`)
- Test: `tests/test_runner.py` (append)

**Interfaces:**
- Produces: `FlightTestRunner(..., envelope: FlightEnvelope | None = None)`; `FlightTestRunner._fly_sequence(self) -> None`, called by `run()` between takeoff and hover/stop.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_runner.py`; `_FakeClient` already exists in that file)

```python
def test_envelope_is_injectable():
    from sentinel.envelope import FlightEnvelope
    envelope = FlightEnvelope(min_z_cm=80, max_z_cm=150)
    runner = FlightTestRunner(_FakeClient(), "dry-run", envelope=envelope)
    assert runner.envelope is envelope


def test_fly_sequence_is_the_override_point():
    calls = []

    class _Custom(FlightTestRunner):
        def _fly_sequence(self):
            calls.append("custom")

    client = _FakeClient(height=200)
    report = _Custom(client, "dry-run").run(confirm=True)
    assert calls == ["custom"] and report.status == "PASS"
    commands = [r.command for r in client.records]
    assert "forward 50" not in commands and "land" in commands
```

If `_FakeClient` records moves under a different string than `"forward 50"`, read its `move` method and adapt the two literals; do not change the fake.

- [ ] **Step 2: Run to verify failure** — `TypeError: ... unexpected keyword argument 'envelope'`

- [ ] **Step 3: Implement**

In `__init__`, add the keyword-only parameter `envelope: FlightEnvelope | None = None` after `video_only_duration_s`, and replace `self.envelope = FlightEnvelope()` with:

```python
        self.envelope = envelope if envelope is not None else FlightEnvelope()
```

Add this method directly above `run`:

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

In `run()`, replace the block from `self._enter_altitude_band()` through the counter-clockwise rotation failure check (the lines now duplicated in `_fly_sequence`) with the single line:

```python
                self._fly_sequence()
```

The hover/stop and landing code that follows stays in `run()`.

- [ ] **Step 4: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 249 passed, and `grep -n "sentinel.pose" src/sentinel/runner.py` prints nothing.

```bash
git add src/sentinel/runner.py tests/test_runner.py
git commit -m "refactor(runner): overridable _fly_sequence and injectable envelope" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Cube navigator

**Files:**
- Create: `src/sentinel/cube/__init__.py` (empty), `src/sentinel/cube/navigator.py`
- Test: `tests/cube/__init__.py` (empty), `tests/cube/test_navigator.py`

**Interfaces:**
- Produces:
  - `Command(kind: str, direction: str, amount: int)` — `kind` is `"move"` (direction ∈ forward/back/left/right, amount cm) or `"rotate"` (direction ∈ cw/ccw, amount degrees).
  - `CubeBounds(min_x_cm=40, max_x_cm=260, min_y_cm=40, max_y_cm=210)` with `.contains(x, y)`.
  - `PlanError(ValueError)`.
  - `CubeNavigator(bounds, x_cm, y_cm, yaw_deg)` with attributes `x_cm, y_cm, yaw_deg`; `plan_leg(tx, ty) -> list[Command]`; `rotation_to(target_yaw_deg) -> Command | None`; `apply(command) -> None`.
  - `bearing_deg(from_xy, to_xy) -> int`; `propose_correction(target_xy, measured_xy, cardinal_yaw_deg, bounds) -> list[Command]`.
- Yaw: degrees, 0 = +x, counter-clockwise positive, stored in `[0, 360)`. Tello `ccw` increases yaw.

- [ ] **Step 1: Write the failing tests**

```python
# tests/cube/test_navigator.py
import pytest

from sentinel.cube.navigator import (
    Command, CubeBounds, CubeNavigator, PlanError, bearing_deg, propose_correction,
)

BOUNDS = CubeBounds()


def test_leg_at_yaw_90_maps_body_axes_to_room_axes():
    nav = CubeNavigator(BOUNDS, 150, 60, 90)          # facing +y
    assert nav.plan_leg(200, 125) == [Command("move", "right", 50), Command("move", "forward", 65)]


def test_leg_at_yaw_180():
    nav = CubeNavigator(BOUNDS, 150, 100, 180)        # facing -x
    assert nav.plan_leg(100, 150) == [Command("move", "forward", 50), Command("move", "right", 50)]


def test_apply_tracks_position_and_yaw():
    nav = CubeNavigator(BOUNDS, 150, 60, 90)
    for command in nav.plan_leg(200, 125):
        nav.apply(command)
    assert (nav.x_cm, nav.y_cm) == (200, 125)
    nav.apply(Command("rotate", "cw", 90))
    assert nav.yaw_deg == 0
    nav.apply(Command("rotate", "ccw", 135))
    assert nav.yaw_deg == 135


def test_leg_refused_below_min_move_out_of_bounds_or_off_cardinal():
    nav = CubeNavigator(BOUNDS, 150, 60, 90)
    with pytest.raises(PlanError, match="20"):
        nav.plan_leg(160, 60)
    with pytest.raises(PlanError, match="bounds"):
        nav.plan_leg(150, 240)
    nav.apply(Command("rotate", "ccw", 30))
    with pytest.raises(PlanError, match="cardinal"):
        nav.plan_leg(150, 100)
    with pytest.raises(PlanError, match="cardinal"):
        nav.apply(Command("move", "forward", 50))


def test_start_outside_bounds_is_refused():
    with pytest.raises(PlanError, match="bounds"):
        CubeNavigator(BOUNDS, 10, 60, 90)


def test_rotation_takes_the_short_way():
    nav = CubeNavigator(BOUNDS, 150, 60, 90)
    assert nav.rotation_to(90) is None
    assert nav.rotation_to(180) == Command("rotate", "ccw", 90)
    assert nav.rotation_to(350) == Command("rotate", "cw", 100)
    assert nav.rotation_to(270) == Command("rotate", "ccw", 180)


def test_bearing():
    assert bearing_deg((150, 125), (150, 250)) == 90
    assert bearing_deg((150, 125), (0, 125)) == 180
    assert bearing_deg((150, 125), (300, 0)) == 320


def test_shadow_correction_rules():
    # 8 cm error: inside the dead band, nothing proposed
    assert propose_correction((150, 125), (158, 125), 90, BOUNDS) == []
    # 14 cm short in +y at yaw 90: one 20 cm forward move (residual 6 cm)
    assert propose_correction((150, 125), (150, 111), 90, BOUNDS) == [Command("move", "forward", 20)]
    # 35 cm error in -x at yaw 90 (left is -x)
    assert propose_correction((150, 125), (185, 125), 90, BOUNDS) == [Command("move", "left", 35)]
    # a correction that would leave the commanded bounds is not proposed
    assert propose_correction((250, 125), (255, 125), 90, BOUNDS) == []
    assert propose_correction((260, 125), (245, 125), 90, BOUNDS) == []
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.cube`

- [ ] **Step 3: Implement**

```python
# src/sentinel/cube/navigator.py
"""Open-loop dead reckoning inside the operating cube.

Commanded state only: nothing here reads a pose. Translation is permitted
only at cardinal yaw, so body-axis moves stay axis-aligned in the room.
"""
import math
from dataclasses import dataclass

MIN_MOVE_CM = 20
MAX_MOVE_CM = 500
CARDINALS = (0, 90, 180, 270)
CORRECTION_DEAD_BAND_CM = 10


class PlanError(ValueError):
    """A commanded leg or rotation that the cube rules do not allow."""


@dataclass(frozen=True)
class Command:
    kind: str        # "move" | "rotate"
    direction: str   # forward/back/left/right | cw/ccw
    amount: int      # cm | degrees


@dataclass(frozen=True)
class CubeBounds:
    """Commanded x/y bounds: the cube inset by the drift and tag-standoff margin."""

    min_x_cm: int = 40
    max_x_cm: int = 260
    min_y_cm: int = 40
    max_y_cm: int = 210

    def contains(self, x_cm: float, y_cm: float) -> bool:
        return self.min_x_cm <= x_cm <= self.max_x_cm and self.min_y_cm <= y_cm <= self.max_y_cm


def _body_vectors(yaw_deg: int) -> dict[str, tuple[int, int]]:
    """Room-axis unit vector of each body direction at a cardinal yaw."""
    fx = round(math.cos(math.radians(yaw_deg)))
    fy = round(math.sin(math.radians(yaw_deg)))
    return {"forward": (fx, fy), "back": (-fx, -fy), "left": (-fy, fx), "right": (fy, -fx)}


def _direction_for(yaw_deg: int, vector: tuple[int, int]) -> str:
    for name, v in _body_vectors(yaw_deg).items():
        if v == vector:
            return name
    raise PlanError(f"no body direction for {vector} at yaw {yaw_deg}")


def bearing_deg(from_xy, to_xy) -> int:
    """Room bearing from one point to another, integer degrees in [0, 360)."""
    return round(math.degrees(math.atan2(to_xy[1] - from_xy[1], to_xy[0] - from_xy[0]))) % 360


def _axis_moves(yaw_deg: int, dx: int, dy: int) -> list[Command]:
    commands = []
    for delta, vector in ((dx, (1 if dx > 0 else -1, 0)), (dy, (0, 1 if dy > 0 else -1))):
        if delta == 0:
            continue
        if not MIN_MOVE_CM <= abs(delta) <= MAX_MOVE_CM:
            raise PlanError(
                f"axis move of {abs(delta)} cm is outside {MIN_MOVE_CM}-{MAX_MOVE_CM} cm")
        commands.append(Command("move", _direction_for(yaw_deg, vector), abs(delta)))
    return commands


class CubeNavigator:
    def __init__(self, bounds: CubeBounds, x_cm: int, y_cm: int, yaw_deg: int) -> None:
        if not bounds.contains(x_cm, y_cm):
            raise PlanError(f"start ({x_cm}, {y_cm}) is outside the commanded bounds")
        self.bounds = bounds
        self.x_cm, self.y_cm, self.yaw_deg = int(x_cm), int(y_cm), int(yaw_deg) % 360

    def plan_leg(self, target_x_cm: int, target_y_cm: int) -> list[Command]:
        """Axis-aligned moves (x first, then y) to the target; validates every endpoint."""
        if self.yaw_deg not in CARDINALS:
            raise PlanError(f"translation requires a cardinal yaw, not {self.yaw_deg}")
        dx, dy = int(target_x_cm) - self.x_cm, int(target_y_cm) - self.y_cm
        commands = _axis_moves(self.yaw_deg, dx, dy)
        for x, y in ((self.x_cm + dx, self.y_cm), (self.x_cm + dx, self.y_cm + dy)):
            if not self.bounds.contains(x, y):
                raise PlanError(f"leg endpoint ({x}, {y}) is outside the commanded bounds")
        return commands

    def rotation_to(self, target_yaw_deg: int) -> Command | None:
        delta = (int(target_yaw_deg) - self.yaw_deg + 180) % 360 - 180
        if delta == 0:
            return None
        if delta == -180:
            delta = 180
        return Command("rotate", "ccw" if delta > 0 else "cw", abs(delta))

    def apply(self, command: Command) -> None:
        """Commit a command to the dead-reckoned state."""
        if command.kind == "rotate":
            sign = 1 if command.direction == "ccw" else -1
            self.yaw_deg = (self.yaw_deg + sign * command.amount) % 360
            return
        if self.yaw_deg not in CARDINALS:
            raise PlanError(f"translation requires a cardinal yaw, not {self.yaw_deg}")
        vx, vy = _body_vectors(self.yaw_deg)[command.direction]
        x, y = self.x_cm + vx * command.amount, self.y_cm + vy * command.amount
        if not self.bounds.contains(x, y):
            raise PlanError(f"move endpoint ({x}, {y}) is outside the commanded bounds")
        self.x_cm, self.y_cm = x, y


def propose_correction(target_xy, measured_xy, cardinal_yaw_deg: int,
                       bounds: CubeBounds) -> list[Command]:
    """The moves that WOULD correct an arrival error. Shadow only: callers log
    this and never send it (SR-063)."""
    commands = []
    x, y = float(measured_xy[0]), float(measured_xy[1])
    for axis, vector_of in ((0, lambda s: (s, 0)), (1, lambda s: (0, s))):
        error = float(target_xy[axis]) - float(measured_xy[axis])
        if abs(error) <= CORRECTION_DEAD_BAND_CM:
            continue
        sign = 1 if error > 0 else -1
        distance = max(MIN_MOVE_CM, round(abs(error)))
        end_x = x + sign * distance if axis == 0 else x
        end_y = y + sign * distance if axis == 1 else y
        if not bounds.contains(end_x, end_y):
            continue
        commands.append(Command("move", _direction_for(cardinal_yaw_deg, vector_of(sign)), distance))
        x, y = end_x, end_y
    return commands
```

- [ ] **Step 4: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 257 passed

```bash
git add src/sentinel/cube tests/cube
git commit -m "feat(cube): cardinal-yaw navigator with shadow correction" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Mission file and `CubeMissionRunner`

**Files:**
- Create: `src/sentinel/cube/mission.py`
- Test: `tests/cube/test_mission.py`

**Interfaces:**
- Consumes: `FlightTestRunner._fly_sequence` / `envelope=` (Task 7); `CubeNavigator`, `CubeBounds`, `Command`, `PlanError`, `bearing_deg`, `propose_correction` (Task 8); `localise`, `LocalisationFix` (Task 3); `check_containment`, `Containment` (Task 4); `run_pose_stream` (existing, `sentinel.pose.stream`).
- Produces:
  - `Waypoint(name: str, x_cm: int, y_cm: int, observe_heading_deg: int)`; `Mission(start_x_cm, start_y_cm, start_yaw_deg, waypoints: tuple[Waypoint, ...])`.
  - `load_mission(path) -> Mission` for `cube-mission-v1` JSON: `{"schema_version": "cube-mission-v1", "start": {"x_cm", "y_cm", "yaw_deg"}, "waypoints": [{"name", "x_cm", "y_cm", "observe_heading_deg"}]}`; 1–8 waypoints; start yaw cardinal.
  - `CubeMissionRunner(client, mode, minimum_battery=40, *, mission, tag_map, conn, run_id, mission_log: Path, burst_fn=None, sleep_fn=time.sleep, **runner_kwargs)`. `burst_fn() -> list[DronePoseReport]` is injectable for tests. Raises `PlanError` from the constructor when the mission is infeasible (nothing has been sent yet).
  - `ContainmentAbort(RuntimeError)`.
  - Log: one JSON object per line: `{"waypoint", "commanded_cm", "fix_cm", "heading_deg", "n", "spread_p95_cm", "arrival_error_cm", "containment", "shadow_correction", "status"}`; `status` is `LOCALISED` or `UNLOCALISED` (then the fix-derived fields are `null`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/cube/test_mission.py
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.cube.mission import CubeMissionRunner, Mission, Waypoint, load_mission
from sentinel.cube.navigator import PlanError
from sentinel.dry_run import DryRunClient
from sentinel.pose.persistence import ensure_schema
from sentinel.pose.tagmap import TagMap

TAG_MAP = TagMap({
    "schema_version": "tag-map-v1", "tag_family": "36h11", "room_scale_m_per_unit": 0.1,
    "room": {"width_units": 100, "height_units": 100, "origin": "LEFT_ALLEY_FRONT_CORNER"},
    "tags": [
        {"tag_id": 0, "zone_id": "PANEL_S", "x_units": 14.0, "y_units": 0.5, "z_units": 0.85,
         "yaw_deg": 115.0, "size_m": 0.192, "mounting": "WALL"},
        {"tag_id": 1, "zone_id": "PANEL_S", "x_units": 16.0, "y_units": 0.5, "z_units": 0.85,
         "yaw_deg": 65.0, "size_m": 0.192, "mounting": "WALL"},
        {"tag_id": 2, "zone_id": "PANEL_N", "x_units": 14.0, "y_units": 24.5, "z_units": 0.85,
         "yaw_deg": -115.0, "size_m": 0.192, "mounting": "WALL"},
        {"tag_id": 3, "zone_id": "PANEL_N", "x_units": 16.0, "y_units": 24.5, "z_units": 0.85,
         "yaw_deg": -65.0, "size_m": 0.192, "mounting": "WALL"},
    ],
    "camera_calibration": {"width": 960, "height": 720, "fx": 893.0, "fy": 894.0, "cx": 468.0,
                           "cy": 354.0, "dist_coeffs": [], "calibration_source": "chessboard"},
})

MISSION = Mission(150, 60, 90, (Waypoint("WP1", 150, 160, 90), Waypoint("WP2", 200, 160, 45)))


def _report(x_units, y_units):
    return DronePoseReport(
        report_id="p", run_id="r", observed_at=datetime.now(timezone.utc),
        source_kind="SIMULATED", point=(x_units, y_units), heading_deg=90.0, altitude_m=None,
        method="APRILTAG_36H11_PNP",
        tag_observations=(TagObservation(2, "PANEL_N", 1.0, 0.5),), reprojection_error_px=0.5)


def _runner(tmp_path, bursts, mission=MISSION):
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    queue = list(bursts)
    client = DryRunClient()
    runner = CubeMissionRunner(
        client, "dry-run", mission=mission, tag_map=TAG_MAP, conn=conn, run_id="r",
        mission_log=tmp_path / "mission.jsonl", burst_fn=lambda: queue.pop(0),
        sleep_fn=lambda s: None)
    return runner, client


def _log(tmp_path):
    return [json.loads(line) for line in (tmp_path / "mission.jsonl").read_text().splitlines()]


def test_mission_flies_open_loop_and_logs_arrival_and_shadow(tmp_path):
    bursts = [[_report(15.0, 14.2)] * 10,      # WP1: 18 cm short in y
              [_report(20.3, 16.1)] * 10]      # WP2: within the dead band
    runner, client = _runner(tmp_path, bursts)
    report = runner.run(confirm=True)
    assert report.status == "PASS"
    commands = list(client.commands)
    assert "forward 100" in commands and "right 50" in commands and commands[-1] == "land"
    wp1, wp2 = _log(tmp_path)
    assert wp1["status"] == "LOCALISED" and wp1["containment"] == "INSIDE"
    assert wp1["arrival_error_cm"] == pytest.approx(18.0, abs=0.1)
    assert wp1["shadow_correction"] == [{"kind": "move", "direction": "forward", "amount": 20}]
    assert wp2["shadow_correction"] == []
    # SR-063: the proposed correction is never transmitted
    assert "forward 20" not in commands


def test_unreliable_burst_marks_the_waypoint_and_continues(tmp_path):
    runner, client = _runner(tmp_path, [[_report(15.0, 16.0)] * 3, [_report(20.0, 16.0)] * 10])
    assert runner.run(confirm=True).status == "PASS"
    wp1, wp2 = _log(tmp_path)
    assert wp1["status"] == "UNLOCALISED" and wp1["fix_cm"] is None
    assert wp1["shadow_correction"] is None and wp2["status"] == "LOCALISED"


def test_fix_outside_the_cube_aborts_with_a_controlled_landing(tmp_path):
    runner, client = _runner(tmp_path, [[_report(15.0, 26.0)] * 10])
    report = runner.run(confirm=True)
    assert report.status == "FAIL" and "containment" in report.error.lower()
    assert "land" in client.commands and "right 50" not in client.commands


def test_infeasible_mission_is_refused_before_anything_is_sent(tmp_path):
    bad = Mission(150, 60, 90, (Waypoint("WP1", 150, 240, 90),))
    with pytest.raises(PlanError):
        _runner(tmp_path, [], mission=bad)


def test_load_mission_validates(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "schema_version": "cube-mission-v1", "start": {"x_cm": 150, "y_cm": 60, "yaw_deg": 90},
        "waypoints": [{"name": "WP1", "x_cm": 150, "y_cm": 160, "observe_heading_deg": 90}]}))
    assert load_mission(path) == Mission(150, 60, 90, (Waypoint("WP1", 150, 160, 90),))
    path.write_text(json.dumps({"schema_version": "cube-mission-v1",
                                "start": {"x_cm": 150, "y_cm": 60, "yaw_deg": 45}, "waypoints": []}))
    with pytest.raises(ValueError):
        load_mission(path)
```

If `DryRunClient.commands` formats a move differently from `"forward 100"`, read `_record` in `src/sentinel/dry_run.py` and adapt the literals in the tests only.

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.cube.mission`

- [ ] **Step 3: Implement**

```python
# src/sentinel/cube/mission.py
"""Open-loop waypoint missions inside the operating cube.

SR-063: pose is measured at each waypoint and logged. It may trip the existing
abort policy through the containment check. It is never turned into a motion
command: the correction it implies is logged as a shadow, not sent.
"""
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from sentinel.cube.navigator import (
    CARDINALS, Command, CubeBounds, CubeNavigator, bearing_deg, propose_correction,
)
from sentinel.envelope import FlightEnvelope
from sentinel.models import Position
from sentinel.pose.containment import Containment, CubeFootprint, check_containment
from sentinel.pose.localise import LocalisationFix, localise
from sentinel.pose.stream import run_pose_stream
from sentinel.runner import FlightTestRunner

MAX_WAYPOINTS = 8
SETTLE_S = 1.5
BURST_MAX_S = 12.0
BURST_TARGET_REPORTS = 12
CM_PER_UNIT = 10.0


class ContainmentAbort(RuntimeError):
    """The measured position left the operating cube."""


@dataclass(frozen=True)
class Waypoint:
    name: str
    x_cm: int
    y_cm: int
    observe_heading_deg: int


@dataclass(frozen=True)
class Mission:
    start_x_cm: int
    start_y_cm: int
    start_yaw_deg: int
    waypoints: tuple[Waypoint, ...]


def load_mission(path) -> Mission:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema_version") != "cube-mission-v1":
        raise ValueError(f"unsupported mission file: {doc.get('schema_version')!r}")
    start = doc["start"]
    if start["yaw_deg"] not in CARDINALS:
        raise ValueError(f"start yaw must be one of {CARDINALS}, got {start['yaw_deg']}")
    waypoints = tuple(Waypoint(w["name"], int(w["x_cm"]), int(w["y_cm"]),
                               int(w["observe_heading_deg"])) for w in doc["waypoints"])
    if not 1 <= len(waypoints) <= MAX_WAYPOINTS:
        raise ValueError(f"a mission has 1-{MAX_WAYPOINTS} waypoints, got {len(waypoints)}")
    return Mission(int(start["x_cm"]), int(start["y_cm"]), int(start["yaw_deg"]), waypoints)


def cube_envelope() -> FlightEnvelope:
    return FlightEnvelope(min_x_cm=0, max_x_cm=300, min_y_cm=0, max_y_cm=250,
                          min_z_cm=80, max_z_cm=150)


def _panel_centres_cm(tag_map) -> list[tuple[float, float]]:
    groups: dict[str, list[tuple[float, float]]] = {}
    for tag in tag_map.doc["tags"]:
        groups.setdefault(tag["zone_id"], []).append(
            (tag["x_units"] * CM_PER_UNIT, tag["y_units"] * CM_PER_UNIT))
    return [(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            for pts in groups.values()]


class _StreamHeld:
    """Client view whose video lifecycle calls are no-ops: the mission keeps one
    stream open for the whole flight instead of toggling it per burst."""

    def __init__(self, client) -> None:
        self._client = client

    def start_video(self) -> None:
        return None

    def stop_video(self) -> None:
        return None

    def read_frame(self, timeout=None):
        return self._client.read_frame(timeout=timeout)


class CubeMissionRunner(FlightTestRunner):
    def __init__(self, client, mode, minimum_battery: int = 40, *, mission: Mission, tag_map,
                 conn, run_id: str, mission_log: Path,
                 burst_fn: Callable[[], list] | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep, **runner_kwargs) -> None:
        runner_kwargs["retain_video_stream"] = True
        runner_kwargs["envelope"] = cube_envelope()
        super().__init__(client, mode, minimum_battery, **runner_kwargs)
        self.mission = mission
        self.tag_map = tag_map
        self.conn = conn
        self.run_id = run_id
        self.mission_log = Path(mission_log)
        self.burst_fn = burst_fn if burst_fn is not None else self._live_burst
        self.sleep_fn = sleep_fn
        self.bounds = CubeBounds()
        self.panels = _panel_centres_cm(tag_map)
        if not self.panels:
            raise ValueError("tag map has no panels to localise against")
        self._validate_mission()

    # -- planning -----------------------------------------------------------
    def _new_navigator(self) -> CubeNavigator:
        m = self.mission
        return CubeNavigator(self.bounds, m.start_x_cm, m.start_y_cm, m.start_yaw_deg)

    def _nearest_panel_bearing(self, nav: CubeNavigator) -> int:
        here = (nav.x_cm, nav.y_cm)
        panel = min(self.panels, key=lambda p: math.dist(here, p))
        return bearing_deg(here, panel)

    def _waypoint_commands(self, nav: CubeNavigator, wp: Waypoint):
        """Yield (phase, command) for one waypoint, committing each to ``nav``."""
        for command in nav.plan_leg(wp.x_cm, wp.y_cm):
            nav.apply(command)
            yield "leg", command
        cardinal = nav.yaw_deg
        for phase, target in (("face_panel", self._nearest_panel_bearing(nav)),
                              ("face_observation", wp.observe_heading_deg),
                              ("face_cardinal", cardinal)):
            command = nav.rotation_to(target)
            if command is not None:
                nav.apply(command)
            yield phase, command

    def _validate_mission(self) -> None:
        """Dry-plan the whole mission; raises PlanError before anything is sent."""
        nav = self._new_navigator()
        for wp in self.mission.waypoints:
            for _ in self._waypoint_commands(nav, wp):
                pass

    # -- flight -------------------------------------------------------------
    def _send(self, command: Command, nav: CubeNavigator) -> None:
        position = Position(nav.x_cm, nav.y_cm, self.envelope.position.z_cm)
        if command.kind == "move":
            self._emit("flight", f"sending {command.direction} {command.amount} cm")
            action = lambda: self.client.move(command.direction, command.amount)
        else:
            self._emit("flight", f"sending rotation {command.direction} {command.amount} degrees")
            action = lambda: self.client.rotate(command.direction, command.amount)
        response = self._call(action, position)
        if not self._is_ok(response):
            raise RuntimeError(f"{command.kind} {command.direction} {command.amount} failed: {response}")

    def _live_burst(self) -> list:
        if self.mode != "real":
            # DryRunClient frames are an undecodable placeholder (verified: _to_bgr
            # raises on them), so a dry run is honestly UNLOCALISED at every waypoint.
            return []
        count = {"n": 0}

        def on_report(_report) -> None:
            count["n"] += 1

        return run_pose_stream(
            _StreamHeld(self.client), self.tag_map, self.conn, run_id=self.run_id,
            max_frames=None, duration_s=BURST_MAX_S, source_kind="LIVE",
            on_frame=lambda _frame: count["n"] < BURST_TARGET_REPORTS,
            on_report=on_report)

    def _measure(self, wp: Waypoint, cardinal_yaw: int) -> dict:
        self.sleep_fn(SETTLE_S)
        result = localise(self.burst_fn())
        entry = {"waypoint": wp.name, "commanded_cm": [wp.x_cm, wp.y_cm], "fix_cm": None,
                 "heading_deg": None, "n": result.n, "spread_p95_cm": None,
                 "arrival_error_cm": None, "containment": None, "shadow_correction": None,
                 "status": "UNLOCALISED"}
        if isinstance(result, LocalisationFix):
            fix = (result.x_units * CM_PER_UNIT, result.y_units * CM_PER_UNIT)
            shadow = propose_correction((wp.x_cm, wp.y_cm), fix, cardinal_yaw, self.bounds)
            entry.update({
                "fix_cm": [round(fix[0], 1), round(fix[1], 1)],
                "heading_deg": result.heading_deg,
                "spread_p95_cm": round(result.spread_p95_cm, 2),
                "arrival_error_cm": round(math.dist(fix, (wp.x_cm, wp.y_cm)), 1),
                "containment": check_containment(fix[0], fix[1], CubeFootprint()).value,
                "shadow_correction": [asdict(c) for c in shadow],
                "status": "LOCALISED",
            })
        else:
            self._emit("pose", f"{wp.name}: unlocalised ({result.reason})")
        return entry

    def _append_log(self, entry: dict) -> None:
        self.mission_log.parent.mkdir(parents=True, exist_ok=True)
        with self.mission_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _fly_sequence(self) -> None:
        self._enter_altitude_band()
        nav = self._new_navigator()
        for wp in self.mission.waypoints:
            cardinal = nav.yaw_deg
            for phase, command in self._waypoint_commands(nav, wp):
                if phase == "leg":
                    cardinal = nav.yaw_deg
                if command is not None:
                    self._send(command, nav)
                if phase == "face_panel":
                    entry = self._measure(wp, cardinal)
                    self._append_log(entry)
                    self._emit("pose", f"{wp.name}: {entry['status']} "
                                       f"arrival_error_cm={entry['arrival_error_cm']} "
                                       f"containment={entry['containment']}")
                    if entry["containment"] == Containment.OUTSIDE.value:
                        raise ContainmentAbort(
                            f"containment: measured position {entry['fix_cm']} cm is outside the cube")
                    if entry["containment"] == Containment.MARGIN.value:
                        self._emit("alert", f"{wp.name}: measured position is in the cube margin")
                if phase == "face_observation":
                    self.sleep_fn(SETTLE_S)
                    self._emit("observe", f"{wp.name}: observing at heading {wp.observe_heading_deg}")
```

Note for the implementer: `ContainmentAbort` is an ordinary exception, so `FlightTestRunner.run()` handles it through its existing generic failure path — controlled landing, `FAIL` report. That **is** the "existing abort policy" SR-063 permits; do not add a new landing path.

- [ ] **Step 4: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 262 passed, and `grep -n "sentinel.pose" src/sentinel/runner.py` still prints nothing.

```bash
git add src/sentinel/cube/mission.py tests/cube/test_mission.py
git commit -m "feat(cube): open-loop waypoint mission with measured arrival and shadow correction" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `cube-run` CLI and the safety checklist

**Files:**
- Modify: `src/sentinel/cli.py`, `docs/safety.md`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `CubeMissionRunner`, `load_mission` (Task 9).
- Produces: `sentinel cube-run (--dry-run | --real --confirm) --mission M --tag-map T [--db D] [--mission-log L]` plus every flag `run` accepts.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`)

```python
def test_cube_run_dry_run_flies_the_mission_and_writes_the_log(tmp_path):
    import json
    from sentinel.cli import main

    mission = tmp_path / "mission.json"
    mission.write_text(json.dumps({
        "schema_version": "cube-mission-v1", "start": {"x_cm": 150, "y_cm": 60, "yaw_deg": 90},
        "waypoints": [{"name": "WP1", "x_cm": 150, "y_cm": 160, "observe_heading_deg": 90}]}))
    log = tmp_path / "mission.jsonl"
    code = main(["cube-run", "--dry-run", "--mission", str(mission),
                 "--tag-map", "calibration/tag-map-calibrated.json",
                 "--db", str(tmp_path / "pose.db"), "--mission-log", str(log),
                 "--log", str(tmp_path / "run.jsonl")])
    assert code == 0
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    # the dry-run client serves undecodable frames, so the waypoint is honestly UNLOCALISED
    assert [e["status"] for e in entries] == ["UNLOCALISED"]
```

In dry-run mode `_live_burst` returns no reports, so the only wall-clock cost is the settle sleeps. Keep the test fast by adding `monkeypatch` to its signature and starting it with `monkeypatch.setattr("sentinel.cube.mission.SETTLE_S", 0.0)`; `_measure` and `_fly_sequence` read that module global at call time.

- [ ] **Step 2: Run to verify failure** — argparse exits: `invalid choice: 'cube-run'`

- [ ] **Step 3: Implement**

3a. In `build_parser()`, move every `run.add_argument(...)` call and the `mode` mutually-exclusive group into a helper and use it for both parsers:

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

Before replacing, diff this list against the existing `run.add_argument` calls and carry over any flag or help text not shown here — the helper must reproduce the existing `run` parser exactly. Then:

```python
    run = subparsers.add_parser("run", help="run the approved command sequence")
    _add_flight_arguments(run)

    cube_run = subparsers.add_parser(
        "cube-run", help="fly an open-loop waypoint mission inside the operating cube")
    _add_flight_arguments(cube_run)
    cube_run.add_argument("--mission", type=Path, required=True)
    cube_run.add_argument("--tag-map", type=Path, required=True)
    cube_run.add_argument("--db", type=Path, default=Path("artifacts/pose.db"))
    cube_run.add_argument("--mission-log", type=Path, default=Path("artifacts/cube-mission.jsonl"))
```

3b. In `main()`, at the top of the flight path (just after the `--video-duration` check) add:

```python
    if args.command == "cube-run" and args.video_only:
        print("cube-run does not support --video-only.")
        return 1
    cube_conn: sqlite3.Connection | None = None
```

Replace the runner-type selection so `cube-run` swaps the class and adds its kwargs:

```python
        if args.command == "cube-run":
            from .cube.mission import CubeMissionRunner, load_mission

            runner_type = CubeMissionRunner
        elif FlightTestRunner is None:
            from .runner import FlightTestRunner as runner_type
        else:
            runner_type = FlightTestRunner
```

and, immediately after the `runner_kwargs` block for `args.real`:

```python
        if args.command == "cube-run":
            tag_map = load_tag_map(str(args.tag_map))
            if args.real and tag_map.calibration_source == "nominal":
                raise RuntimeError("cube-run --real refuses a nominal (uncalibrated) tag map")
            args.db.parent.mkdir(parents=True, exist_ok=True)
            cube_conn = sqlite3.connect(args.db)
            ensure_schema(cube_conn)
            runner_kwargs.pop("retain_video_stream", None)
            runner_kwargs.pop("video_only", None)
            runner_kwargs.pop("video_only_duration_s", None)
            runner_kwargs.update({
                "mission": load_mission(args.mission), "tag_map": tag_map, "conn": cube_conn,
                "run_id": str(uuid.uuid4()), "mission_log": args.mission_log,
            })
```

In the existing outermost `finally:` of that block add:

```python
        if cube_conn is not None:
            cube_conn.close()
```

Ensure `import uuid` and `import sqlite3` are present at the top of `cli.py` (`sqlite3` already is).

3c. Append to `docs/safety.md`:

```markdown
## Cube mission variant (`cube-run`)

- The operating cube is 3.0 m x 2.5 m x 2.0 m from the floor. Commanded positions stay 40 cm
  inside its x/y faces and between 80 and 150 cm altitude; the room must be substantially larger
  than the cube, and the space behind every tag panel must be clear.
- Place the drone on the marked takeoff pad at the mission file's `start` point, facing its
  `start.yaw_deg`. The whole mission is validated against the commanded bounds before takeoff.
- Commands are `move` and `rotate` only. Position is open-loop. Pose is measured at each
  waypoint and logged; it can alert the operator and trigger a controlled landing if the measured
  position leaves the cube, but it never commands motion (SR-063). The correction it implies is
  logged as a shadow only.
- Always run `cube-run --dry-run` on the exact mission file first.
```

- [ ] **Step 4: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 263 passed

```bash
git add src/sentinel/cli.py docs/safety.md tests/test_cli.py
git commit -m "feat(cube): cube-run CLI and the cube safety checklist" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Observation-pose candidates

**Files:**
- Create: `src/sentinel/cube/observe.py`
- Test: `tests/cube/test_observe.py`

**Interfaces:**
- Consumes: `CubeBounds`, `bearing_deg` (Task 8).
- Produces: `AlleyMouth(name: str, x_cm: float, y_cm: float, axis_deg: float, width_cm: float)` — `axis_deg` is the direction looking **into** the alley; `Candidate(kind: str, x_cm: int, y_cm: int, z_cm: int, heading_deg: int, standoff_cm: float, width_fraction: float, near_blind_cm: float)`; `observation_candidates(mouth: AlleyMouth, bounds: CubeBounds, *, z_cm: int = 80) -> list[Candidate]`. `kind` ∈ `close_and_deep`, `back_and_wide`, `oblique`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/cube/test_observe.py
import pytest

from sentinel.cube.navigator import CubeBounds
from sentinel.cube.observe import AlleyMouth, observation_candidates

BOUNDS = CubeBounds()


def test_on_axis_alley_gives_close_and_wide_candidates():
    # alley opens beyond the y=250 face, centred on x=150, looking along +y
    mouth = AlleyMouth("ALLEY_N", 150.0, 300.0, 90.0, 200.0)
    close, wide = observation_candidates(mouth, BOUNDS)
    assert (close.kind, close.x_cm, close.y_cm, close.heading_deg) == ("close_and_deep", 150, 210, 90)
    assert close.standoff_cm == pytest.approx(90.0)
    assert close.width_fraction == pytest.approx(1.406 * 90 / 200, abs=0.01)
    assert close.near_blind_cm == pytest.approx(80 / 0.7373, abs=1.0)
    assert wide.kind == "back_and_wide" and wide.x_cm == 150
    assert wide.standoff_cm >= 200 / 1.406 - 5 and wide.width_fraction == pytest.approx(1.0, abs=0.04)


def test_wide_candidate_is_omitted_when_the_close_one_already_spans_the_mouth():
    mouth = AlleyMouth("ALLEY_N", 150.0, 300.0, 90.0, 100.0)
    candidates = observation_candidates(mouth, BOUNDS)
    assert [c.kind for c in candidates] == ["close_and_deep"]
    assert candidates[0].width_fraction == 1.0


def test_off_axis_alley_falls_back_to_an_oblique_view():
    # alley axis line (x = 400) never crosses the commanded bounds
    mouth = AlleyMouth("ALLEY_E", 400.0, 300.0, 90.0, 150.0)
    (candidate,) = observation_candidates(mouth, BOUNDS)
    assert candidate.kind == "oblique" and (candidate.x_cm, candidate.y_cm) == (260, 210)
    assert candidate.heading_deg == 33  # bearing from (260, 210) to (400, 300)
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: sentinel.cube.observe`

- [ ] **Step 3: Implement**

```python
# src/sentinel/cube/observe.py
"""Feasible observation poses for one alley mouth, with computed view metrics.

Deterministic geometry only: the agent chooses among the candidates; it never
does this arithmetic itself, and every candidate is inside the commanded bounds.
"""
import math
from dataclasses import dataclass

from sentinel.cube.navigator import CubeBounds, bearing_deg

WIDTH_PER_RANGE = 2.0 * math.tan(math.radians(70.2 / 2.0))   # 1.406: horizontal coverage per unit range
BELOW_HORIZON_DEG = 55.6 / 2.0 + 8.6                          # 36.4: bottom edge of the pitched view
_STEP_CM = 5
_MAX_STANDOFF_CM = 600


@dataclass(frozen=True)
class AlleyMouth:
    name: str
    x_cm: float
    y_cm: float
    axis_deg: float   # direction looking INTO the alley
    width_cm: float


@dataclass(frozen=True)
class Candidate:
    kind: str
    x_cm: int
    y_cm: int
    z_cm: int
    heading_deg: int
    standoff_cm: float
    width_fraction: float
    near_blind_cm: float


def _candidate(kind: str, mouth: AlleyMouth, x: float, y: float, z_cm: int, heading: int) -> Candidate:
    standoff = math.dist((x, y), (mouth.x_cm, mouth.y_cm))
    return Candidate(
        kind=kind, x_cm=round(x), y_cm=round(y), z_cm=z_cm, heading_deg=heading,
        standoff_cm=round(standoff, 1),
        width_fraction=round(min(1.0, WIDTH_PER_RANGE * standoff / mouth.width_cm), 3),
        near_blind_cm=round(z_cm / math.tan(math.radians(BELOW_HORIZON_DEG)), 1))


def _first_on_axis(mouth: AlleyMouth, bounds: CubeBounds, min_standoff_cm: float):
    ux, uy = math.cos(math.radians(mouth.axis_deg)), math.sin(math.radians(mouth.axis_deg))
    t = math.ceil(min_standoff_cm / _STEP_CM) * _STEP_CM
    while t <= _MAX_STANDOFF_CM:
        x, y = mouth.x_cm - t * ux, mouth.y_cm - t * uy
        if bounds.contains(round(x), round(y)):
            return x, y
        t += _STEP_CM
    return None


def observation_candidates(mouth: AlleyMouth, bounds: CubeBounds, *, z_cm: int = 80) -> list[Candidate]:
    heading = round(mouth.axis_deg) % 360
    close = _first_on_axis(mouth, bounds, 0.0)
    if close is None:
        x = min(max(mouth.x_cm, bounds.min_x_cm), bounds.max_x_cm)
        y = min(max(mouth.y_cm, bounds.min_y_cm), bounds.max_y_cm)
        return [_candidate("oblique", mouth, x, y, z_cm,
                           bearing_deg((x, y), (mouth.x_cm, mouth.y_cm)))]
    candidates = [_candidate("close_and_deep", mouth, close[0], close[1], z_cm, heading)]
    if candidates[0].width_fraction < 1.0:
        wide = _first_on_axis(mouth, bounds, mouth.width_cm / WIDTH_PER_RANGE)
        if wide is not None:
            candidates.append(_candidate("back_and_wide", mouth, wide[0], wide[1], z_cm, heading))
    return candidates
```

- [ ] **Step 4: Run and commit**

Run: `uv run python -m pytest tests -q` → Expected: 266 passed

```bash
git add src/sentinel/cube/observe.py tests/cube/test_observe.py
git commit -m "feat(cube): observation-pose candidates for best-effort alley viewing" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

**Part B exit gate (operator):** `cube-run --dry-run` on the real mission file, then Stage 2 supervised flight under G3 owner approval and G4 live-rehearsal consent. Pass: at least 5 of 6 arrivals with horizontal distance to the commanded point ≤ 20 cm, every containment `INSIDE`. The shadow-correction log from that flight is the evidence for deciding spec C1(b).

---

## Self-review record

- **Spec coverage:** §3.1 → Task 1, 6 · §3.2 → Task 2 · §3.3 → Task 3 · §3.4 → Task 4 · §3.5 → Tasks 7–10 · §3.6 → Task 11 · §4 failure table → Tasks 2 (fallback), 9 (UNLOCALISED, MARGIN alert, OUTSIDE abort, pre-takeoff refusal) · §7 Stage 0 → every task's tests; Stage 1 → Tasks 5–6; Stage 2 → Part B exit gate; Stage 3 → deliberately unplanned (needs the SR-063 amendment). C4 → Task 5. C7 → `MAX_WAYPOINTS = 8` in Task 9.
- **Known soft spots, stated rather than hidden:** expected pass counts assume no test is parametrised differently than written — treat them as "baseline + this task's new tests, zero failures", not as exact targets. Literal command strings (`"forward 100"`) in Tasks 7 and 9 depend on the fakes' formatting; the steps say how to adapt.
- **Type consistency:** `_solve_mapped_tags` 5-tuple is consumed identically in `estimate_all`, `estimate_pose`, `_solve_joint`; `Command(kind, direction, amount)`, `CubeBounds`, `propose_correction(target_xy, measured_xy, cardinal_yaw_deg, bounds)` match between Tasks 8, 9 and 11.
