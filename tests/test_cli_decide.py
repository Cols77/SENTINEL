"""P2 Task 3 (f): `sentinel pending` and `sentinel decide` CLI subcommands."""
from __future__ import annotations

import json

import pytest

from sentinel.cli import main
from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"


def _create_db_with_run(tmp_path):
    db_path = tmp_path / "coordinator.sqlite3"
    repository = CoordinatorRepository.open(db_path)
    gateway = CommandGateway(repository)
    gateway.submit({
        "contract_version": "sentinel.v0", "wire_type": "command_envelope",
        "command_id": "22222222-2222-4222-8222-222222222222", "command_type": "RUN_CREATE",
        "run_id": RUN_ID, "actor_type": "OPERATOR", "actor_id": "OPERATOR_1",
        "correlation_id": "33333333-3333-4333-8333-333333333333", "idempotency_key": "run-create-1",
        "issued_at": "2026-09-17T13:00:00Z", "payload": {"request_text": "inspect one station"},
    })
    repository.close()
    return db_path


def test_pending_prints_the_options(tmp_path, capsys):
    db_path = _create_db_with_run(tmp_path)

    exit_code = main(["pending", "--db", str(db_path), "--run-id", RUN_ID])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "decision_point: SELECT_PLAN" in out
    assert "PLAN_ROUTE_ONLY_2_ZONE" in out
    assert "escalate" in out


def test_decide_exits_zero_on_success_and_writes_audit(tmp_path, capsys):
    db_path = _create_db_with_run(tmp_path)
    audit_path = tmp_path / "audit.jsonl"

    exit_code = main([
        "decide", "--db", str(db_path), "--run-id", RUN_ID,
        "--label", "PLAN_ROUTE_ONLY_2_ZONE", "--audit", str(audit_path),
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "decide OK" in out

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record == {
        "record": "operator_choice", "run_id": RUN_ID, "state_version": 2,
        "decision_point": "SELECT_PLAN", "label": "PLAN_ROUTE_ONLY_2_ZONE", "actor_id": "OPERATOR_1",
    }

    repository = CoordinatorRepository.open(db_path)
    try:
        assert repository.read_state(RUN_ID).to_dict()["run_status"] == "WAITING_FOR_APPROVAL"
    finally:
        repository.close()


def test_decide_exits_two_on_an_invalid_label(tmp_path, capsys):
    db_path = _create_db_with_run(tmp_path)

    exit_code = main([
        "decide", "--db", str(db_path), "--run-id", RUN_ID, "--label", "NOT_A_REAL_LABEL",
    ])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "decide failed" in out
    assert "INVALID_OPERATOR_CHOICE" in out
