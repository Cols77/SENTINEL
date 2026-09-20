# tests/survey/test_survey_cli.py
import json

import pytest

from scripts.survey_run import build_policy, main
from sentinel.survey.consent import ConsentMissing
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer


class _Sink:
    def write(self, record):
        pass


def test_each_mode_builds_its_own_proposer():
    assert isinstance(build_policy("F", sink=_Sink()), RuleProposer)
    assert isinstance(build_policy("J", judge=object(), sink=_Sink()), JevProposer)
    assert isinstance(build_policy("E", adapter=object(), sink=_Sink()), LlmProposer)


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        build_policy("X", sink=_Sink())


def test_a_mode_with_no_judge_or_adapter_still_builds_an_offline_default():
    """Task 10's flag list has no --judge/--adapter (Task 12's provider gate
    is separate and still closed), so J and E must still be constructible
    -- and usable -- offline, without a live provider."""
    j = build_policy("J", sink=_Sink())
    e = build_policy("E", sink=_Sink())
    assert isinstance(j, JevProposer)
    assert isinstance(e, LlmProposer)


def test_retaining_person_frames_without_consent_refuses_to_start(tmp_path, capsys):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ConsentMissing):
        main(["--mode", "F", "--frames", str(frames), "--responses", str(frames),
              "--db", str(tmp_path / "s.sqlite3"), "--request", "count",
              "--location", "garden", "--date", "2026-09-21",
              "--retain-person-frames", "--auto-approve"])
