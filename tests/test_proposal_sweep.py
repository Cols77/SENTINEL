"""CLI consent and dry-run boundaries must not create providers or artifacts."""
import json
import pytest


def test_dry_run_validates_and_prints_budget_without_artifacts(tmp_path, capsys):
    from scripts.proposal_sweep import main
    out = tmp_path / "new"
    assert main(["--output", str(out), "--arms", "jev", "--repeats", "1"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["manifest"]["maximum_possible_calls"] > 0
    assert printed["manifest"]["configurations"]["jev"]["requested_model"] == "typesafe/jev-1.13-20260917"
    assert len(printed["authorization_sha256"]) == 64
    assert not out.exists()


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
