import json
from pathlib import Path

import pytest

from sentinel.coordinator import EvidencePolicy, PlanApprovalService, SimulatedWorker
from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"
PLAN_ID = "55555555-5555-4555-8555-555555555555"
PLAN_HASH = "a" * 64


def test_operator_approval_binds_dispatch_to_exact_plan_hash(tmp_path: Path):
    repo = CoordinatorRepository.open(tmp_path / "approval.sqlite3")
    repo.create_run(RUN_ID, status="WAITING_FOR_APPROVAL")
    repo.connection.execute(
        "INSERT INTO plans(plan_id, run_id, catalogue_id, catalogue_revision, plan_sha256, mode, station_ids_json, canonical_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (PLAN_ID, RUN_ID, "SURVEY_CATALOGUE_V1", "REV_1", PLAN_HASH, "MARKER_VERIFIED", "[]", "{}", "2026-09-17T00:00:00Z"),
    )
    service = PlanApprovalService(repo)
    service.approve(RUN_ID, PLAN_ID, PLAN_HASH, actor_id="OPERATOR_1", expected_state_version=0)
    assert service.dispatch_is_allowed(RUN_ID, PLAN_ID, PLAN_HASH)
    assert not service.dispatch_is_allowed(RUN_ID, PLAN_ID, "b" * 64)
    repo.close()


def test_simulated_worker_submits_canonical_preflight_event():
    received = []
    worker = SimulatedWorker(type("Port", (), {"submit": lambda _, command: received.append(command) or {"status": "ACCEPTED"}})())
    result = worker.report_preflight(RUN_ID, PLAN_ID, PLAN_HASH, {name: True for name in (
        "WIFI_INTERFACE", "DRONE_LINK", "BATTERY_TELEMETRY", "LIVE_VIDEO", "LAYOUT_MARKERS", "BOUNDED_AREA", "HEIGHT_ENVELOPE", "EMERGENCY_LATCH",
    )}, "2026-09-17T00:00:00Z")
    assert result == {"status": "ACCEPTED"}
    assert received[0]["command_type"] == "PREFLIGHT_INGEST"
    assert received[0]["payload"]["preflight_report"]["passed"] is True


def test_evidence_policy_is_disjoint_and_hash_bound():
    fixture = json.loads((Path(__file__).parents[2] / "docs/superpowers/specs/contracts/fixtures/judged-perception-live.valid.json").read_text())
    policy = EvidencePolicy()
    assert policy.evaluate(fixture, artifact_sha256=fixture["artifact_sha256"], threshold=0.9).status == "NEEDS_REVIEW"
    fixture["status"] = "PROVIDER_ERROR"
    assert policy.evaluate(fixture, artifact_sha256=fixture["artifact_sha256"], threshold=0.1).status == "FAILED"
    assert policy.evaluate(fixture, artifact_sha256="b" * 64, threshold=0.1).status == "NEEDS_REVIEW"
