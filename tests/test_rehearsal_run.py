"""P2 Task 6: rehearsal runner smoke test.

``scripts/rehearsal_run.py`` drives one simulated demo run end to end
through the real gateway (``ShadowLadder(RulePolicy(), advisors, ...)`` as
the AgentLoop policy), taking the two judgment-point choices from
``--choices`` instead of prompting, and submitting the plan/task approvals
automatically. This is the offline smoke path only: ``--advisors fake``
never makes a network call. The live arm (``--advisors live``) is
parent-only and is never exercised here.
"""
from __future__ import annotations

import json

from scripts.rehearsal_run import main

RUN_ID = "99999999-9999-4999-8999-999999999999"


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_rehearsal_run_with_fake_advisors_completes_and_writes_audit(tmp_path):
    db_path = tmp_path / "rehearsal.sqlite3"
    audit_path = tmp_path / "rehearsal.jsonl"

    exit_code = main([
        "--advisors", "fake",
        "--choices", "sp=PLAN_ROUTE_ONLY_2_ZONE,eg=create_task",
        "--run-id", RUN_ID,
        "--db", str(db_path),
        "--audit", str(audit_path),
    ])
    assert exit_code == 0

    from sentinel.coordinator.repository import CoordinatorRepository

    repo = CoordinatorRepository.open(db_path)
    try:
        status = repo.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (RUN_ID,)
        ).fetchone()[0]
        assert status == "COMPLETED"
    finally:
        repo.close()

    records = _read_jsonl(audit_path)
    kinds = {r["record"] for r in records}
    assert "judgment" in kinds
    assert "operator_choice" in kinds

    judgment_records = [r for r in records if r["record"] == "judgment"]
    assert judgment_records, "expected at least one judgment record"
    assert any(set(r["advisors"]) == {"jev", "llm"} for r in judgment_records)
    for r in judgment_records:
        for name, entry in r["advisors"].items():
            assert entry["error"] is None, (name, entry)
        # anti-automation-bias: the judgment record itself never carries the
        # operator's pick -- that is a *separate* operator_choice record.
        assert r["operator_choice"] is None

    operator_choice_records = [r for r in records if r["record"] == "operator_choice"]
    assert {r["label"] for r in operator_choice_records} == {"PLAN_ROUTE_ONLY_2_ZONE", "create_task"}


def test_rehearsal_run_with_no_advisors_still_completes(tmp_path):
    db_path = tmp_path / "rehearsal_none.sqlite3"
    audit_path = tmp_path / "rehearsal_none.jsonl"

    exit_code = main([
        "--advisors", "none",
        "--choices", "sp=PLAN_ROUTE_ONLY_2_ZONE,eg=create_task",
        "--run-id", RUN_ID,
        "--db", str(db_path),
        "--audit", str(audit_path),
    ])
    assert exit_code == 0

    records = _read_jsonl(audit_path)
    judgment_records = [r for r in records if r["record"] == "judgment"]
    assert judgment_records
    assert all(r["advisors"] == {} for r in judgment_records)
