"""Minimum-loop fail-closed regression checks for A15/A16."""
from __future__ import annotations

import pytest

from sentinel.coordinator.agent_loop import AgentLoop
from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.repository import CoordinatorRepository


def test_waiting_for_approval_never_calls_model(tmp_path):
    repo = CoordinatorRepository.open(tmp_path / "approval.sqlite3")
    repo.create_run("11111111-1111-4111-8111-111111111111", status="WAITING_FOR_APPROVAL")
    calls = []
    result = AgentLoop(repo, lambda request: calls.append(request)).run("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "inspect")
    assert result["error_code"] == "WAITING_FOR_APPROVAL"
    assert calls == []
    repo.close()


def test_force_run_create_requires_failed_report_and_operator(tmp_path):
    repo = CoordinatorRepository.open(tmp_path / "force.sqlite3")
    run_id = "11111111-1111-4111-8111-111111111111"
    repo.create_run(run_id, status="FAILED")
    command = {
        "contract_version": "sentinel.v0", "wire_type": "command_envelope", "command_id": "33333333-3333-4333-8333-333333333333", "command_type": "FORCE_RUN_CREATE", "run_id": run_id, "actor_type": "AGENT", "actor_id": "AGENT_1", "correlation_id": "44444444-4444-4444-8444-444444444444", "idempotency_key": "minimum-force-1", "expected_state_version": 0, "issued_at": "2026-09-17T00:00:00Z", "payload": {"prior_run_id": run_id, "failed_report_id": "55555555-5555-4555-8555-555555555555", "operator_reason": "retry", "new_plan_reference": {"plan_id": "66666666-6666-4666-8666-666666666666", "plan_sha256": "a" * 64, "catalogue_id": "SURVEY_CATALOGUE_V1", "catalogue_revision": "REV_1", "station_ids": ["LEFT_ALLEY_MIDDLE"]}, "real_confirm": True},
    }
    with pytest.raises(CoordinatorError, match="operator"):
        CommandGateway(repo).submit(command)
    repo.close()
