# tests/survey/test_survey_cli.py
import json

import pytest

from scripts.survey_run import build_policy, build_provider_detector, main
from sentinel.survey.consent import ConsentMissing, record_consent
from sentinel.survey.detector import ReplayDetector
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer
from sentinel.survey.providers.moondream import MoondreamDetector
from sentinel.survey.store import SurveyStore

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


# -- Task 12 / plan section C4: --detector / --fallback CLI wiring -----------

class _FakeSession:
    """Never reaches a socket -- records nothing here, just answers every
    call the same way; only used to prove ``build_provider_detector``
    wires the injected session through instead of constructing a real
    one."""

    def post(self, url, *, headers, json, timeout):
        raise AssertionError("a CLI test must never actually post")


def test_replay_remains_the_default_detector(tmp_path):
    """No --detector/--fallback flag at all: the default replay path never
    requires a key or consent, exactly as before this plan's C4."""
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    argv = ["--mode", "F", "--frames", str(frames), "--responses", str(frames),
           "--db", str(tmp_path / "s.sqlite3"), "--request", "count", "--auto-approve"]
    assert main(argv) == 0  # runs to completion -- no refusal for a key or consent


def test_a_live_detector_without_location_and_date_is_refused(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    argv = ["--mode", "F", "--detector", "moondream", "--frames", str(frames),
           "--responses", str(frames), "--db", str(tmp_path / "s.sqlite3"),
           "--request", "count", "--auto-approve"]
    with pytest.raises(ValueError):
        main(argv)


def test_a_live_detector_without_recorded_consent_is_refused(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    argv = ["--mode", "F", "--detector", "moondream", "--frames", str(frames),
           "--responses", str(frames), "--db", str(tmp_path / "s.sqlite3"),
           "--request", "count", "--location", "garden", "--date", "2026-09-21",
           "--auto-approve"]
    with pytest.raises(ConsentMissing):
        main(argv)


def test_a_live_detector_without_an_api_key_is_refused(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    db_path = tmp_path / "s.sqlite3"
    store = SurveyStore.open(db_path)
    record_consent(store, "garden", "2026-09-21", "colin")

    argv = ["--mode", "F", "--detector", "moondream", "--frames", str(frames),
           "--responses", str(frames), "--db", str(db_path), "--request", "count",
           "--location", "garden", "--date", "2026-09-21", "--auto-approve"]
    with pytest.raises(RuntimeError, match="MOONDREAM_API_KEY"):
        main(argv, environ={})


def test_a_fallback_provider_without_an_api_key_is_also_refused(tmp_path):
    """Proves the fallback path is gated too, not just --detector."""
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frames.json").write_text("[]", encoding="utf-8")
    db_path = tmp_path / "s.sqlite3"
    store = SurveyStore.open(db_path)
    record_consent(store, "garden", "2026-09-21", "colin")

    argv = ["--mode", "F", "--detector", "replay", "--fallback", "gemini",
           "--frames", str(frames), "--responses", str(frames), "--db", str(db_path),
           "--request", "count", "--location", "garden", "--date", "2026-09-21",
           "--auto-approve"]
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        main(argv, environ={})


def test_build_provider_detector_never_touches_the_network():
    fake = _FakeSession()
    detector = build_provider_detector(
        "moondream", environ={"MOONDREAM_API_KEY": "k", "MOONDREAM_MODEL": "moondream-3"},
        session=fake)
    assert isinstance(detector, MoondreamDetector)
    assert detector.session is fake


# -- a live judge for mode J: gated, never reached in tests --------------------

def _live_argv(tmp_path, mode, *flags):
    argv, frames = _base_argv(tmp_path)
    argv[argv.index("--mode") + 1] = mode
    return argv + ["--frames", str(frames), *flags]


def test_a_live_judge_is_refused_without_the_operators_go_ahead(tmp_path):
    with pytest.raises((SystemExit, ValueError)):
        main(_live_argv(tmp_path, "J", "--live-judge", "jev"))


def test_a_live_judge_is_refused_outside_mode_j(tmp_path):
    with pytest.raises((SystemExit, ValueError)):
        main(_live_argv(tmp_path, "E", "--live-judge", "llm", "--i-authorise-paid-calls"))


def test_a_live_judge_is_refused_without_an_api_key(tmp_path, monkeypatch):
    import sentinel.env_file as env_file
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(env_file, "load_env_file", lambda *a, **k: {})
    with pytest.raises((SystemExit, ValueError)):
        main(_live_argv(tmp_path, "J", "--live-judge", "llm", "--i-authorise-paid-calls"))


# -- mode names: words for people, letters kept as aliases ---------------------

def test_mode_words_and_letters_are_the_same_modes(tmp_path, capsys):
    import json as _json

    for index, (word, letter) in enumerate((("rules", "F"), ("judge", "J"), ("agent", "E"))):
        for spelling in (word, letter):
            run_dir = tmp_path / f"{index}-{spelling}"
            run_dir.mkdir()
            argv, frames = _base_argv(run_dir)
            argv[argv.index("--mode") + 1] = spelling
            main(argv + ["--frames", str(frames)])
            summary = _json.loads(capsys.readouterr().out)
            assert (summary["mode"], summary["mode_name"]) == (letter, word)


def test_a_live_agent_is_refused_without_the_go_ahead_and_outside_agent_mode(tmp_path, monkeypatch):
    with pytest.raises((SystemExit, ValueError)):
        main(_live_argv(tmp_path, "agent", "--live-agent"))
    with pytest.raises((SystemExit, ValueError)):
        main(_live_argv(tmp_path, "judge", "--live-agent", "--i-authorise-paid-calls"))
    import sentinel.env_file as env_file
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(env_file, "load_env_file", lambda *a, **k: {})
    with pytest.raises((SystemExit, ValueError)):
        main(_live_argv(tmp_path, "agent", "--live-agent", "--i-authorise-paid-calls"))


def test_a_live_agent_with_key_and_go_ahead_reaches_the_proposer(tmp_path, monkeypatch, capsys):
    """The positive wiring: with the flag, the mode and a key, main() builds the real
    LlmAdapter and hands it to LlmProposer. The adapter's transport is stubbed so the
    proof needs no network: its reply picks the first sweep, and the run reports."""
    import sentinel.env_file as env_file
    import sentinel.survey.llm_adapter as llm_adapter

    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(env_file, "load_env_file", lambda *a, **k: {})
    seen = {}

    def fake_transport(url, headers, payload):
        seen["url"], seen["tools"] = url, payload["tools"]
        option_id = payload["tools"][0]["function"]["parameters"]["properties"]["option_id"]["enum"][0]
        return {"model": "z-ai/glm-5.3", "choices": [{"message": {"tool_calls": [
            {"function": {"name": "survey_propose_sweep",
                          "arguments": json.dumps({"option_id": option_id,
                                                   "reason": "the rail sees the whole room"})}}]}}]}

    real_init = llm_adapter.LlmAdapter.__init__

    def stubbed_init(self, *args, **kwargs):
        real_init(self, *args, transport=fake_transport, **kwargs)

    monkeypatch.setattr(llm_adapter.LlmAdapter, "__init__", stubbed_init)
    code = main(_live_argv(tmp_path, "agent", "--live-agent", "--i-authorise-paid-calls"))
    summary = json.loads(capsys.readouterr().out)
    assert code == 0 and summary["mode_name"] == "agent"
    assert seen["url"].endswith("/v1/chat/completions")
    assert summary["outcome"]["status"] == "REPORTED"
