"""Run authorization is bound to immutable inputs, not a generic paid-call flag."""
import json
import pytest


def test_manifest_change_invalidates_authorization(monkeypatch):
    from sentinel.evaluation_run import manifest_digest, require_authorization
    manifest = {"version": "proposal-eval-run.v1", "mode": "live", "max_calls": 3}
    monkeypatch.setenv("SENTINEL_PROPOSAL_EVAL_CONSENT", manifest_digest(manifest))
    require_authorization(manifest)
    with pytest.raises(PermissionError):
        require_authorization({**manifest, "max_calls": 4})


def test_budget_counts_failed_calls_and_refuses_before_next_transport():
    from sentinel.evaluation_run import CallBudget, BudgetExhausted
    budget = CallBudget(1)
    called = []
    def broken():
        called.append(True)
        raise TimeoutError("sensitive upstream detail")
    with pytest.raises(TimeoutError):
        budget.invoke(broken)
    with pytest.raises(BudgetExhausted):
        budget.invoke(broken)
    assert len(called) == budget.used == 1
    assert budget.exhausted


def test_raw_records_append_and_redact_secrets(tmp_path, monkeypatch):
    from sentinel.evaluation_run import RawStream
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret-123")
    path = tmp_path / "raw.jsonl"
    RawStream(path).write({"record": "request", "text": "test-secret-123", "api_key": "hidden"})
    RawStream(path).write({"record": "result", "value": 42})
    text = path.read_text()
    assert "test-secret-123" not in text and "hidden" not in text
    assert [json.loads(line)["record"] for line in text.splitlines()] == ["request", "result"]


def test_recorded_failure_replays_without_network(tmp_path):
    from sentinel.evaluation_run import RecordedTransport
    transport = RecordedTransport("jev", [{"error_category": "provider_error"}])
    with pytest.raises(RuntimeError, match="recorded provider_error"):
        transport.choice(state={}, criteria={}, instruction="test", name="select_plan")


def test_replay_exhaustion_is_explicit():
    from sentinel.evaluation_run import RecordedTransport
    transport = RecordedTransport("jev", [])
    with pytest.raises(RuntimeError, match="recording exhausted"):
        transport.choice(state={}, criteria={}, instruction="test", name="select_plan")


def test_strict_replay_matches_semantics_but_remaps_runtime_ids():
    from sentinel.evaluation_run import RecordedTransport
    source = {"state": {"run_id": "11111111-1111-4111-8111-111111111111", "operator_request": "Inspect east"}, "criteria": {"PLAN_A": "east"}}
    replay = RecordedTransport("jev", [{"request": source, "result": {"value": "PLAN_A"}}], strict=True)
    current = {**source, "state": {**source["state"], "run_id": "22222222-2222-4222-8222-222222222222"}}
    assert replay.choice(**current) == {"value": "PLAN_A"}
    assert replay.remaining == 0
    replay.validate_complete()


@pytest.mark.parametrize("change", ["operator_request", "criteria", "missing_request"])
def test_strict_replay_rejects_changed_or_missing_request(change):
    from sentinel.evaluation_run import RecordedTransport
    source = {"state": {"operator_request": "Inspect east"}, "criteria": {"PLAN_A": "east"}}
    row = {"request": source, "result": {"value": "PLAN_A"}}
    current = json.loads(json.dumps(source))
    if change == "operator_request":
        current["state"]["operator_request"] = "Inspect west"
    elif change == "criteria":
        current["criteria"]["PLAN_A"] = "west"
    else:
        del row["request"]
    replay = RecordedTransport("jev", [row], strict=True)
    with pytest.raises(RuntimeError, match="recording_request"):
        replay.choice(**current)
    assert replay.invalid


def test_replay_unused_rows_are_an_explicit_failure():
    from sentinel.evaluation_run import RecordedTransport
    replay = RecordedTransport("jev", [{"result": {"value": "PLAN_A"}}])
    with pytest.raises(RuntimeError, match="recording_unused"):
        replay.validate_complete()


