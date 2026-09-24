"""Task V: the room_jev_check harness stays tested for free, with a scripted
fake Jev standing in for the real (paid, gated) model.

Nothing here ever builds a real ``JevJudge`` or touches the network: every
case is driven by ``GreedyJudge``, a small deterministic double that always
picks a legal, sensible-shaped action (finish observing before answering,
re-anchor whenever it is offered, answer directly once it can). What is
under test is the harness's own plumbing -- the fake supervisor/site/detector
wiring, the event log it writes, and the Jev-call budget -- not Jev's own
judgment, which the real run (``docs/reports/room-agent-jev-check.md``)
covers separately.

Every case here passes its own ``artifact_root`` (a ``tmp_path`` subdirectory)
instead of relying on ``run_all``'s default. ``run_all``'s default is the
same fixed ``artifacts/room-agent/jev-check/`` tree a real run under
``scripts/room_jev_check.py main()`` writes its evidence to; a scripted,
offline double's logs must never land there, or a later real run's evidence
becomes indistinguishable from -- and can be silently overwritten by --
``GreedyJudge``'s.
"""
from __future__ import annotations

import json

import pytest

from sentinel.judgment import JudgeUnavailable
import scripts.room_jev_check as harness
from scripts.room_jev_check import (BudgetedJudge, JevCheckRefused, build_real_jev_judge,
                                    declared_regions, load_dotenv_nearest, load_site, run_all)


