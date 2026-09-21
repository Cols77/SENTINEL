"""Closed-loop waypoint check without a drone: the real mission runner flies a
simulated Tello whose moves drift, and localises from simulated pose bursts.

Only the airframe and the camera are simulated. Planning, the quality and
plausibility gates, re-anchoring, trims, containment and the mission log are
the production code in ``cube.mission``. The pose noise defaults are the
2026-09-21 stand-test figures; the move-drift defaults are assumptions, since
no in-flight data exists yet.
"""
import argparse
import json
import math
import random
import statistics
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.cube.mission import CubeMissionRunner, load_mission
from sentinel.cube.site import load_site
from sentinel.dry_run import DryRunClient

_BODY_AXES = {"forward": (1, 0), "back": (-1, 0), "right": (0, -1), "left": (0, 1)}


@dataclass(frozen=True)
class DriftModel:
    takeoff_sd_cm: float = 10.0     # assumed: where the drone really is after takeoff
    scale_sd: float = 0.05          # assumed: per-flight move length error (fraction)
    along_sd_cm: float = 5.0        # assumed: per-move noise along the move
    cross_sd_cm: float = 3.0        # assumed: per-move noise across the move
    yaw_sd_deg: float = 2.0         # assumed: heading drift per move
    pose_bias_cm: float = 4.0       # measured: static bias 2.6-5.6 cm
    pose_sd_cm: float = 1.0         # measured: joint spread 0.45-2.63 cm
    heading_bias_deg: float = 2.5   # measured: +2.1 to +3.4 deg at every viewpoint

    def scaled(self, factor: float) -> "DriftModel":
        return DriftModel(self.takeoff_sd_cm * factor, self.scale_sd * factor,
                          self.along_sd_cm * factor, self.cross_sd_cm * factor,
                          self.yaw_sd_deg * factor, self.pose_bias_cm, self.pose_sd_cm,
                          self.heading_bias_deg)


class DriftingClient(DryRunClient):
    """A dry-run client that also tracks where a drifting drone would really be."""

    def __init__(self, start_x_cm: float, start_y_cm: float, model: DriftModel,
                 rng: random.Random) -> None:
        super().__init__()
        self.model, self.rng = model, rng
        self.x_cm = start_x_cm + rng.gauss(0, model.takeoff_sd_cm)
        self.y_cm = start_y_cm + rng.gauss(0, model.takeoff_sd_cm)
        self.heading_deg = 90.0 + rng.gauss(0, model.yaw_sd_deg)
        self._scale = 1.0 + rng.gauss(0, model.scale_sd)
        bias_dir = rng.uniform(0, 2 * math.pi)
        self._pose_bias = (model.pose_bias_cm * math.cos(bias_dir),
                           model.pose_bias_cm * math.sin(bias_dir))

    def move(self, direction, distance_cm):
        response = super().move(direction, distance_cm)
        axes = _BODY_AXES.get(str(getattr(direction, "value", direction)))
        if axes is not None:
            along = distance_cm * self._scale + self.rng.gauss(0, self.model.along_sd_cm)
            cross = self.rng.gauss(0, self.model.cross_sd_cm)
            forward, left = axes[0] * along - axes[1] * cross, axes[1] * along + axes[0] * cross
            heading = math.radians(self.heading_deg)
            self.x_cm += forward * math.cos(heading) - left * math.sin(heading)
            self.y_cm += forward * math.sin(heading) + left * math.cos(heading)
            self.heading_deg += self.rng.gauss(0, self.model.yaw_sd_deg)
        return response

    def rotate(self, direction_or_degrees, degrees=None, *, clockwise=None):
        response = super().rotate(direction_or_degrees, degrees, clockwise=clockwise)
        if degrees is None:
            degrees, direction = direction_or_degrees, "cw" if clockwise else "ccw"
        else:
            direction = str(direction_or_degrees)
        self.heading_deg += -degrees if direction == "cw" else degrees
        return response

    def burst(self, n: int = 12) -> list:
        now = datetime.now(timezone.utc)
        reports = []
        for index in range(n):
            x = self.x_cm + self._pose_bias[0] + self.rng.gauss(0, self.model.pose_sd_cm)
            y = self.y_cm + self._pose_bias[1] + self.rng.gauss(0, self.model.pose_sd_cm)
            heading = self.heading_deg + self.model.heading_bias_deg + self.rng.gauss(0, 0.3)
            reports.append(DronePoseReport(
                report_id=f"sim-{index}", run_id="sim", observed_at=now, source_kind="SIMULATED",
                point=(x / 10.0, y / 10.0), heading_deg=(heading + 180.0) % 360.0 - 180.0,
                altitude_m=None, method="APRILTAG_36H11_PNP",
                tag_observations=(TagObservation(0, "STAND_0", 1.5, 0.9),),
                reprojection_error_px=0.9))
        return reports


