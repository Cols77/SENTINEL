"""Fly one waypoint trial and record it for the multi-run report. MOTORS ON (G3 + G4).

    uv run python scripts/cube_trial.py --real --confirm            # a real flight
    uv run python scripts/cube_trial.py --dry-run                   # rehearse the plumbing

Each trial gets its own directory under artifacts/trials/ with the mission log, the run report,
the pose database and the raw video, then prints that trial's summary. The mission has no start
pad: the drone may take off anywhere in the commanded area, facing the tags, and the runner
measures where it is before the first leg.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

DEFAULTS = {"site": "docs/wp5-flight1-site.json", "mission": "docs/wp5-trial-loop.json",
            "tag_map": "../wp5-drone-pose/artifacts/flight1-map.json"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--real", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--pose", choices=("passive", "reanchor"), default="reanchor")
    parser.add_argument("--site", default=DEFAULTS["site"])
    parser.add_argument("--mission", default=DEFAULTS["mission"])
    parser.add_argument("--tag-map", default=DEFAULTS["tag_map"])
    parser.add_argument("--local-ip", default="192.168.10.2")
    parser.add_argument("--min-battery", type=int, default=40,
                        help="cube-run takeoff floor, %% (operator allowed 20 for trials, 2026-09-21)")
    parser.add_argument("--note", default="", help="free text kept with the trial, e.g. 'windy'")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path("artifacts/trials") / f"{stamp}-{args.pose}{'-dry' if args.dry_run else ''}"
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.note:
        (run_dir / "note.txt").write_text(args.note + "\n", encoding="utf-8")
    command = ["uv", "run", "sentinel", "cube-run", "--real" if args.real else "--dry-run",
               "--site", args.site, "--mission", args.mission, "--pose", args.pose,
               "--tag-map", args.tag_map, "--db", str(run_dir / "pose.db"),
               "--mission-log", str(run_dir / "mission.jsonl"), "--log", str(run_dir / "run.jsonl")]
    if args.pose == "reanchor":
        command.append("--sr063-amended")
    command += ["--min-battery", str(args.min_battery)]
    if args.real:
        command += ["--local-ip", args.local_ip, "--video-path", str(run_dir / "flight.h264")]
    if args.confirm:
        command.append("--confirm")
    print(" ".join(command), flush=True)
    with (run_dir / "console.txt").open("w", encoding="utf-8") as console:
        code = subprocess.call(command, stdout=console, stderr=subprocess.STDOUT)
    print(f"cube-run exited {code}; trial saved in {run_dir}\n", flush=True)
    subprocess.call([sys.executable, "scripts/cube_trials_report.py", str(run_dir)])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
