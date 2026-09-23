"""Build CALIBRATION-scope flight profiles from recorded CanningSt flights.

Task B item 6 (room-live-adapters). The operator does not fly L1 calibration
routes; the supervisor still needs an energy profile and a drift profile, and
the live session needs a calibration profile with a validated speed. This
script derives them offline, read-only, from the WP5 trial artifacts already
recorded in the CanningSt cube (``artifacts/trials/*/mission.jsonl`` and
``run.jsonl``):

- battery: the worst observed burn rate of any real flight (first to last
  timestamped waypoint, plus the 1 % reading quantization) plus the
  declared margin -- the same bound ``calibration.BatteryBound`` defines;
- pose drift: every localised open-loop leg's arrival error against its
  length (the sampled pose-drift envelope), the worst error per centimetre,
  the worst heading change per centimetre and the worst altimeter error;
- speeds: the SDK ``speed`` commands the flights actually flew at.

Everything the recordings cannot measure (hover drift per second, recovery
bounds, admission limits, the aircraft identity...) must be *declared* in the
policy file; a missing declaration or a missing measurement refuses with the
quantity's name. Nothing is defaulted silently.

The outputs are legal only inside a matching TRIAL acceptance run: the
calibration profile's state is ``CALIBRATION`` and the energy profile's
execution scope is ``CALIBRATION`` (``SafetySupervisor`` admits that scope in
REAL mode only under a TRIAL grant with an acceptance manifest).

Usage::

    python scripts/room_profiles_from_history.py \
        --trials-root C:/coding/sentinel/.worktrees/integration/artifacts/trials \
        --site fixtures/room_agent/v1/canningst-site.json \
        --policy fixtures/room_agent/v1/profiles/canningst-policy.json \
        --out-dir fixtures/room_agent/v1/profiles
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys

from sentinel.cube.checkpoint import CommandLimits
from sentinel.cube.energy import EnergyProfile
from sentinel.cube.localization import DriftProfile
from sentinel.room import calibration
from sentinel.room.site import parse_room_site

POLICY_SCHEMA = "room-history-profile-policy-v1"

#: DriftProfile fields measured from the recordings; every other numeric
#: field must be declared in the policy's ``drift_limits``.
MEASURED_DRIFT_FIELDS = ("xy_drift_per_cm", "heading_drift_per_cm", "max_height_error_cm")
_DRIFT_IDENTITY_FIELDS = ("schema_version", "profile_id", "validated", "evidence_refs")
DECLARED_DRIFT_FIELDS = tuple(f.name for f in fields(DriftProfile)
                              if f.name not in (*MEASURED_DRIFT_FIELDS, *_DRIFT_IDENTITY_FIELDS))
POLICY_FIELDS = ("profile_id", "aircraft_id", "battery_margin_percent_per_s",
                 "battery_quantization_allowance_percent", "landing_floor_percent", "telemetry_allowance_pct",
                 "sample_max_age_s", "height_error_margin_cm", "drift_limits", "command_limits")

_SPEED = re.compile(r"^speed (\d+)$")


class HistoryError(ValueError):
    """The recordings or the policy cannot support a profile; names what is missing."""


@dataclass(frozen=True)
class Leg:
    trial: str
    waypoint: str
    distance_cm: float
    elapsed_s: float | None
    drift_cm: float
    heading_change_deg: float | None
    height_error_cm: float | None
    #: Task B2: the commanded axis-move path (|dx| + |dy| + |dz|) flown since
    #: the last accepted tag fix -- what ``LocalizationTracker`` charges its
    #: per-centimetre drift on (every MOVE, vertical included) and what an
    #: accepted fix resets. A leg that starts at an UNLOCALISED waypoint
    #: accumulates every leg back to the last fix.
    path_cm: float = 0.0
    speed_cm_s: int | None = None

    @property
    def xy_per_cm(self) -> float | None:
        return self.drift_cm / self.path_cm if self.path_cm > 0 else None

    @property
    def heading_per_cm(self) -> float | None:
        if self.heading_change_deg is None or self.path_cm <= 0:
            return None
        return self.heading_change_deg / self.path_cm


@dataclass(frozen=True)
class BatteryWindow:
    trial: str
    consumed_pct: float
    elapsed_s: float
    #: "flight": first post-takeoff reading to the last reading, timed from
    #: the first acknowledged command after ``takeoff`` to ``land`` (Task B2,
    #: whole flight); "waypoints": first to last timed waypoint (a run
    #: without command timestamps).
    source: str = "waypoints"
    distance_cm: float = 0.0


@dataclass(frozen=True)
class History:
    trials: tuple[str, ...]
    legs: tuple[Leg, ...]
    battery: tuple[BatteryWindow, ...]
    speeds_cm_s: tuple[int, ...]


def percentile(values, q: float) -> float:
    """Linear-interpolated percentile (numpy's default), no numpy needed."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise HistoryError("percentile of an empty sample")
    rank = (len(ordered) - 1) * q / 100.0
    low = math.floor(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


_MOVE = re.compile(r"^(up|down|left|right|forward|back) (\d+)$")


def _flight_window(runs: list[dict]) -> tuple[float, float, float] | None:
    """(first command after takeoff, land) timestamps and the commanded move distance."""
    commands = [c for run in runs if run.get("mode") == "real" for c in run.get("commands") or ()]
    takeoff = next((i for i, c in enumerate(commands) if c.get("command") == "takeoff"
                    and c.get("response") == "ok"), None)
    if takeoff is None:
        return None
    after = [c for c in commands[takeoff + 1:] if _parse_time(c.get("timestamp")) is not None]
    land = next((c for c in after if c.get("command") == "land"), None)
    if not after or land is None:
        return None
    start, end = _parse_time(after[0]["timestamp"]), _parse_time(land["timestamp"])
    distance = sum(int(m.group(2)) for c in after if (m := _MOVE.match(str(c.get("command", "")))))
    return (start, end, float(distance)) if end > start else None


def _parse_time(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _waypoint_time(row: dict) -> float | None:
    times = [_parse_time(frame.get("captured_at")) for frame in row.get("frames") or ()]
    times = [t for t in times if t is not None]
    return min(times) if times else None


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_history(trials_root: str | Path) -> History:
    """Read every real recorded trial under ``trials_root`` (read-only)."""
    root = Path(trials_root)
    if not root.is_dir():
        raise HistoryError(f"trials root {str(root)!r} does not exist")
    trials, legs, battery, speeds = [], [], [], set()
    for trial_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        run_path, mission_path = trial_dir / "run.jsonl", trial_dir / "mission.jsonl"
        if not run_path.exists() or not mission_path.exists():
            continue
        runs = _read_jsonl(run_path)
        if not any(run.get("mode") == "real" for run in runs):
            continue  # dry runs are not flight evidence
        for run in runs:
            for command in run.get("commands") or ():
                match = _SPEED.match(str(command.get("command", "")))
                if match and command.get("response") == "ok":
                    speeds.add(int(match.group(1)))
        rows = _read_jsonl(mission_path)
        trials.append(trial_dir.name)
        # Battery (Task B2): the whole flight -- the first post-takeoff reading
        # to the last, over the time from the first command after takeoff to
        # the land command -- when the run records command timestamps; else
        # the first to last timestamped waypoint.
        readings = [row["battery_pct"] for row in rows if isinstance(row.get("battery_pct"), int)]
        window = _flight_window(runs)
        timed = [(t, row["battery_pct"]) for row in rows
                 if (t := _waypoint_time(row)) is not None and isinstance(row.get("battery_pct"), int)]
        if window is not None and len(readings) >= 2:
            battery.append(BatteryWindow(trial_dir.name, readings[0] - readings[-1], window[1] - window[0],
                                         "flight", window[2]))
        elif len(timed) >= 2 and timed[-1][0] > timed[0][0]:
            battery.append(BatteryWindow(trial_dir.name, timed[0][1] - timed[-1][1], timed[-1][0] - timed[0][0]))
        # Legs: from the previous waypoint's final fix (or its commanded point)
        # to this one's commanded point; drift is the open-loop arrival error.
        previous, previous_time, previous_heading = None, None, None
        previous_z, path = None, 0.0
        for row in rows:
            commanded, fix = row.get("commanded_cm"), row.get("fix_cm")
            now = _waypoint_time(row)
            heading = row.get("heading_error_deg") if row.get("status") == "LOCALISED" else None
            error = row.get("open_loop_error_cm")
            height = row.get("height_cm")
            if previous is not None and commanded is not None:
                path += abs(commanded[0] - previous[0]) + abs(commanded[1] - previous[1])
                if previous_z is not None and len(commanded) > 2:
                    path += abs(commanded[2] - previous_z)
            if (previous is not None and commanded is not None and row.get("status") == "LOCALISED"
                    and isinstance(error, (int, float))):
                distance = math.hypot(commanded[0] - previous[0], commanded[1] - previous[1])
                legs.append(Leg(
                    trial_dir.name, row.get("waypoint", "?"), distance,
                    now - previous_time if now is not None and previous_time is not None else None,
                    float(error),
                    abs(heading - previous_heading) if heading is not None and previous_heading is not None else None,
                    abs(height - commanded[2]) if isinstance(height, (int, float)) else None,
                    path, row.get("leg_speed_cm_s")))
            if fix is not None and row.get("status") == "LOCALISED":
                previous = (fix[0], fix[1])
                path = 0.0  # an accepted fix resets the drift the tracker carries
            elif commanded is not None:
                previous = (commanded[0], commanded[1])
            if isinstance(height, (int, float)):
                previous_z = height
            elif commanded is not None and len(commanded) > 2:
                previous_z = commanded[2]
            previous_time = now if now is not None else previous_time
            if heading is not None:
                previous_heading = heading
    return History(tuple(trials), tuple(legs), tuple(battery), tuple(sorted(speeds)))


def _require(policy: dict, name: str):
    if name not in policy or policy[name] is None:
        raise HistoryError(f"policy does not declare {name}; it cannot be measured from the recordings")
    return policy[name]


def load_policy(path: str | Path) -> dict:
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or policy.get("schema_version") != POLICY_SCHEMA:
        raise HistoryError(f"policy {str(path)!r} is not a {POLICY_SCHEMA} document")
    for name in POLICY_FIELDS:
        _require(policy, name)
    limits = policy["drift_limits"]
    for name in DECLARED_DRIFT_FIELDS:
        _require(limits, name)
    for name in MEASURED_DRIFT_FIELDS:
        if name in limits:
            raise HistoryError(f"policy must not declare {name}: it is measured from the recordings")
    return policy


@dataclass(frozen=True)
class ProfileSet:
    calibration_profile: calibration.CalibrationProfile
    energy_profile: EnergyProfile
    drift_profile: DriftProfile
    command_limits: dict
    measurements: dict


def _excluded_runs(policy: dict) -> dict[str, str]:
    """Task B2: runs the operator excludes as anomalous, each with its reason."""
    excluded = policy.get("excluded_runs") or {}
    if not isinstance(excluded, dict):
        raise HistoryError("excluded_runs must map a run ID to its reason")
    for run_id, reason in excluded.items():
        if not isinstance(reason, str) or not reason.strip():
            raise HistoryError(f"excluded run {run_id!r} needs an explicit reason")
    return dict(excluded)


def _nominal(history: History, excluded: dict[str, str], min_window_s: float) -> tuple[History, list[dict]]:
    """The history without the excluded runs or too-short battery windows, and the log of what was dropped."""
    unknown = sorted(set(excluded) - set(history.trials))
    if unknown:
        raise HistoryError(f"excluded_runs names run(s) not in the history: {unknown}")
    dropped = [{"run": run_id, "what": "run", "reason": excluded[run_id]} for run_id in sorted(excluded)]
    battery = []
    for window in history.battery:
        if window.trial in excluded:
            continue
        if window.elapsed_s < min_window_s:
            # A 1 % reading step over a few seconds is a quantization artefact,
            # not a burn rate (Task B2).
            dropped.append({"run": window.trial, "what": "battery window",
                            "reason": f"{window.elapsed_s:.1f} s is shorter than min_battery_window_s {min_window_s}"})
            continue
        battery.append(window)
    return History(tuple(t for t in history.trials if t not in excluded),
                   tuple(leg for leg in history.legs if leg.trial not in excluded),
                   tuple(battery), history.speeds_cm_s), dropped


def robust_drift(history: History, policy: dict) -> dict | None:
    """The declared percentile of per-leg drift per path centimetre, with its margin."""
    spec = policy.get("robust_drift")
    if spec is None:
        return None
    q, margin = spec.get("percentile"), spec.get("margin_fraction")
    if not isinstance(q, (int, float)) or not 0 < q <= 100 or not isinstance(margin, (int, float)) or margin < 0:
        raise HistoryError("robust_drift needs a percentile in (0, 100] and a nonnegative margin_fraction")
    xy = [leg.xy_per_cm for leg in history.legs if leg.xy_per_cm is not None]
    if not xy:
        raise HistoryError("history lacks pose drift (no localised open-loop leg)")
    statistic = percentile(xy, q)
    return {"percentile": q, "margin_fraction": margin, "legs": len(xy),
            "xy_drift_per_cm_percentile": statistic, "xy_drift_per_cm": statistic * (1 + margin)}


def build_profiles(history: History, *, site, policy: dict) -> ProfileSet:
    """Derive the three profiles; refuses naming any missing quantity."""
    if not history.trials:
        raise HistoryError("history lacks any real recorded flight")
    all_trials = history.trials
    excluded = _excluded_runs(policy)
    history, dropped = _nominal(history, excluded, float(policy.get("min_battery_window_s", 0)))
    if not history.trials:
        raise HistoryError("history lacks any real recorded flight once the excluded runs are removed")
    if not history.battery:
        raise HistoryError("history lacks battery burn (no flight with two timestamped battery readings)")
    if not history.legs:
        raise HistoryError("history lacks pose drift (no localised open-loop leg)")
    if not history.speeds_cm_s:
        raise HistoryError("history lacks flown speeds (no acknowledged SDK speed command)")
    headings = [leg for leg in history.legs if leg.heading_change_deg is not None and leg.distance_cm > 0]
    if not headings:
        raise HistoryError("history lacks heading drift (no leg between two localised headings)")
    heights = [leg.height_error_cm for leg in history.legs if leg.height_error_cm is not None]
    if not heights:
        raise HistoryError("history lacks altitude error (no leg with a height reading)")
    allowance = float(policy["battery_quantization_allowance_percent"])
    rates = [(window.consumed_pct + allowance) / window.elapsed_s for window in history.battery]
    battery_percentile = policy.get("battery_percentile")
    # Task B2: the declared percentile across flights (whole-flight windows),
    # or the worst window when the policy declares none.
    worst_rate = max(rates) if battery_percentile is None else percentile(rates, float(battery_percentile))
    margin = float(policy["battery_margin_percent_per_s"])
    xy_per_cm = max(leg.xy_per_cm for leg in history.legs if leg.xy_per_cm is not None)
    heading_per_cm = max(leg.heading_per_cm for leg in history.legs if leg.heading_per_cm is not None)
    height_error = max(heights) + float(policy["height_error_margin_cm"])
    evidence = tuple(f"wp5-trial:{name}" for name in history.trials)
    evidence += tuple(f"excluded-run:{run_id}" for run_id in sorted(excluded))
    measured = {"xy_drift_per_cm": xy_per_cm, "heading_drift_per_cm": heading_per_cm,
                "max_height_error_cm": float(math.ceil(height_error))}
    robust = robust_drift(history, policy)
    # An explicit, justified operator declaration may replace a measured
    # drift value (never silently: it is named in the profile's evidence and
    # in the printed measurements, next to the value the history measured).
    overrides = policy.get("declared_overrides") or {}
    for name, declared in overrides.items():
        if name not in MEASURED_DRIFT_FIELDS:
            raise HistoryError(f"declared_overrides may only replace a measured drift field, not {name!r}")
        if (not isinstance(declared, dict) or not isinstance(declared.get("value"), (int, float))
                or not str(declared.get("justification", "")).strip()):
            raise HistoryError(f"declared override {name} needs a numeric value and a justification")
        measured[name] = float(declared["value"])
        evidence += (f"operator-declared:{name}",)

    battery = calibration.BatteryBound(
        worst_observed_percent_per_s=worst_rate, margin_percent_per_s=margin,
        quantization_allowance_percent=allowance, landing_floor_percent=float(policy["landing_floor_percent"]))
    envelope = {}
    for leg in history.legs:
        if leg.elapsed_s is None:
            continue
        key = (round(leg.distance_cm / 5.0) * 5.0, round(leg.elapsed_s / 0.5) * 0.5, 0.0)
        envelope[key] = max(envelope.get(key, 0.0), leg.drift_cm)
    if not envelope:
        raise HistoryError("history lacks a timed open-loop leg for the pose-drift envelope")
    profile = calibration.CalibrationProfile(
        schema_version=calibration.SCHEMA_VERSION, kind="flight", state="CALIBRATION",
        site_sha256=site.cube_site_sha256, aircraft_id=str(policy["aircraft_id"]),
        firmware_version=policy.get("firmware_version"), camera_sha256=policy.get("camera_sha256"),
        tag_map_sha256=site.tag_map_sha256, speeds_cm_s=history.speeds_cm_s, battery=battery,
        pose_envelope=tuple(calibration.PoseEnvelopePoint(d, t, y, drift)
                            for (d, t, y), drift in sorted(envelope.items())),
        training_run_ids=tuple(sorted(history.trials)))
    energy = EnergyProfile(
        profile_id=f"{policy['profile_id']}-energy", site_sha256=site.cube_site_sha256,
        aircraft_id=str(policy["aircraft_id"]), percent_per_second_upper=round(battery.conservative_percent_per_s, 6),
        telemetry_allowance_pct=int(policy["telemetry_allowance_pct"]),
        landing_floor_pct=int(policy["landing_floor_percent"]), sample_max_age_s=float(policy["sample_max_age_s"]),
        execution_scope="CALIBRATION")
    drift = DriftProfile(
        schema_version="pose-drift-v1", profile_id=f"{policy['profile_id']}-drift", validated=True,
        evidence_refs=evidence, xy_drift_per_cm=round(measured["xy_drift_per_cm"], 6),
        heading_drift_per_cm=round(measured["heading_drift_per_cm"], 6),
        max_height_error_cm=measured["max_height_error_cm"],
        **{name: policy["drift_limits"][name] for name in DECLARED_DRIFT_FIELDS})
    limits = dict(policy["command_limits"])
    CommandLimits(**{k: v for k, v in limits.items() if k != "transport_limits"})  # refuses unknown fields
    measurements = {
        "trials": list(history.trials), "legs": len(history.legs), "speeds_cm_s": list(history.speeds_cm_s),
        "worst_battery_percent_per_s": worst_rate, "worst_xy_drift_per_cm": xy_per_cm,
        "worst_heading_drift_per_cm": heading_per_cm, "worst_height_error_cm": max(heights),
        "declared_overrides": {name: overrides[name] for name in sorted(overrides)},
        "recorded_trials": list(all_trials), "excluded": dropped, "robust_drift": robust,
        "battery_windows": [{"run": w.trial, "source": w.source, "consumed_pct": w.consumed_pct,
                             "elapsed_s": round(w.elapsed_s, 2), "commanded_distance_cm": w.distance_cm,
                             "percent_per_s": round((w.consumed_pct + allowance) / w.elapsed_s, 6)}
                            for w in history.battery],
        "per_leg": [{"run": leg.trial, "waypoint": leg.waypoint, "path_since_fix_cm": round(leg.path_cm, 1),
                     "open_loop_error_cm": leg.drift_cm, "speed_cm_s": leg.speed_cm_s,
                     "xy_per_cm": None if leg.xy_per_cm is None else round(leg.xy_per_cm, 4)}
                    for leg in history.legs],
    }
    return ProfileSet(profile, energy, drift, limits, measurements)


def write_profiles(profiles: ProfileSet, out_dir: str | Path, *, prefix: str = "canningst") -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "profile": out / f"{prefix}-flight-profile.json",
        "energy": out / f"{prefix}-energy.json",
        "drift": out / f"{prefix}-drift.json",
        "limits": out / f"{prefix}-command-limits.json",
    }
    calibration.write_profile(paths["profile"], profiles.calibration_profile)
    for key, document in (("energy", asdict(profiles.energy_profile)),
                          ("drift", {**asdict(profiles.drift_profile),
                                     "evidence_refs": list(profiles.drift_profile.evidence_refs)}),
                          ("limits", profiles.command_limits)):
        paths[key].write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="room_profiles_from_history")
    parser.add_argument("--trials-root", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--prefix", default="canningst")
    args = parser.parse_args(argv)
    try:
        site = parse_room_site(json.loads(Path(args.site).read_text(encoding="utf-8")))
        profiles = build_profiles(read_history(args.trials_root), site=site, policy=load_policy(args.policy))
    except (HistoryError, OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    paths = write_profiles(profiles, args.out_dir, prefix=args.prefix)
    print(json.dumps({"profile_sha256": calibration.canonical_profile_sha256(profiles.calibration_profile),
                      "files": {key: str(path) for key, path in paths.items()},
                      "measurements": profiles.measurements}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