class GreedyJudge:
    """Always finishes observing before it answers, always re-anchors when
    offered, and otherwise answers directly -- a stand-in for "a judge that
    never makes a nonsensical choice", not a claim about Jev's own quality."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict]] = []

    def choice(self, state, instruction, *, criteria, name):
        self.calls.append((state, dict(criteria)))
        if "REANCHOR" in criteria:
            picked = "REANCHOR"
        elif any(key.startswith("OBSERVE:") for key in criteria):
            picked = sorted(key for key in criteria if key.startswith("OBSERVE:"))[0]
        elif "READ_DRONE" in criteria:
            picked = "READ_DRONE"
        elif any(key.startswith("ANSWER") for key in criteria):
            picked = sorted(key for key in criteria if key.startswith("ANSWER"))[0]
        else:
            picked = next(iter(criteria))
        probabilities = {key: (.99 if key == picked else .01 / max(1, len(criteria) - 1)) for key in criteria}
        return {"value": picked, "probabilities": probabilities, "confidence": .95}


def test_load_site_prefers_canningst_and_declares_forward_and_side_views():
    site, path = load_site()
    assert path.name == "canningst-site.json"
    regions = declared_regions(site)
    # The measured site now offers six wall-facing and two side-looking
    # viewpoints. The harness must expose all declared poses, not silently
    # filter away the two side views to preserve the old three-strip shape.
    assert set(regions) == {pose_id for pose_id, _ in site.pose_visibility}
    assert len([region for region in regions if region.endswith("toward_targets")]) == 6
    assert len([region for region in regions if region.endswith(("-left", "-right"))]) == 2


def test_search_case_finds_the_phone_and_returns_home(tmp_path):
    artifact_root = tmp_path / "artifacts"
    results = run_all(GreedyJudge, tmp_dir=tmp_path, cases=("search",), artifact_root=artifact_root)
    result = results[0]
    assert result.terminal_state == "COMPLETED", result.reason
    assert result.passed, result.reason
    assert (artifact_root / "search" / "events.jsonl").exists()
    assert (artifact_root / "search" / "jev_calls.jsonl").exists()


def test_readonly_case_moves_nothing_and_answers_directly(tmp_path):
    results = run_all(GreedyJudge, tmp_dir=tmp_path, cases=("readonly",), artifact_root=tmp_path / "artifacts")
    result = results[0]
    assert result.terminal_state == "COMPLETED", result.reason
    assert result.passed, result.reason
    assert result.jev_calls <= 3


def test_count_case_reaches_a_terminal_count_answer_with_no_invalid_choice(tmp_path):
    results = run_all(GreedyJudge, tmp_dir=tmp_path, cases=("count",), artifact_root=tmp_path / "artifacts")
    result = results[0]
    assert result.terminal_state == "COMPLETED"
    assert result.answer is not None and "phone" in result.answer
    # The harness's own mechanics (no malformed/illegal Jev choice recorded)
    # are what this test pins; the exact confirmed/uncertain split is the
    # counting subsystem's own concern and is reported, not asserted, here.
    assert "invalid/malformed" not in result.reason


def test_staleness_case_either_reanchors_or_the_supervisor_forces_recovery(tmp_path):
    artifact_root = tmp_path / "artifacts"
    results = run_all(GreedyJudge, tmp_dir=tmp_path, cases=("staleness",), artifact_root=artifact_root)
    result = results[0]
    assert "neither a voluntary REANCHOR nor a forced RECOVER" not in result.reason
    # Task V2: staleness is injected after the first accepted observation and
    # the re-anchor is actually reached; the reason says who did it.
    assert result.passed, result.reason
    assert result.reason.startswith("Jev chose REANCHOR (POSE_REANCHORED=True, yaw ROTATE commands issued=0)")
    log = (artifact_root / "staleness" / "events.jsonl").read_text(encoding="utf-8")
    event_types = {json.loads(line)["event_type"] for line in log.splitlines() if line.strip()}
    assert "POSE_REANCHORED" in event_types or any(
        json.loads(line).get("payload", {}).get("candidate_id") == "RECOVER"
        for line in log.splitlines() if line.strip())


class NeverReanchors(GreedyJudge):
    """Observes and answers like GreedyJudge but never picks REANCHOR."""

    def choice(self, state, instruction, *, criteria, name):
        return super().choice(state, instruction, name=name,
                              criteria={k: v for k, v in criteria.items() if k != "REANCHOR"})


def test_staleness_case_forces_the_supervisor_recovery_when_jev_does_not_reanchor(tmp_path):
    results = run_all(NeverReanchors, tmp_dir=tmp_path, cases=("staleness",), artifact_root=tmp_path / "a")
    result = results[0]
    assert result.passed, result.reason
    assert result.reason.startswith("supervisor forced RECOVER (POSE_REANCHORED=True")


def test_count_computers_case_counts_two_computers_in_two_regions(tmp_path):
    artifact_root = tmp_path / "artifacts"
    results = run_all(GreedyJudge, tmp_dir=tmp_path, cases=("count_computers",), artifact_root=artifact_root)
    result = results[0]
    assert result.passed, result.reason
    assert "computer: 0 confirmed; 2 uncertain" in result.answer  # single views, never corroborated


def test_every_case_records_its_acceptance_policy(tmp_path):
    from sentinel.room.controller import AcceptancePolicy
    artifact_root = tmp_path / "artifacts"
    run_all(GreedyJudge, tmp_dir=tmp_path, cases=("readonly",), artifact_root=artifact_root)
    config = json.loads((artifact_root / "readonly" / "config.json").read_text(encoding="utf-8"))
    assert config["acceptance_policy"] == AcceptancePolicy().as_config()
    strict_root = tmp_path / "strict"
    (tmp_path / "s").mkdir()
    run_all(GreedyJudge, tmp_dir=tmp_path / "s", cases=("readonly",), artifact_root=strict_root,
            acceptance=harness.STRICT_ACCEPTANCE)
    strict = json.loads((strict_root / "readonly" / "config.json").read_text(encoding="utf-8"))
    assert strict["acceptance_policy"]["observe_act_at"] == .8 and strict["acceptance_policy"]["reask_once"] is False


def test_all_five_cases_run_under_the_forty_call_budget(tmp_path):
    results = run_all(GreedyJudge, tmp_dir=tmp_path, artifact_root=tmp_path / "artifacts")
    assert [r.name for r in results] == ["search", "staleness", "count", "readonly", "count_computers"]
    for result in results:
        assert result.jev_calls <= 40, f"{result.name} exceeded the Jev call budget: {result.jev_calls}"


def test_no_real_call_without_the_explicit_paid_calls_flag(monkeypatch, tmp_path):
    """Task V2 item 4: the nearest .env (C:/coding/sentinel/.env) carries the
    consent and the key, so they alone must never make a paid call."""
    monkeypatch.setenv("SENTINEL_JEV_PROPOSER_CONSENT", "operator-2026-09-23")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(harness, "JevJudge", lambda **kwargs: pytest.fail("a JevJudge was built"))
    with pytest.raises(JevCheckRefused, match="--i-authorise-paid-calls"):
        build_real_jev_judge()
    built = []
    monkeypatch.setattr(harness, "build_real_jev_judge", lambda **kwargs: built.append(kwargs))
    assert harness.main(["--case", "readonly", "--run-root", str(tmp_path)]) == 2
    assert built == []


def test_the_paid_calls_flag_reaches_the_consent_and_key_gates(monkeypatch, tmp_path):
    built = []

    def fake_build(**kwargs):
        built.append(kwargs)
        raise JevCheckRefused("stop here")

    monkeypatch.setattr(harness, "build_real_jev_judge", fake_build)
    assert harness.main(["--i-authorise-paid-calls", "--case", "readonly", "--run-root", str(tmp_path)]) == 2
    assert built == [{"authorised": True}]


def test_budgeted_judge_refuses_once_the_call_budget_is_exhausted():
    class AlwaysAnswers:
        def choice(self, state, instruction, *, criteria, name):
            value = next(iter(criteria))
            return {"value": value, "probabilities": {value: 1.0}, "confidence": 1.0}

    judge = BudgetedJudge(AlwaysAnswers(), budget=2)
    judge.choice({}, "x", criteria={"A": None}, name="n")
    judge.choice({}, "x", criteria={"A": None}, name="n")
    with pytest.raises(JudgeUnavailable):
        judge.choice({}, "x", criteria={"A": None}, name="n")
    assert len(judge.calls) == 2  # the refused call is never appended twice


def test_budgeted_judge_logs_provider_errors_without_swallowing_them():
    class AlwaysFails:
        def choice(self, state, instruction, *, criteria, name):
            raise RuntimeError("boom")

    judge = BudgetedJudge(AlwaysFails(), budget=5)
    with pytest.raises(RuntimeError):
        judge.choice({}, "x", criteria={"A": None}, name="n")
    assert judge.calls and "error" in judge.calls[0]


def test_build_real_jev_judge_refuses_without_operator_consent(monkeypatch, tmp_path):
    monkeypatch.delenv("SENTINEL_JEV_PROPOSER_CONSENT", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env anywhere above an empty tmp dir
    with pytest.raises(JevCheckRefused):
        build_real_jev_judge(authorised=True)


def test_build_real_jev_judge_refuses_with_consent_but_no_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTINEL_JEV_PROPOSER_CONSENT", "operator-2026-09-23")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(JevCheckRefused):
        build_real_jev_judge(authorised=True)


def test_load_dotenv_nearest_finds_a_parent_env_file(tmp_path):
    (tmp_path / ".env").write_text("FOO=bar\n# comment\nBAZ = qux\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert load_dotenv_nearest(nested) == {"FOO": "bar", "BAZ": "qux"}


def test_load_dotenv_nearest_returns_empty_when_none_is_found(tmp_path):
    assert load_dotenv_nearest(tmp_path) == {}
