#!/usr/bin/env python
"""Push a branch only if its tip is the commit the test suite was run on.

Three sessions write to ``integration/survey-demo``. On 2026-09-22 a merge landed
on the branch between a full suite run and the push that followed it, so an
untested commit was published. This is the guard against that: record the tip
when the suite passes, and refuse to push a different tip.

    uv run python scripts/push_tested.py record <worktree>      # after a green suite
    uv run python scripts/push_tested.py push <worktree> <branch>

The record is a file in the worktree's .git directory, so it is per-worktree
and never committed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _git(worktree: str, *args: str) -> str:
    return subprocess.run(["git", "-C", worktree, *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _record_path(worktree: str) -> Path:
    git_dir = Path(_git(worktree, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = Path(worktree) / git_dir
    return git_dir / "TESTED_TIP"


def _dirty(worktree: str) -> bool:
    """Modified or staged tracked files. Untracked files are another session's
    drafts in a shared worktree; they are not in HEAD and do not go with a push."""
    return bool(_git(worktree, "status", "--porcelain", "--untracked-files=no"))


def record(worktree: str) -> int:
    if _dirty(worktree):
        print("refusing to record: tracked files are modified", file=sys.stderr)
        return 2
    tip = _git(worktree, "rev-parse", "HEAD")
    _record_path(worktree).write_text(tip + "\n", encoding="utf-8")
    print(f"recorded tested tip {tip[:7]}")
    return 0


def push(worktree: str, branch: str) -> int:
    path = _record_path(worktree)
    if not path.exists():
        print("refusing to push: no tested tip recorded for this worktree", file=sys.stderr)
        return 2
    tested = path.read_text(encoding="utf-8").strip()
    tip = _git(worktree, "rev-parse", "HEAD")
    if tip != tested:
        print(f"refusing to push: HEAD is {tip[:7]} but the suite ran on {tested[:7]}; "
              "run the suite again and re-record", file=sys.stderr)
        return 2
    if _dirty(worktree):
        print("refusing to push: tracked files are modified", file=sys.stderr)
        return 2
    subprocess.run(["git", "-C", worktree, "push", "origin", branch], check=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "record":
        return record(argv[1])
    if len(argv) == 3 and argv[0] == "push":
        return push(argv[1], argv[2])
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
