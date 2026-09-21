"""CLI consent, immutable telemetry, and live-gate boundaries."""
import hashlib
import json
from pathlib import Path
import pytest


FIXTURE_RELATIVE = "fixtures/proposal_eval/generated-requests-v1.json"
FIXTURE_SHA256 = "1b1089694fd74a82924c020bacfefc2e7a8b905077f05b8bbe01135777822f70"
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
    assert manifest["pinned_fixture_sha256"] == FIXTURE_SHA256
    assert manifest["fixture_sha256"] != fixture.name
    assert manifest["case_ids"] == CASE_IDS
    config = manifest["configurations"]["jev"]
    assert config["requested_model"] == "typesafe/jev-1.13-20260917"
    assert config["prompt_revision"] == DECISION_PROMPT_REVISION
    assert config["decision_prompt_revision"] == DECISION_PROMPT_REVISION
    assert config["expected_resolved_model"] is None
    assert config["resolved_model"] is None
    assert config["identity_status"] == "unverified"
    assert config["identity_decision_note"] is None
    assert manifest["thresholds"] == {"jev_act_at": 0.60, "jev_min_margin": 0.15}
    assert len(printed["authorization_sha256"]) == 64
    assert not out.exists()


def test_dry_run_rejects_semantic_revision_drift_before_provider_or_output(
        tmp_path, monkeypatch, capsys):
    from scripts import proposal_sweep

    out = tmp_path / "must-not-exist"
    monkeypatch.setattr(proposal_sweep, "DECISION_PROMPT_REVISION", "drifted-revision")
    monkeypatch.setattr(
        proposal_sweep, "_live_transport",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider initialized before revision validation")),
    )

    status = proposal_sweep.main([
        "--mode", "live", "--arms", "jev", "--dry-run", "--output", str(out),
        *_identity_args("jev", "typesafe/jev-1.13-20260917"),
    ])

    assert status == 2
    error = capsys.readouterr().err
    assert "select_plan.complete_constraints.v2" in error
    assert "drifted-revision" in error
    assert not out.exists()


def test_dry_run_rejects_changed_canonical_fixture_digest_before_provider_or_output(
        tmp_path, monkeypatch, capsys):
    from scripts import proposal_sweep

    out = tmp_path / "must-not-exist"
    actual_file_digest = proposal_sweep.file_digest

    def changed_digest(path):
        if path.resolve() == proposal_sweep.PINNED_CASES.resolve():
            return "b" * 64
        return actual_file_digest(path)

    monkeypatch.setattr(proposal_sweep, "file_digest", changed_digest)
    monkeypatch.setattr(
        proposal_sweep, "_live_transport",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider initialized before fixture validation")),
    )

    status = proposal_sweep.main([
        "--mode", "live", "--arms", "jev", "--dry-run", "--output", str(out),
        *_identity_args("jev", "typesafe/jev-1.13-20260917"),
    ])

    assert status == 2
    error = capsys.readouterr().err
    assert FIXTURE_SHA256 in error
    assert "b" * 64 in error
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


