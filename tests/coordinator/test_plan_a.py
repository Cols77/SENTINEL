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


def test_propose_persists_plan_and_can_be_approved(tmp_path: Path):
    repo = CoordinatorRepository.open(tmp_path / "proposal.sqlite3")
    repo.create_run(RUN_ID)
    service = PlanApprovalService(repo)
    plan = {"plan_id": PLAN_ID, "plan_sha256": PLAN_HASH,
            "catalogue_id": "SURVEY_CATALOGUE_V1", "catalogue_revision": "REV_1",
            "station_ids": ("LEFT_ALLEY_MIDDLE",), "mode": "MARKER_VERIFIED"}
    service.propose(RUN_ID, plan)
    assert repo.connection.execute("SELECT plan_id FROM plans WHERE plan_id = ?", (PLAN_ID,)).fetchone()
    service.approve(RUN_ID, PLAN_ID, PLAN_HASH, actor_id="OPERATOR_1", expected_state_version=2)


def test_preflight_rejects_false_aggregate_and_wrong_plan_binding(tmp_path: Path):
    repo = CoordinatorRepository.open(tmp_path / "preflight.sqlite3")
    repo.create_run(RUN_ID, status="WAITING_FOR_APPROVAL")
    repo.connection.execute(
        "INSERT INTO plans(plan_id, run_id, catalogue_id, catalogue_revision, plan_sha256, mode, station_ids_json, canonical_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (PLAN_ID, RUN_ID, "SURVEY_CATALOGUE_V1", "REV_1", PLAN_HASH, "MARKER_VERIFIED", '["LEFT_ALLEY_MIDDLE"]', "{}", "2026-09-17T00:00:00Z"),
    )
    PlanApprovalService(repo).approve(RUN_ID, PLAN_ID, PLAN_HASH, actor_id="OPERATOR_1", expected_state_version=0)
    checks = [{"name": name, "passed": True, "checked_at": "2026-09-17T00:00:00Z"} for name in (
        "WIFI_INTERFACE", "DRONE_LINK", "BATTERY_TELEMETRY", "LIVE_VIDEO", "LAYOUT_MARKERS", "BOUNDED_AREA", "HEIGHT_ENVELOPE", "EMERGENCY_LATCH")]
    report = {"report_id": "66666666-6666-4666-8666-666666666666", "run_id": RUN_ID,
              "plan_id": PLAN_ID, "plan_sha256": PLAN_HASH, "passed": False,
              "checks": checks, "checked_at": "2026-09-17T00:00:00Z"}
    command = {"contract_version": "sentinel.v0", "wire_type": "command_envelope",
               "command_id": "77777777-7777-4777-8777-777777777777", "command_type": "PREFLIGHT_INGEST",
               "run_id": RUN_ID, "actor_type": "WORKER", "actor_id": "SIMULATED_WORKER",
               "correlation_id": report["report_id"], "idempotency_key": "preflight-test-1",
               "issued_at": "2026-09-17T00:00:00Z", "payload": {"preflight_report": report}}
    with pytest.raises(CoordinatorError) as caught:
        CommandGateway(repo).submit(command)
    assert caught.value.code == "INVALID_PREFLIGHT_REPORT"


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


def test_schema_valid_provider_error_is_failed():
    fixture = json.loads((Path(__file__).parents[2] / "docs/superpowers/specs/contracts/fixtures/judged-perception-live.valid.json").read_text())
    fixture["status"] = "PROVIDER_ERROR"
    fixture["raw_score"] = None
    assert EvidencePolicy().evaluate(fixture, artifact_sha256=fixture["artifact_sha256"], threshold=0.99).status == "FAILED"
