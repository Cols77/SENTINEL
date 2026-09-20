"""Consent and construction boundary for rehearsal ``--proposer jev``."""
from __future__ import annotations

import pytest

from scripts import rehearsal_run
from sentinel.coordinator.proposer import JevProposer
from sentinel.coordinator.shadow import MemoryAuditSink


def test_jev_selector_refuses_without_fresh_consent_before_artifacts(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv(rehearsal_run.JEV_PROPOSER_CONSENT_ENV, raising=False)
    db_path = tmp_path / "nested" / "run.sqlite3"
    audit_path = tmp_path / "nested" / "run.jsonl"

    code = rehearsal_run.main([
        "--advisors", "none", "--architecture", "E", "--proposer", "jev",
        "--db", str(db_path), "--audit", str(audit_path),
    ])

    assert code == 2
    assert "G-live-jev-proposer" in capsys.readouterr().err
    assert not db_path.exists()
    assert not audit_path.exists()
    assert not db_path.parent.exists()


def test_jev_selector_uses_pinned_model_and_shared_sink_with_injected_factory(monkeypatch) -> None:
    monkeypatch.setenv(rehearsal_run.JEV_PROPOSER_CONSENT_ENV, "operator-batch-13")
    sink = MemoryAuditSink()
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return object()

    proposer = rehearsal_run.build_proposer("jev", sink=sink, judge_factory=factory)

    assert isinstance(proposer, JevProposer)
    assert proposer.sink is sink
    assert calls == [{
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai",
        "model": "typesafe/jev-1.13-20260917",
    }]


def test_scripted_selector_remains_offline_and_unchanged(monkeypatch) -> None:
    monkeypatch.delenv(rehearsal_run.JEV_PROPOSER_CONSENT_ENV, raising=False)
    proposer = rehearsal_run.build_proposer("scripted:PLAN_ROUTE_ONLY_2_ZONE")
    assert isinstance(proposer, rehearsal_run.ScriptedProposer)
    assert proposer.plan_id == "PLAN_ROUTE_ONLY_2_ZONE"
