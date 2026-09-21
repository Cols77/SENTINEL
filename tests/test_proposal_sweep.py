"""CLI consent, immutable telemetry, and live-gate boundaries."""
import hashlib
import json
from pathlib import Path
import pytest


FIXTURE_RELATIVE = "fixtures/proposal_eval/generated-requests-v1.json"
CASE_IDS = [
    "generated_middle_only", "generated_short_route", "generated_no_ends",
    "generated_all_tags", "generated_complete_verified", "generated_not_short",
    "generated_left_only", "generated_front_pair", "generated_verified_middle",
    "generated_unspecified", "generated_conflicting_coverage", "generated_visible_computers",
]


def _identity_args(arm, model):
    return ["--expected-resolved-model", f"{arm}={model}",
            "--identity-decision-note", f"{arm}=reviewed exact provider identity"]


def test_dry_run_validates_and_prints_budget_without_artifacts(tmp_path, capsys):
    from scripts.proposal_sweep import main
    from sentinel.decision_prompt import DECISION_PROMPT_REVISION
    out = tmp_path / "new"
    assert main(["--output", str(out), "--arms", "jev", "--repeats", "1"]) == 0
    printed = json.loads(capsys.readouterr().out)
    manifest = printed["manifest"]
    fixture = Path(__file__).parents[1] / FIXTURE_RELATIVE
    assert manifest["maximum_possible_calls"] == 12
    assert manifest["fixture_path"] == FIXTURE_RELATIVE
    assert manifest["fixture_sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert manifest["fixture_sha256"] != fixture.name
    assert manifest["case_ids"] == CASE_IDS
    config = manifest["configurations"]["jev"]
    assert config["requested_model"] == "typesafe/jev-1.13-20260917"
    assert config["decision_prompt_revision"] == DECISION_PROMPT_REVISION
    assert config["expected_resolved_model"] is None
    assert config["resolved_model"] is None
    assert config["identity_status"] == "unverified"
    assert config["identity_decision_note"] is None
    assert manifest["thresholds"] == {"jev_act_at": 0.60, "jev_min_margin": 0.15}
    assert len(printed["authorization_sha256"]) == 64
    assert not out.exists()


def test_live_dry_run_binds_reviewed_identity_cost_and_fixed_call_budget(capsys):
    from scripts.proposal_sweep import main
    args = ["--mode", "live", "--arms", "jev", "--dry-run", "--max-calls", "36",
            "--cost-per-call-usd", "0.025", *_identity_args("jev", "typesafe/jev-1.13-20260917")]
    assert main(args) == 0
    manifest = json.loads(capsys.readouterr().out)["manifest"]
    assert manifest["repeats"] == 3
    assert manifest["maximum_possible_calls"] == 36
    assert manifest["maximum_fresh_provider_calls"] == 36
    assert manifest["cost_per_call_usd_assumption"] == 0.025
    assert manifest["estimated_cost_usd"] == pytest.approx(0.9)
    assert manifest["token_accounting"] == {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
    }
    assert manifest["configurations"]["jev"]["expected_resolved_model"] == "typesafe/jev-1.13-20260917"
    assert manifest["configurations"]["jev"]["identity_decision_note"] == "reviewed exact provider identity"


def test_llm_dry_run_requires_exact_reviewed_prompt_and_tool_revisions(monkeypatch, capsys):
    from scripts.proposal_sweep import main

    monkeypatch.setenv("SENTINEL_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("SENTINEL_LLM_MODEL", "z-ai/glm-5.3")
    monkeypatch.setenv("SENTINEL_LLM_TIMEOUT_MS", "10000")
    monkeypatch.setenv("SENTINEL_LLM_PROMPT_REVISION", "prompt-v4-complete-constraints")
    monkeypatch.setenv("SENTINEL_LLM_TOOL_POLICY_REVISION", "legal-tools-v2")
    monkeypatch.setenv("SENTINEL_LLM_API_KEY_ENV", "OPENROUTER_API_KEY")
    args = ["--mode", "live", "--arms", "llm", "--dry-run",
            *_identity_args("llm", "z-ai/glm-5.3")]
    assert main(args) == 0
    config = json.loads(capsys.readouterr().out)["manifest"]["configurations"]["llm"]
    assert config["prompt_revision"] == "prompt-v4-complete-constraints"
    assert config["tool_policy_revision"] == "legal-tools-v2"
    assert config["decision_prompt_revision"] == "select_plan.complete_constraints.v2"

    monkeypatch.setenv("SENTINEL_LLM_PROMPT_REVISION", "old")
    assert main(args) == 2
    assert "prompt-v4-complete-constraints" in capsys.readouterr().err


@pytest.mark.parametrize("option,value", [
    ("--expected-resolved-model", "bogus=model"),
    ("--identity-decision-note", "bogus=reviewed"),
    ("--expected-resolved-model", "jev="),
    ("--identity-decision-note", "jev="),
])
def test_identity_mapping_arguments_validate_selected_arms_and_nonempty_values(option, value, capsys):
    from scripts.proposal_sweep import main
    assert main(["--arms", "jev", option, value]) == 2
    assert "evaluation refused" in capsys.readouterr().err


def test_live_identity_mappings_are_required_for_each_model_arm(capsys):
    from scripts.proposal_sweep import main
    assert main(["--mode", "live", "--arms", "jev", "--dry-run"]) == 2
    error = capsys.readouterr().err
    assert "expected-resolved-model" in error
    assert "identity-decision-note" in error


@pytest.mark.parametrize(("observed", "expected", "total", "status"), [
    (["typesafe/jev-1.13-20260917"] * 36, "typesafe/jev-1.13-20260917", 36, "verified"),
    (["typesafe/jev-1.13-20260917"], "typesafe/jev-1.13-20260917", 36, "partially_verified"),
    ([], "typesafe/jev-1.13-20260917", 36, "unverified"),
    (["~typesafe/jev-latest"] * 36, "typesafe/jev-1.13-20260917", 36, "mismatched"),
    (["z-ai/glm-5.3-flash"], "z-ai/glm-5.3", 1, "mismatched"),
])
def test_identity_classification_uses_exact_model_ids(observed, expected, total, status):
    from scripts.proposal_sweep import _classify_identity
    assert _classify_identity(observed, expected, total) == status


def test_live_gate_has_exact_applicability_and_denominators():
    from scripts.proposal_sweep import _evaluate_live_gate

    manifest = {
        "mode": "live", "arms": ["jev"], "fixture_path": FIXTURE_RELATIVE,
        "fixture_sha256": "a" * 64, "pinned_fixture_sha256": "a" * 64, "repeats": 3,
        "max_calls": 36,
    }
    trials = []
    for repeat in range(1, 4):
        for index, case_id in enumerate(CASE_IDS):
            supported = index < 6
            trials.append({
                "case_id": case_id, "repeat": repeat, "supported": supported,
                "supported_correct": supported, "safe_escalation": not supported,
                "unsupported_false_proposal": False, "architecture_f_fallbacks": 0,
                "event_counts": {"provider_request": 1, "provider_result": 1,
                                 "proposal_attempt": 1, "case_outcome": 1},
            })
    gate = _evaluate_live_gate(manifest, trials, identity_status="verified")
    assert gate == {
        "live_gate_applicable": True, "passed": True,
        "supported_correct": 18, "supported_denominator": 18,
        "safe_escalation": 18, "escalation_denominator": 18,
        "unsupported_false_proposals": 0, "unsupported_denominator": 18,
        "architecture_f_fallbacks": 0, "f_credit": 0,
        "terminal_trials": 36, "failure_reasons": [],
    }

    for changed in (
        {"mode": "scripted"}, {"arms": ["jev", "llm"]}, {"repeats": 2},
        {"fixture_path": "fixtures/proposal_eval/cases.json"}, {"fixture_sha256": "b" * 64},
    ):
        candidate = {**manifest, **changed}
        assert _evaluate_live_gate(candidate, trials, identity_status="verified")["live_gate_applicable"] is False


def test_applicable_live_gate_fails_on_mismatch_or_trial_shape():
    from scripts.proposal_sweep import _evaluate_live_gate
    manifest = {"mode": "live", "arms": ["jev"], "fixture_path": FIXTURE_RELATIVE,
                "fixture_sha256": "a" * 64, "pinned_fixture_sha256": "a" * 64,
                "repeats": 3, "max_calls": 36}
    trials = [{"case_id": CASE_IDS[i % 12], "repeat": i // 12 + 1, "supported": i % 12 < 6,
               "supported_correct": i % 12 < 6, "safe_escalation": i % 12 >= 6,
               "unsupported_false_proposal": False, "architecture_f_fallbacks": 0,
               "event_counts": {"provider_request": 1, "provider_result": 1,
                                "proposal_attempt": 1, "case_outcome": 1}}
              for i in range(36)]
    assert not _evaluate_live_gate(manifest, trials, identity_status="mismatched")["passed"]
    trials[0]["event_counts"]["provider_result"] = 0
    gate = _evaluate_live_gate(manifest, trials, identity_status="verified")
    assert not gate["passed"]
    assert "trial_event_shape" in gate["failure_reasons"]


def test_live_refuses_before_initializing_provider_or_artifacts(tmp_path, monkeypatch):
    from scripts import proposal_sweep
    monkeypatch.delenv("SENTINEL_PROPOSAL_EVAL_CONSENT", raising=False)
    monkeypatch.setenv("SENTINEL_JEV_PROPOSER_CONSENT", "older-generic-consent")
    def forbidden(*args, **kwargs):
        raise AssertionError("provider initialized without manifest authorization")
    monkeypatch.setattr(proposal_sweep, "_live_transport", forbidden)
    out = tmp_path / "new"
    assert proposal_sweep.main(["--mode", "live", "--arms", "jev", "--execute", "--output", str(out)]) == 2
    assert not out.exists()


def test_recorded_mode_requires_capture_file(tmp_path):
    from scripts.proposal_sweep import main
    assert main(["--mode", "recorded", "--execute", "--output", str(tmp_path / "new")]) == 2


def _escalation_fixture(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"version": "proposal-outcomes.v1", "cases": [{"case_id": "unsupported", "request": "count computers",
        "expected_escalation": True, "expected_terminal": "RUNNING", "expected_effects": [],
        "scripted_choices": ["escalate"]}]}))
    return path


