# tests/survey/test_survey_cli.py
import json

import pytest

from scripts.survey_run import build_policy, main
from sentinel.survey.consent import ConsentMissing
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer

#: RuleProposer always picks the first actionable candidate, and
#: options.generate_options lists RAIL_MID_HEIGHT first -- see
#: candidates.propose_candidates (a survey.propose_sweep Candidate per
#: option, in the options list's own order).
_REPLAY_OPTION_ID = "RAIL_MID_HEIGHT"


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


# -- Task 11: --site / --pose CLI wiring (V12 + V13) -------------------------

def _base_argv(tmp_path, **extra):
    frames = tmp_path / "frames"
    frames.mkdir(exist_ok=True)
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    argv = ["--mode", "F", "--responses", str(frames),
           "--db", str(tmp_path / "s.sqlite3"), "--request", "count", "--auto-approve"]
    for key, value in extra.items():
        argv += [key, value]
    return argv, frames


def test_site_and_frames_are_mutually_exclusive(tmp_path):
    argv, frames = _base_argv(tmp_path)
    argv += ["--frames", str(frames), "--site", "docs/wp5-flight1-site.json",
            "--surface-z-cm", "0"]
    with pytest.raises(SystemExit):
        main(argv)


def test_neither_site_nor_frames_is_also_refused(tmp_path):
    argv, _ = _base_argv(tmp_path)
    with pytest.raises(SystemExit):
        main(argv)


def test_site_requires_surface_z_cm(tmp_path):
    argv, _ = _base_argv(tmp_path, **{"--site": "docs/wp5-flight1-site.json"})
    with pytest.raises(SystemExit):
        main(argv)


def test_reanchor_requires_the_amendment_flag_at_cli_level(tmp_path):
    argv, _ = _base_argv(tmp_path, **{"--site": "docs/wp5-flight1-site.json",
                                      "--surface-z-cm": "0", "--pose": "reanchor"})
    with pytest.raises(SystemExit):
        main(argv)


def test_summary_reports_measured_and_commanded_frame_counts(tmp_path, capsys):
    """Does not depend on --site landing anywhere real -- the existing
    replay path (--frames, no --site) already produces COMMANDED-only
    frames (every ReplayFlight fixture predates Task 11's MEASURED
    stamping), so this exercises the new counting code with the
    pre-existing flight port."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    manifest = [
        {"stop_index": i, "look_deg": 0.0, "x_cm": float(i * 75 - 112), "y_cm": 0.0,
         "z_cm": 120.0, "heading_deg": 0.0, "position_kind": "COMMANDED",
         "image_path": str(frames_dir / f"f{i}.png"), "artifact_id": f"A{i}",
         "sha256": "0" * 64, "option_id": _REPLAY_OPTION_ID}
        for i in range(4)
    ]
    (frames_dir / "frames.json").write_text(json.dumps(manifest), encoding="utf-8")
    responses = tmp_path / "responses"
    responses.mkdir()

    argv = ["--mode", "F", "--frames", str(frames_dir), "--responses", str(responses),
           "--db", str(tmp_path / "s.sqlite3"), "--request", "count", "--auto-approve"]
    assert main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["frame_counts"] == {"measured": 0, "commanded": 4, "outer_tolerance": 0}
