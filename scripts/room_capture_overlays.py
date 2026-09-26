"""Bake the verified VLM boxes onto copies of room runs' saved captures.

Writes ``runs/<run_id>/captures_overlay/*.png``; the original captures are
never modified. New runs get this automatically at mission end; use this for
runs flown before that, or to re-render.

  python scripts/room_capture_overlays.py                      # every runs/l3-* run
  python scripts/room_capture_overlays.py runs/l3-20260925-163311
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sentinel.room.capture_overlays import render_run_overlays

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="room_capture_overlays", description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*", type=Path,
                        help="run directories (default: every runs/l3-* directory)")
    args = parser.parse_args(argv)
    runs = args.runs or sorted(path for path in (REPO_ROOT / "runs").glob("l3-*") if path.is_dir())
    total = 0
    for run_dir in runs:
        try:
            written = render_run_overlays(run_dir)
        except Exception as exc:  # one damaged run must not stop the others
            print(f"{run_dir.name}: failed: {exc}", file=sys.stderr)
            continue
        total += len(written)
        print(f"{run_dir.name}: {len(written)} overlay(s)"
              + (f" -> {written[0].parent}" if written else ""))
    print(f"{total} overlay(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