def test_budget_refusal_does_not_fail_later_rule_arm(tmp_path):
    from scripts.proposal_sweep import main
    path = _escalation_fixture(tmp_path)
    out = tmp_path / "run"
    assert main(["--fixtures", str(path), "--execute", "--output", str(out),
                 "--arms", "jev", "F", "--repeats", "1", "--max-calls", "0"]) == 1
    jev = json.loads((out / "repeat-1/unsupported/jev/final-outcome.json").read_text())
    rules = json.loads((out / "repeat-1/unsupported/f/final-outcome.json").read_text())
    assert not jev["metrics"]["outcome_success"]
    assert rules["metrics"]["outcome_success"]
    assert jev["provider_calls"] == rules["provider_calls"] == 0


def test_scripted_capture_replays_through_actual_proposers(tmp_path):
    from scripts.proposal_sweep import main
    path = _escalation_fixture(tmp_path)
    source, target = tmp_path / "source", tmp_path / "replay"
    common = ["--fixtures", str(path), "--execute", "--arms", "llm", "jev", "--repeats", "1"]
    assert main([*common, "--output", str(source)]) == 0
    assert main([*common, "--mode", "recorded", "--recording", str(source / "raw.jsonl"), "--output", str(target)]) == 0
    for arm in ("llm", "jev"):
        a = json.loads((source / f"repeat-1/unsupported/{arm}/final-outcome.json").read_text())
        b = json.loads((target / f"repeat-1/unsupported/{arm}/final-outcome.json").read_text())
        assert a["metrics"]["outcome_success"] == b["metrics"]["outcome_success"]
        assert b["error"] is None
        assert b["provider_calls"] == 0


