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
LIVE_DETECTORS = ("gemini-openrouter", "gemini", "moondream")
LIVE_DETECT = str(Path(__file__).resolve().parent / "live_detect.py")
#: after landing, how long the last frames' detection may take before it is stopped
FOLLOWER_DRAIN_S = 90.0


def main(argv=None, *, run=subprocess.call, call=subprocess.call, popen=subprocess.Popen) -> int:
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
    parser.add_argument("--detect", choices=("none",) + LIVE_DETECTORS, default="none",
                        help="detect on each saved frame during the flight, in a separate "
                             "process (scripts/live_detect.py); paid calls, needs consent")
    parser.add_argument("--detect-tiles", choices=("none", "2x2", "3x3"), default="none",
                        help="with --detect: also detect on enlarged crops, for small objects")
    parser.add_argument("--consent-db", default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--i-authorise-paid-calls", action="store_true")
    args = parser.parse_args(argv)

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

    follower = None
    if args.detect != "none":
        gates = [f"--detector={args.detect}", f"--tiles={args.detect_tiles}"]
        for flag, value in (("--consent-db", args.consent_db), ("--location", args.location),
                            ("--date", args.date)):
            if value:
                gates.append(f"{flag}={value}")
        if args.i_authorise_paid_calls:
            gates.append("--i-authorise-paid-calls")
        # refused here, before cube-run is even started: no takeoff without the gates
        if run([sys.executable, LIVE_DETECT, "--check-only", *gates]) != 0:
            print("live detection refused; not flying", flush=True)
            return 2
        frames = run_dir / "frames"
        command += ["--capture-dir", str(frames)]
        done = run_dir / "flight.done"
        follower = popen([sys.executable, LIVE_DETECT, *gates,
                          "--frames-log", str(frames / "frames.jsonl"),
                          "--out", str(run_dir / "detections.jsonl"), "--done-file", str(done)])

    print(" ".join(command), flush=True)
    code = 1
    try:
        with (run_dir / "console.txt").open("w", encoding="utf-8") as console:
            code = call(command, stdout=console, stderr=subprocess.STDOUT)
    finally:
        if follower is not None:
            done.touch()
            try:
                follower.wait(timeout=FOLLOWER_DRAIN_S)
            except subprocess.TimeoutExpired:
                follower.kill()
                print(f"live detection still busy after {FOLLOWER_DRAIN_S:.0f} s; stopped",
                      flush=True)
    print(f"cube-run exited {code}; trial saved in {run_dir}\n", flush=True)
    run([sys.executable, "scripts/cube_trials_report.py", str(run_dir)])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
