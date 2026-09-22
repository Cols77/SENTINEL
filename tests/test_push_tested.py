"""scripts/push_tested.py: a push is refused unless HEAD is the recorded tested tip."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import push_tested  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("1\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "one")
    _git(work, "push", "-q", "-u", "origin", "HEAD:refs/heads/main")
    return work, origin


def test_push_is_refused_without_a_record_and_allowed_at_the_recorded_tip(repo, capsys):
    work, origin = repo
    assert push_tested.main(["push", str(work), "HEAD:refs/heads/main"]) == 2
    assert "no tested tip" in capsys.readouterr().err

    (work / "a.txt").write_text("2\n")
    _git(work, "commit", "-q", "-am", "two")
    assert push_tested.main(["record", str(work)]) == 0
    assert push_tested.main(["push", str(work), "HEAD:refs/heads/main"]) == 0
    assert _git(origin, "rev-parse", "main") == _git(work, "rev-parse", "HEAD")


def test_push_is_refused_when_the_tip_moved_after_the_suite_ran(repo, capsys):
    work, origin = repo
    assert push_tested.main(["record", str(work)]) == 0
    before = _git(origin, "rev-parse", "main")
    (work / "a.txt").write_text("3\n")
    _git(work, "commit", "-q", "-am", "landed after the suite")
    assert push_tested.main(["push", str(work), "HEAD:refs/heads/main"]) == 2
    assert "suite ran on" in capsys.readouterr().err
    assert _git(origin, "rev-parse", "main") == before      # nothing was pushed


def test_record_and_push_refuse_a_dirty_tree(repo):
    work, _ = repo
    assert push_tested.main(["record", str(work)]) == 0
    (work / "a.txt").write_text("dirty\n")
    assert push_tested.main(["record", str(work)]) == 2
    assert push_tested.main(["push", str(work), "HEAD:refs/heads/main"]) == 2