def test_live_jev_preserves_http_failure_evidence(monkeypatch):
    import httpx
    from scripts.proposal_sweep import _live_transport
    from types import SimpleNamespace
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    records = []
    class Response:
        status_code = 429
        text = '{"error":"rate limited","usage":{"requests":1}}'
        def json(self):
            return json.loads(self.text)
        def raise_for_status(self):
            raise RuntimeError("private HTTP details")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: Response())
    judge = _live_transport("jev", None, SimpleNamespace(write=records.append), [])
    with pytest.raises(RuntimeError):
        judge.choice(state={}, instruction="choose", criteria={"plan": "plan"}, name="select_plan")
    responses = [r for r in records if r["record"] == "provider_wire_result"]
    assert responses and responses[0]["http_status"] == 429
    assert "rate limited" in json.dumps(responses[0])
    assert "private HTTP details" not in json.dumps(records)


def test_live_jev_preserves_malformed_http_body(monkeypatch):
    import httpx
    from scripts.proposal_sweep import _live_transport
    from types import SimpleNamespace
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    records = []
    class Response:
        status_code = 200
        text = '<html>upstream failure</html>'
        def json(self):
            raise ValueError("not JSON")
        def raise_for_status(self):
            pass
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: Response())
    judge = _live_transport("jev", None, SimpleNamespace(write=records.append), [])
    with pytest.raises(ValueError):
        judge.choice(state={}, instruction="choose", criteria={"plan": "plan"}, name="select_plan")
    responses = [r for r in records if r["record"] == "provider_wire_result"]
    assert responses and responses[0]["http_status"] == 200
    assert "upstream failure" in json.dumps(responses[0])


def test_recorded_rejection_and_reproposal_preserves_request_integrity(tmp_path):
    from dataclasses import asdict
    from sentinel.proposal_eval import load_cases
    from scripts.proposal_sweep import main
    case = next(c for c in load_cases() if c.case_id == "reject_then_different_plan")
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps({"version": "proposal-outcomes.v1", "cases": [asdict(case)]}))
    source, replay = tmp_path / "source", tmp_path / "replay"
    common = ["--fixtures", str(fixture), "--execute", "--arms", "llm", "jev", "--repeats", "1"]
    assert main([*common, "--output", str(source)]) == 0
    assert main([*common, "--mode", "recorded", "--recording", str(source / "raw.jsonl"), "--output", str(replay)]) == 0
    for arm in ("llm", "jev"):
        before = json.loads((source / f"repeat-1/{case.case_id}/{arm}/final-outcome.json").read_text())
        after = json.loads((replay / f"repeat-1/{case.case_id}/{arm}/final-outcome.json").read_text())
        assert before["metrics"]["outcome_success"]
        assert after["error"] is None
        assert after["metrics"]["outcome_success"]
        assert after["metrics"]["accepted_proposals"] == before["metrics"]["accepted_proposals"]
        if arm == "llm":
            assert after["metrics"]["accepted_proposals"] == 2