def test_expected_model_identity_is_bounded_before_manifest_or_output(tmp_path, capsys):
    from scripts.proposal_sweep import main

    out = tmp_path / "must-not-exist"
    status = main([
        "--mode", "live", "--arms", "jev", "--dry-run", "--output", str(out),
        *_identity_args("jev", "m" * 257),
    ])
    assert status == 2
    assert "256" in capsys.readouterr().err
    assert not out.exists()


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
        "fixture_sha256": FIXTURE_SHA256, "pinned_fixture_sha256": FIXTURE_SHA256,
        "repeats": 3,
        "max_calls": 36,
    }
    trials = []
    for repeat in range(1, 4):
        for index, case_id in enumerate(CASE_IDS):
            supported = index < 6
            trials.append({
                "case_id": case_id, "repeat": repeat, "supported": supported,
                "supported_correct": supported, "safe_escalation": not supported,
                "correct_supported_trial": supported,
                "safe_escalation_trial": not supported,
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
    forged = {**manifest, "fixture_sha256": "b" * 64, "pinned_fixture_sha256": "b" * 64}
    assert _evaluate_live_gate(forged, trials, identity_status="verified")["live_gate_applicable"] is False


def test_overlength_provider_identity_cannot_verify_matching_256_character_prefix():
    from scripts.proposal_sweep import _classify_identity
    from sentinel.adapter.llm.openai_compatible import _observation_from_mapping

    expected = "m" * 256
    observation = _observation_from_mapping({"model": expected + "-different-release"})
    observed = [observation["model"]] if observation["model"] else []

    assert observation["model"] is None
    assert _classify_identity(observed, expected, 1) == "unverified"


def test_identity_mismatch_fails_applicable_gate_and_bad_shape_is_nonapplicable():
    from scripts.proposal_sweep import _evaluate_live_gate
    manifest = {"mode": "live", "arms": ["jev"], "fixture_path": FIXTURE_RELATIVE,
                "fixture_sha256": FIXTURE_SHA256, "pinned_fixture_sha256": FIXTURE_SHA256,
                "repeats": 3, "max_calls": 36}
    trials = [{"case_id": CASE_IDS[i % 12], "repeat": i // 12 + 1, "supported": i % 12 < 6,
               "supported_correct": i % 12 < 6, "safe_escalation": i % 12 >= 6,
               "correct_supported_trial": i % 12 < 6,
               "safe_escalation_trial": i % 12 >= 6,
               "unsupported_false_proposal": False, "architecture_f_fallbacks": 0,
               "event_counts": {"provider_request": 1, "provider_result": 1,
                                "proposal_attempt": 1, "case_outcome": 1}}
              for i in range(36)]
    mismatch = _evaluate_live_gate(manifest, trials, identity_status="mismatched")
    assert mismatch["live_gate_applicable"] is True
    assert mismatch["passed"] is False
    trials[0]["event_counts"]["provider_result"] = 0
    gate = _evaluate_live_gate(manifest, trials, identity_status="verified")
    assert gate["live_gate_applicable"] is False
    assert gate["passed"] is False
    assert "trial_event_shape" in gate["failure_reasons"]


def test_live_gate_escalation_credit_requires_authoritative_safe_escalation_score():
    from scripts.proposal_sweep import _evaluate_live_gate

    manifest = {"mode": "live", "arms": ["jev"], "fixture_path": FIXTURE_RELATIVE,
                "fixture_sha256": FIXTURE_SHA256, "pinned_fixture_sha256": FIXTURE_SHA256,
                "repeats": 3, "max_calls": 36}
    trials = [{
        "case_id": CASE_IDS[i % 12], "repeat": i // 12 + 1, "supported": i % 12 < 6,
        "supported_correct": i % 12 < 6, "safe_escalation": i % 12 >= 6,
        "correct_supported_trial": i % 12 < 6,
        "safe_escalation_trial": i % 12 >= 6,
        "unsupported_false_proposal": False, "architecture_f_fallbacks": 0,
        "event_counts": {"provider_request": 1, "provider_result": 1,
                         "proposal_attempt": 1, "case_outcome": 1},
    } for i in range(36)]
    # Generic outcome success remains true, but one escalation lacks the
    # authoritative proposer_escalation route/persisted evidence score.
    escalation = next(row for row in trials if not row["supported"])
    escalation["safe_escalation_trial"] = False

    gate = _evaluate_live_gate(manifest, trials, identity_status="verified")

    assert gate["live_gate_applicable"] is True
    assert gate["safe_escalation"] == 17
    assert gate["passed"] is False
    assert "safe_escalation" in gate["failure_reasons"]


def test_live_gate_supported_credit_requires_authoritative_correct_supported_score():
    from scripts.proposal_sweep import _evaluate_live_gate

    manifest = {"mode": "live", "arms": ["jev"], "fixture_path": FIXTURE_RELATIVE,
                "fixture_sha256": FIXTURE_SHA256, "pinned_fixture_sha256": FIXTURE_SHA256,
                "repeats": 3, "max_calls": 36}
    trials = [{
        "case_id": CASE_IDS[i % 12], "repeat": i // 12 + 1, "supported": i % 12 < 6,
        "supported_correct": i % 12 < 6, "safe_escalation": i % 12 >= 6,
        "correct_supported_trial": i % 12 < 6,
        "safe_escalation_trial": i % 12 >= 6,
        "unsupported_false_proposal": False, "architecture_f_fallbacks": 0,
        "event_counts": {"provider_request": 1, "provider_result": 1,
                         "proposal_attempt": 1, "case_outcome": 1},
    } for i in range(36)]
    supported = next(row for row in trials if row["supported"])
    supported["correct_supported_trial"] = False

    gate = _evaluate_live_gate(manifest, trials, identity_status="verified")

    assert gate["live_gate_applicable"] is True
    assert gate["supported_correct"] == 17
    assert gate["passed"] is False
    assert "supported_correct" in gate["failure_reasons"]


def test_completed_applicable_live_gate_failure_exits_nonzero_without_network(
        tmp_path, monkeypatch, capsys):
    from dataclasses import asdict
    from types import SimpleNamespace
    from scripts import proposal_sweep

    expected_model = "typesafe/jev-1.13-20260917"
    provider_calls = []

    class FakeJudge:
        def __init__(self, stream, observations):
            self.stream = stream
            self.observations = observations

        def choice(self, **kwargs):
            provider_calls.append(kwargs)
            self.stream.write({"record": "provider_wire_request", "request": {"model": expected_model}})
            self.observations.append({
                "model": expected_model,
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            })
            return {"value": "PLAN_ROUTE_ONLY_2_ZONE"}

    def fake_run_case(case, arm, output_dir, proposer_factory=None, **kwargs):
        directory = output_dir / case.case_id / arm
        directory.mkdir(parents=True)
        measured = proposer_factory(arm, SimpleNamespace(write=lambda row: None), case)
        measured.choice(state={}, instruction="select", criteria={}, name="select_plan")
        return {
            "case": asdict(case),
            "artifact_dir": str(directory),
            "audit": [{"record": "proposal_attempt", "gate_outcome": "accepted"}],
            "metrics": {
                "outcome_success": True,
                "failure_reasons": [],
                "correct_supported_trial": not case.expected_escalation,
                "safe_escalation_trial": False,
                "architecture_f_fallbacks": 0,
                "unsupported_false_proposals": 0,
            },
        }

    monkeypatch.setattr(proposal_sweep, "require_authorization", lambda manifest: None)
    monkeypatch.setattr(proposal_sweep, "require_proposer_consent", lambda arm: None)
    monkeypatch.setattr(
        proposal_sweep, "_live_transport",
        lambda arm, config, stream, observations: FakeJudge(stream, observations),
    )
    monkeypatch.setattr(
        proposal_sweep, "build_proposer",
        lambda *args, judge_factory=None, **kwargs: judge_factory(),
    )
    monkeypatch.setattr(proposal_sweep, "run_case", fake_run_case)
    monkeypatch.setattr(
        proposal_sweep, "aggregate",
        lambda records: {"case_denominator": len(records), "plan_attempt_denominator": len(records)},
    )
    out = tmp_path / "fake-live"

    status = proposal_sweep.main([
        "--mode", "live", "--arms", "jev", "--execute", "--output", str(out),
        *_identity_args("jev", expected_model),
    ])
    capsys.readouterr()

    summary = json.loads((out / "summary.json").read_text())
    assert status == 1
    assert len(provider_calls) == 36
    assert summary["live_gate"]["live_gate_applicable"] is True
    assert summary["live_gate"]["passed"] is False
    assert summary["live_gate"]["supported_correct"] == 18
    assert summary["live_gate"]["safe_escalation"] == 0


def test_live_refuses_manifest_authorization_before_initializing_provider_or_artifacts(
        tmp_path, monkeypatch, capsys):
    from scripts import proposal_sweep
    monkeypatch.delenv("SENTINEL_PROPOSAL_EVAL_CONSENT", raising=False)
    monkeypatch.setenv("SENTINEL_JEV_PROPOSER_CONSENT", "older-generic-consent")
    def forbidden(*args, **kwargs):
        raise AssertionError("provider initialized without manifest authorization")
    monkeypatch.setattr(proposal_sweep, "_live_transport", forbidden)
    out = tmp_path / "new"
    assert proposal_sweep.main([
        "--mode", "live", "--arms", "jev", "--execute", "--output", str(out),
        *_identity_args("jev", "typesafe/jev-1.13-20260917"),
    ]) == 2
    assert "SENTINEL_PROPOSAL_EVAL_CONSENT" in capsys.readouterr().err
    assert not out.exists()


def test_recorded_mode_requires_capture_file(tmp_path):
    from scripts.proposal_sweep import main
    assert main(["--mode", "recorded", "--execute", "--output", str(tmp_path / "new")]) == 2


@pytest.mark.parametrize(("provider_model", "expected"), [
    ("typesafe/jev-1.13-20260917", ["typesafe/jev-1.13-20260917"]),
    ("m" * 256 + "-different-release", [None]),
])
def test_recorded_model_identity_uses_live_observation_sanitizer(
        tmp_path, provider_model, expected):
    from types import SimpleNamespace
    from scripts.proposal_sweep import _load_recording

    source_manifest = {"fixture_sha256": "f" * 64, "catalogue_sha256": "c" * 64}
    key_fields = {"case_id": "case", "arm": "jev", "repeat": 1}
    rows = [
        {"record": "run_manifest", "manifest": source_manifest},
        {"record": "provider_request", **key_fields, "request": {"state": "bounded"}},
        {"record": "provider_result", **key_fields, "result": {"value": "plan"}},
        {"record": "provider_wire_result", **key_fields,
         "response": {"model": provider_model, "choices": ["must-not-be-projected"]}},
    ]
    recording = tmp_path / "raw.jsonl"
    recording.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    _captured, identities, _source = _load_recording(
        recording,
        source_manifest,
        [SimpleNamespace(case_id="case")],
        ["jev"],
        1,
    )

    assert identities[("case", "jev", 1)] == expected
    if expected == [None]:
        assert provider_model not in json.dumps(identities[("case", "jev", 1)])


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


def test_provider_observation_is_ineligible_when_append_only_write_fails(monkeypatch):
    import httpx
    from scripts.proposal_sweep import _live_transport

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    observations = []

    class ThrowingStream:
        def __init__(self):
            self.records = []
            self.observation_writes = 0

        def write(self, row):
            if row.get("record") == "provider_observation":
                self.observation_writes += 1
                raise OSError("synthetic append-only failure")
            self.records.append(row)

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "typesafe/jev-1.13-20260917",
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                "answers": {"select_plan": {
                    "type": "choice", "choice": "plan", "probabilities": {"plan": 1.0},
                }},
            }

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    stream = ThrowingStream()
    judge = _live_transport("jev", None, stream, observations)

    answer = judge.choice(
        state={}, instruction="choose", criteria={"plan": "plan"}, name="select_plan")

    assert answer["value"] == "plan"
    assert stream.observation_writes == 1
    assert observations == []


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
