#!/usr/bin/env python
"""Detect on each frame a flight saves, while it flies (sentinel.survey.live_detect).

    uv run python scripts/live_detect.py --check-only --consent-db artifacts/consent.sqlite3 \\
        --location home-office --date 2026-09-22 --i-authorise-paid-calls
    uv run python scripts/live_detect.py --frames-log <capture-dir>/frames.jsonl \\
        --out <trial-dir>/detections.jsonl --done-file <trial-dir>/flight.done \\
        --consent-db ... --location ... --date ... --i-authorise-paid-calls

Exit 2 when a gate refuses (no consent recorded, no paid-call authorisation, no
key or model). ``scripts/cube_trial.py --detect`` runs the check before takeoff
and this follower beside the flight; it never talks to the drone.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "src", REPO_ROOT / "scripts", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sentinel.survey.live_detect import LiveDetectRefused, check_gates, follow  # noqa: E402

DETECTORS = ("gemini-openrouter", "gemini", "moondream")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--detector", choices=DETECTORS, default="gemini-openrouter")
    parser.add_argument("--consent-db", type=Path)
    parser.add_argument("--location")
    parser.add_argument("--date")
    parser.add_argument("--i-authorise-paid-calls", action="store_true")
    parser.add_argument("--check-only", action="store_true",
                        help="check the gates and the detector's key/model, send nothing")
    parser.add_argument("--frames-log", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--done-file", type=Path,
                        help="the flight is over once this file exists; drain and exit")
    return parser


def main(argv: list[str] | None = None, *, build_detector=None) -> int:
    args = build_parser().parse_args(argv)
    if build_detector is None:
        from survey_run import build_provider_detector as build_detector
    try:
        check_gates(consent_db=args.consent_db, location=args.location, date=args.date,
                    authorised=args.i_authorise_paid_calls)
        detector = build_detector(args.detector)
    except (LiveDetectRefused, RuntimeError) as refused:
        print(f"live detection refused: {refused}", flush=True)
        return 2
    if args.check_only:
        print(f"live detection ready: {args.detector}, consent recorded for "
              f"{args.location} on {args.date}", flush=True)
        return 0
    if not (args.frames_log and args.out and args.done_file):
        print("--frames-log, --out and --done-file are required to follow a flight", flush=True)
        return 1
    count = follow(args.frames_log, args.out, detector, done=args.done_file.exists,
                   print_fn=lambda line: print(line, flush=True))
    print(f"[detect] done: {count} frame(s), results in {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
