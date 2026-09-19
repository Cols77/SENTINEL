#!/usr/bin/env python
"""Replay WP1 live_scenarios.json through a BoundedJudge (shadow-only).

Offline-capable scenario replay mirroring the B10 live-acceptance pass
criteria. Reads a scenario JSON document, maps each turn to ONE batched
judge evaluate() call, and prints a per-turn pass/fail report.

Usage:
    uv run python scripts/jev_scenario_replay.py --scenarios PATH --judge static
    uv run python scripts/jev_scenario_replay.py --scenarios PATH --judge jev --api-key-env SENTINEL_JEV_API_KEY

--judge jev performs REAL network calls to the Jev decisions API and
requires the named environment variable to hold the API key. Per the
experiment protocol this run is gated behind explicit operator consent
(T12-style); this script does not grant or record consent itself.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.judgment import JevJudge, StaticJudge, replay_scenarios  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", required=True, type=Path,
                        help="path to a live_scenarios.json-style document")
    parser.add_argument("--judge", choices=("static", "jev"), default="static",
                        help="static = deterministic offline double; jev = real Jev API (network)")
    parser.add_argument("--api-key-env", default="SENTINEL_JEV_API_KEY",
                        help="env var name holding the Jev API key (jev only; never embed the key)")
    parser.add_argument("--base-url", default="https://ai-gateway.vercel.sh",
                        help="Jev decisions API base URL (jev only)")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the JSON report to this path instead of stdout only")
    args = parser.parse_args(argv)

    if args.judge == "static":
        judge = StaticJudge()
    else:
        judge = JevJudge(api_key_env=args.api_key_env, base_url=args.base_url)

    report = replay_scenarios(str(args.scenarios), judge)
    rendered = json.dumps(report, indent=2)
    if args.out is not None:
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"report written to {args.out}")
    else:
        print(rendered)

    total = sum(len(s["turns"]) for s in report["scenarios"])
    passed = sum(1 for s in report["scenarios"] for t in s["turns"] if t["passed"])
    print(f"\nturns: {passed}/{total} passed; scenarios: "
          f"{sum(1 for s in report['scenarios'] if s['passed'])}/{len(report['scenarios'])} passed")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
