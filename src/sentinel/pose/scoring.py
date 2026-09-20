"""Ground-truth scoring of stored pose runs (pure; no drone, no network).

``truth.json`` maps ``run_id`` -> ``{x, y, heading_deg}`` (room units and
degrees), one stationary run per taped floor point. ``score_runs`` reads the
``pose_events`` rows for each run and reports position/heading error
statistics and PASS/FAIL against thresholds. Fail-closed: a truth run with no
stored poses, or an empty truth set, fails.
"""
import json
import math
import sqlite3
import statistics
from dataclasses import dataclass

ROOM_SCALE_M_PER_UNIT = 0.1  # metres per room unit


@dataclass(frozen=True)
class Thresholds:
    p95_cm: float = 10.0
    heading_deg: float = 5.0
    room_scale_m_per_unit: float = ROOM_SCALE_M_PER_UNIT
    min_samples: int = 0


def heading_error_deg(measured: float, truth: float) -> float:
    """Smallest absolute angular difference in degrees, wrap-safe."""
    return abs((float(measured) - float(truth) + 180.0) % 360.0 - 180.0)


def heading_delta_deg(measured: float, truth: float) -> float:
    """Signed smallest angular difference in degrees, wrapped into [-180, 180]."""
    return (float(measured) - float(truth) + 180.0) % 360.0 - 180.0


def load_truth(path: str) -> dict[str, dict[str, float]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("truth file must map run_id -> {x, y, heading_deg}")
    truth = {}
    for run_id, entry in raw.items():
        try:
            truth[str(run_id)] = {k: float(entry[k]) for k in ("x", "y", "heading_deg")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"truth entry {run_id!r} needs numeric x, y, heading_deg") from exc
    return truth


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0, 1])."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _run_poses(conn: sqlite3.Connection, run_id: str) -> list[tuple[float, float, float]]:
    rows = conn.execute(
        "SELECT payload_json FROM pose_events WHERE run_id = ? ORDER BY sequence_no",
        (run_id,),
    ).fetchall()
    poses = []
    for (payload_json,) in rows:
        pose = json.loads(payload_json)["pose"]
        poses.append((float(pose["point"]["x"]), float(pose["point"]["y"]),
                      float(pose["heading_deg"])))
    return poses


def score_runs(db_path: str, truth: dict[str, dict[str, float]],
               thresholds: Thresholds = Thresholds(), *,
               calibration_source: str | None = None,
               allow_nominal: bool = False) -> dict:
    """Score each truth run against its stored poses.

    When ``calibration_source`` is given and is ``"nominal"`` (uncalibrated
    intrinsics), the overall result fails unless ``allow_nominal``: errors
    measured with guessed intrinsics say nothing about real accuracy.

    Returns ``{"passed": bool, "thresholds": {...}, "runs": {run_id: {...}}}``.
    """
    cm_per_unit = thresholds.room_scale_m_per_unit * 100.0
    runs = {}
    conn = sqlite3.connect(db_path)
    try:
        for run_id, t in truth.items():
            poses = _run_poses(conn, run_id)
            if not poses:
                runs[run_id] = {"n": 0, "passed": False}
                continue
            xs = [x for x, _, _ in poses]
            ys = [y for _, y, _ in poses]
            hs = [h for _, _, h in poses]
            pos_err = [math.hypot(x - t["x"], y - t["y"]) for x, y, _ in poses]
            head_err = [heading_error_deg(h, t["heading_deg"]) for _, _, h in poses]
            p95_units = _percentile(pos_err, 0.95)
            med_units = statistics.median(pos_err)
            head_p95 = _percentile(head_err, 0.95)

            median_x = statistics.median(xs)
            median_y = statistics.median(ys)
            heading_deltas_from_first = [heading_delta_deg(h, hs[0]) for h in hs]
            median_heading = (hs[0] + statistics.median(heading_deltas_from_first)
                              + 180.0) % 360.0 - 180.0
            centroid_dist = [math.hypot(x - median_x, y - median_y) for x, y in zip(xs, ys)]
            heading_spread = [abs(heading_delta_deg(h, median_heading)) for h in hs]

            n = len(poses)
            enough_samples = n >= thresholds.min_samples
            runs[run_id] = {
                "n": n,
                "median_error_units": med_units,
                "p95_error_units": p95_units,
                "median_error_cm": med_units * cm_per_unit,
                "p95_error_cm": p95_units * cm_per_unit,
                "heading_median_deg": statistics.median(head_err),
                "heading_p95_deg": head_p95,
                "bias_dx_cm": (median_x - t["x"]) * cm_per_unit,
                "bias_dy_cm": (median_y - t["y"]) * cm_per_unit,
                "bias_heading_deg": statistics.median(
                    [heading_delta_deg(h, t["heading_deg"]) for h in hs]),
                "spread_p95_cm": _percentile(centroid_dist, 0.95) * cm_per_unit,
                "heading_spread_p95_deg": _percentile(heading_spread, 0.95),
                "enough_samples": enough_samples,
                "passed": bool(p95_units * cm_per_unit <= thresholds.p95_cm
                               and head_p95 <= thresholds.heading_deg
                               and enough_samples),
            }
    finally:
        conn.close()
    calibration_ok = allow_nominal or calibration_source != "nominal"
    return {
        "passed": bool(runs) and calibration_ok and all(r["passed"] for r in runs.values()),
        "calibration_source": calibration_source,
        "calibration_ok": calibration_ok,
        "thresholds": {"p95_cm": thresholds.p95_cm, "heading_deg": thresholds.heading_deg,
                       "min_samples": thresholds.min_samples},
        "runs": runs,
    }