class _ScoredRunner(CubeMissionRunner):
    """Adds the true position to each mission-log line; changes no behaviour."""

    def _append_log(self, entry: dict) -> None:
        entry["true_cm"] = [round(self.client.x_cm, 1), round(self.client.y_cm, 1)]
        if "commanded_cm" in entry:
            entry["true_error_cm"] = round(math.dist(entry["true_cm"], entry["commanded_cm"][:2]), 1)
        super()._append_log(entry)


def fly_once(mission, site, pose_mode: str, model: DriftModel, seed: int, log_path: Path) -> dict:
    rng = random.Random(seed)
    if mission.start_x_cm is None:
        # a start-free mission: the operator put the drone anywhere in the commanded area
        env = site.envelope()
        start = (rng.uniform(env.min_x_cm, env.max_x_cm), rng.uniform(env.min_y_cm, env.max_y_cm))
    else:
        start = (mission.start_x_cm, mission.start_y_cm)
    client = DriftingClient(start[0], start[1], model, rng)
    runner = _ScoredRunner(client, "dry-run", mission=mission, site=site, pose_mode=pose_mode,
                           mission_log=log_path, burst_fn=client.burst,
                           sleep_fn=lambda seconds: None)
    result = runner.run(confirm=True)
    rows = ([json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            if log_path.exists() else [])
    return {"status": result.status, "rows": rows, "error": result.error,
            "moves": sum(1 for c in client.commands if c.split()[0] in _BODY_AXES)}


def monte_carlo(mission, site, pose_mode: str, model: DriftModel, runs: int,
                tolerance_cm: float) -> dict:
    errors: dict[str, list[float]] = {wp.name: [] for wp in mission.waypoints}
    completed = trims = moves = outer = 0
    landings: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for seed in range(runs):
            flight = fly_once(mission, site, pose_mode, model, seed, Path(tmp) / f"{seed}.jsonl")
            finished = flight["status"] == "PASS" and sum(r["waypoint"] != "START" for r in flight["rows"]) == len(mission.waypoints)
            completed += finished
            if not finished:
                cause = str(flight["error"]).split(":")[0]
                landings[cause] = landings.get(cause, 0) + 1
            moves += flight["moves"]
            for row in flight["rows"]:
                if row["waypoint"] == "START":
                    continue
                trims += row["trim_rounds_sent"]
                outer += row.get("tolerance") == "OUTER"
                if finished:
                    errors[row["waypoint"]].append(row["true_error_cm"])
    summary = {"pose_mode": pose_mode, "runs": runs, "completed": completed,
               "mean_moves": round(moves / runs, 1), "mean_trim_rounds": round(trims / runs, 2),
               "outer_circle_stops": outer, "landings": landings, "waypoints": {}}
    for name, values in errors.items():
        if values:
            ordered = sorted(values)
            summary["waypoints"][name] = {
                "median_cm": round(statistics.median(ordered), 1),
                "p95_cm": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 1),
                "within_tolerance": round(sum(v <= tolerance_cm for v in ordered) / len(ordered), 3)}
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mission", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--runs", type=int, default=300)
    parser.add_argument("--tolerance-cm", type=float, default=15.0)
    parser.add_argument("--drift-factor", type=float, default=1.0,
                        help="multiplies every assumed drift term (2.0 = pessimistic)")
    args = parser.parse_args(argv)
    mission, site = load_mission(args.mission), load_site(args.site)
    model = DriftModel().scaled(args.drift_factor)
    for pose_mode in ("off", "passive", "reanchor"):
        if pose_mode == "off" and mission.start_x_cm is None:
            print("\noff: skipped - a mission without a start pad needs a pose mode")
            continue
        summary = monte_carlo(mission, site, pose_mode, model, args.runs, args.tolerance_cm)
        print(f"\n{pose_mode}: {summary['completed']}/{summary['runs']} flights completed, "
              f"{summary['mean_moves']} moves and {summary['mean_trim_rounds']} trim rounds per flight")
        print(f"  stops accepted in the outer circle: {summary['outer_circle_stops']}; "
              f"early landings by cause: {summary['landings'] or 'none'}")
        for name, stats in summary["waypoints"].items():
            print(f"  {name:<12} true error median {stats['median_cm']:5.1f} cm  "
                  f"p95 {stats['p95_cm']:5.1f} cm  inside {args.tolerance_cm:.0f} cm circle "
                  f"{stats['within_tolerance']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
