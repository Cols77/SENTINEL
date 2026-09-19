"""Paid-call consent gates: every path that builds a live Jev/LLM advisor must
refuse without SENTINEL_SHADOW_SWEEP_CONSENT, not only the CLI entry points."""
from __future__ import annotations

import pytest

import scripts.rehearsal_run as rehearsal
import scripts.shadow_sweep as sweep

CONSENT = "SENTINEL_SHADOW_SWEEP_CONSENT"


@pytest.fixture(autouse=True)
def _no_consent(monkeypatch):
    monkeypatch.delenv(CONSENT, raising=False)


def test_rehearsal_build_advisors_live_refuses_without_consent():
    with pytest.raises(PermissionError, match=CONSENT):
        rehearsal.build_advisors("live")


def test_rehearsal_run_rehearsal_live_refuses_before_touching_the_db(tmp_path):
    db = tmp_path / "r.sqlite3"
    with pytest.raises(PermissionError, match=CONSENT):
        rehearsal.run_rehearsal(advisors_mode="live", choices={}, db_path=db, audit_path=tmp_path / "a.jsonl")
    assert not db.exists()


def test_rehearsal_cli_live_exits_2_without_consent(capsys):
    assert rehearsal.main(["--advisors", "live"]) == 2
    assert CONSENT in capsys.readouterr().err


@pytest.mark.parametrize("advisor", ["jev", "llm"])
def test_sweep_build_live_judge_refuses_without_consent(advisor):
    with pytest.raises(PermissionError, match=CONSENT):
        sweep._build_live_judge(advisor)


@pytest.mark.parametrize("advisor", ["jev", "llm"])
def test_sweep_cli_live_exits_2_without_consent(advisor, capsys):
    assert sweep.main(["--advisor", advisor]) == 2
    assert CONSENT in capsys.readouterr().err