@pytest.mark.parametrize("status,category", [("UNAVAILABLE", "provider_error"), ("MALFORMED", "malformed_result")])
def test_measured_typed_failure_is_not_logged_as_success(tmp_path, status, category):
    from types import SimpleNamespace
    from sentinel.evaluation_run import MeasuredTransport, RawStream, CallBudget
    measured = MeasuredTransport(SimpleNamespace(choice=lambda **kw: {"status": status}), CallBudget(1), RawStream(tmp_path / "raw.jsonl"), arm="llm", case_id="case")
    measured.choice(state={})
    assert measured.failures == [category]
    assert json.loads((tmp_path / "raw.jsonl").read_text().splitlines()[-1])["error_category"] == category


def test_budget_refusal_is_local_to_measured_case(tmp_path):
    from types import SimpleNamespace
    from sentinel.evaluation_run import MeasuredTransport, RawStream, CallBudget, BudgetExhausted
    budget = CallBudget(0)
    first = MeasuredTransport(SimpleNamespace(choice=lambda **kw: {}), budget, RawStream(tmp_path / "raw.jsonl"), arm="jev", case_id="first")
    second = MeasuredTransport(SimpleNamespace(choice=lambda **kw: {}), budget, RawStream(tmp_path / "raw.jsonl"), arm="jev", case_id="second")
    with pytest.raises(BudgetExhausted):
        first.choice()
    assert first.budget_refusals == 1 and first.calls == 0
    assert second.budget_refusals == 0 and second.failures == []


def test_raw_redacts_header_credentials_and_known_secrets_in_keys(tmp_path, monkeypatch):
    from sentinel.evaluation_run import RawStream
    monkeypatch.setenv("SENTINEL_LLM_API_KEY_ENV", "CUSTOM_EVAL_KEY")
    monkeypatch.setenv("CUSTOM_EVAL_KEY", "secret-custom-123")
    path = tmp_path / "raw.jsonl"
    RawStream(path).write({"headers": {"X-API-Key": "unknown-secret", "Cookie": "session=hidden"}, "secret-custom-123": "echo secret-custom-123"})
    text = path.read_text()
    assert all(value not in text for value in ("unknown-secret", "session=hidden", "secret-custom-123"))


def test_replay_context_capture_preserves_legal_choices(tmp_path):
    from types import SimpleNamespace
    from sentinel.evaluation_run import MeasuredTransport, RecordedTransport, RawStream, CallBudget
    request = SimpleNamespace(agent_run_id="agent", to_dict=lambda: {"agent_run_id": "agent", "user_request": "Inspect east"})
    context = SimpleNamespace(state=SimpleNamespace(to_packet=lambda: {"operator_request": "Inspect east"}), legal={"labels": ["PLAN_A"]})
    path = tmp_path / "raw.jsonl"
    measured = MeasuredTransport(SimpleNamespace(propose=lambda *args: {"agent_run_id": "agent", "status": "UNAVAILABLE"}), CallBudget(1), RawStream(path), arm="llm", case_id="case")
    measured.propose(request, context)
    source_request = json.loads(path.read_text().splitlines()[0])["request"]
    assert source_request["context"]["legal"] == {"labels": ["PLAN_A"]}
    replay = RecordedTransport("llm", [{"request": source_request, "result": {"status": "UNAVAILABLE"}}], strict=True)
    context.legal = {"labels": ["PLAN_B"]}
    with pytest.raises(RuntimeError, match="recording_request_mismatch"):
        replay.propose(request, context)


def test_replay_uuid_text_is_semantic_and_reference_relationships_are_preserved():
    from sentinel.evaluation_run import normalized_request
    first, second = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    assert normalized_request({"user_request": first}) != normalized_request({"user_request": second})
    assert normalized_request({"run_id": first, "previous": {"run_id": first}}) != normalized_request({"run_id": first, "previous": {"run_id": second}})
