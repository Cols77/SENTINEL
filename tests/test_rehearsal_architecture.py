"""P4 Task 4: ``--architecture``/``--proposer`` on the rehearsal CLI.

F is unchanged (``tests/test_rehearsal_run.py`` still covers it end to end;
this file only pins that the new ``architecture`` field appears in the
summary). E is exercised through a scripted, non-LLM proposer -- no network
call, no live model -- both for the "operator sees approvals only" happy
path and for the fallback-to-F path a bad plan id produces.
"""
from __future__ import annotations

import json

from scripts.rehearsal_run import main

RUN_ID_F = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RUN_ID_E_OK = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
RUN_ID_E_FALLBACK = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
RUN_ID_E_NO_PROPOSER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

LEGAL_PLAN_ID = "PLAN_ROUTE_ONLY_2_ZONE"


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_architecture_f_completes_and_summary_names_it(tmp_path, capsys):
    db_path = tmp_path / "f.sqlite3"
    audit_path = tmp_path / "f.jsonl"

    exit_code = main([
        "--advisors", "none",
        "--architecture", "F",
        "--choices", f"sp={LEGAL_PLAN_ID},eg=create_task",
        "--run-id", RUN_ID_F,
        "--db", str(db_path),
        "--audit", str(audit_path),
    ])
    assert exit_code == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["architecture"] == "F"
    assert summary["status"] == "COMPLETED"


def test_architecture_e_with_a_legal_scripted_plan_reaches_completed_with_approvals_only(tmp_path, capsys):
    db_path = tmp_path / "e_ok.sqlite3"
    audit_path = tmp_path / "e_ok.jsonl"

    exit_code = main([
        "--advisors", "none",
        "--architecture", "E",
        "--proposer", f"scripted:{LEGAL_PLAN_ID}",
        "--run-id", RUN_ID_E_OK,
        "--db", str(db_path),
        "--audit", str(audit_path),
    ])
    assert exit_code == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["architecture"] == "E"
    assert summary["status"] == "COMPLETED"
    assert summary["judgments"] == 0
    assert summary["approvals"] == 2

    records = _read_jsonl(audit_path)
    route_records = [r for r in records if r["record"] == "architecture_route"]
    assert route_records
    assert all(r["route"] == "proposer" for r in route_records)
    # SELECT_PLAN itself never escalates to the operator under E's proposer
    # path -- EVIDENCE_GATE still gets its own "judgment" record (it is an
    # ADVISORY_POINT shadowed unconditionally, rule-decided or not, exactly
    # as it is under F -- see test_rehearsal_run.py).
    assert not any(r["record"] == "judgment" and r["decision_point"] == "SELECT_PLAN" for r in records)


def test_architecture_e_with_an_unknown_plan_falls_back_to_f(tmp_path, capsys):
    db_path = tmp_path / "e_fallback.sqlite3"
    audit_path = tmp_path / "e_fallback.jsonl"

    exit_code = main([
        "--advisors", "none",
        "--architecture", "E",
        "--proposer", "scripted:NOT_A_REAL_CATALOGUE_PLAN",
        "--choices", f"sp={LEGAL_PLAN_ID},eg=create_task",
        "--run-id", RUN_ID_E_FALLBACK,
        "--db", str(db_path),
        "--audit", str(audit_path),
    ])
    assert exit_code == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["architecture"] == "E"
    assert summary["status"] == "COMPLETED"
    assert summary["judgments"] == 1

    records = _read_jsonl(audit_path)
    route_records = [r for r in records if r["record"] == "architecture_route"]
    assert route_records
    assert route_records[0]["route"] == "fallback_F"


def test_architecture_e_without_a_proposer_exits_2_and_creates_no_database(tmp_path, capsys):
    db_path = tmp_path / "e_no_proposer.sqlite3"
    audit_path = tmp_path / "e_no_proposer.jsonl"

    exit_code = main([
        "--advisors", "none",
        "--architecture", "E",
        "--run-id", RUN_ID_E_NO_PROPOSER,
        "--db", str(db_path),
        "--audit", str(audit_path),
    ])
    assert exit_code == 2
    assert not db_path.exists()
