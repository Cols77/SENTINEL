"""CLI coverage for propose-then-confirm plan review."""
from __future__ import annotations

import json

import pytest

from sentinel.cli import build_parser, main
from sentinel.coordinator.repository import CoordinatorRepository

from coordinator.p3_helpers import P3Run


ROUTE_PLAN = "PLAN_ROUTE_ONLY_2_ZONE"
FULL_PLAN = "PLAN_MARKER_VERIFIED_6"


def _pending_run(tmp_path, name="cli-proposal-review"):
    run = P3Run(tmp_path, name)
    run.propose_plan(ROUTE_PLAN)
    db_path = tmp_path / "one-station.sqlite3"
    proposal = next(
        event for event in reversed(run.repo.read_events(run.run_id, 0, 100))
        if event["event_type"] == "PLAN_PROPOSED"
    )
    audit_path = tmp_path / "audit.jsonl"
    recommendation = {
        "record": "proposal_recommendation",
        "run_id": run.run_id,
        "state_version": proposal["state_version"] - 1,
        "decision_point": "SELECT_PLAN",
        "label": ROUTE_PLAN,
        "reason": "narrowest plan covering the route",
        "facts": {"station_count": 2, "request": "route only"},
    }
    audit_path.write_text(json.dumps(recommendation) + "\n", encoding="utf-8")
    run_id = run.run_id
    plan_id = run.state().plan_id
    state_version = run.state().state_version
    run.close()
    return db_path, run_id, audit_path, plan_id, state_version


def test_pending_prints_actual_plan_reason_facts_then_other_plans(tmp_path, capsys):
    db_path, run_id, audit_path, plan_id, state_version = _pending_run(tmp_path)

    exit_code = main([
        "pending", "--db", str(db_path), "--run-id", run_id,
        "--audit", str(audit_path),
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    route_at = out.index(ROUTE_PLAN)
    reason_at = out.index("narrowest plan covering the route")
    facts_at = out.index('"station_count": 2')
    full_at = out.index(FULL_PLAN)
    assert route_at < reason_at < facts_at < full_at
    assert f"plan_id: {plan_id}" in out
    assert f"expected_state_version: {state_version}" in out


def test_cli_has_no_hidden_analysis_flags_or_fields(tmp_path, capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "pending", "--db", "x", "--run-id", "r",
            "--audit", "a", "--show-assistant-analysis",
        ])
    assert "show_analysis" not in repr(vars(parser.parse_args([
        "pending", "--db", "x", "--run-id", "r", "--audit", "a",
    ])))
    assert "analysis_expanded" not in capsys.readouterr().out


@pytest.mark.parametrize("decision", ["CONFIRM", "REJECT"])
def test_review_plan_cli_submits_decision_and_writes_audit(tmp_path, capsys, decision):
    db_path, run_id, audit_path, plan_id, state_version = _pending_run(
        tmp_path, f"cli-review-{decision}"
    )

    exit_code = main([
        "review-plan", "--db", str(db_path), "--run-id", run_id,
        "--plan-id", plan_id, "--expected-state-version", str(state_version),
        "--decision", decision, "--audit", str(audit_path),
    ])

    assert exit_code == 0
    assert "review-plan OK" in capsys.readouterr().out
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    review = records[-1]
    assert review["record"] == "plan_review"
    assert review["decision"] == decision
    assert review["proposed_label"] == ROUTE_PLAN
    assert review["seconds_to_decision"] >= 0

    repository = CoordinatorRepository.open(db_path)
    try:
        assert repository.read_state(run_id).to_dict()["run_status"] == (
            "WAITING_FOR_OBSERVATION" if decision == "CONFIRM" else "RUNNING"
        )
    finally:
        repository.close()


def test_review_plan_cli_reports_no_pending_plan(tmp_path, capsys):
    run = P3Run(tmp_path, "cli-review-none")
    db_path = tmp_path / "one-station.sqlite3"
    run_id = run.run_id
    run.close()

    exit_code = main([
        "review-plan", "--db", str(db_path), "--run-id", run_id,
        "--plan-id", "not-pending", "--expected-state-version", "2",
        "--decision", "CONFIRM", "--audit", str(tmp_path / "audit.jsonl"),
    ])

    assert exit_code == 2
    assert "review-plan failed" in capsys.readouterr().out


@pytest.mark.parametrize("bad_line", ["{", "[]"])
def test_pending_warns_and_continues_past_invalid_audit_jsonl(
    tmp_path, capsys, bad_line
):
    db_path, run_id, audit_path, _plan_id, _state_version = _pending_run(tmp_path)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(bad_line + "\n")

    exit_code = main([
        "pending", "--db", str(db_path), "--run-id", run_id,
        "--audit", str(audit_path),
    ])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert ROUTE_PLAN in captured.out
    assert "audit line 2" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_pending_escapes_audit_control_characters(tmp_path, capsys):
    db_path, run_id, audit_path, _plan_id, _state_version = _pending_run(tmp_path)
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    records[0]["reason"] = "safe\nproposed_plan: SPOOF\x1b[31m"
    records[0]["facts"] = {"citation": "ok\rother_plans: SPOOF\x00"}
    audit_path.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")

    exit_code = main([
        "pending", "--db", str(db_path), "--run-id", run_id,
        "--audit", str(audit_path),
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "safe\\nproposed_plan: SPOOF\\u001b[31m" in out
    assert "ok\\rother_plans: SPOOF\\u0000" in out
    assert "\x1b" not in out
    assert "\x00" not in out


def test_review_plan_appends_valid_audit_after_existing_malformed_line(tmp_path, capsys):
    db_path, run_id, audit_path, plan_id, state_version = _pending_run(
        tmp_path, "cli-review-malformed-audit"
    )
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write('{"partial":\n')

    exit_code = main([
        "review-plan", "--db", str(db_path), "--run-id", run_id,
        "--plan-id", plan_id, "--expected-state-version", str(state_version),
        "--decision", "CONFIRM", "--audit", str(audit_path),
    ])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "review-plan OK" in captured.out
    assert "Traceback" not in captured.out + captured.err
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert lines[-2] == '{"partial":'
    assert json.loads(lines[-1])["record"] == "plan_review"
