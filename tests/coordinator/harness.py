"""Injected fake ports for the A15/A16 coordinator composition test."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.preflight import SimulatedWorker
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"
AGENT_RUN_ID = "22222222-2222-4222-8222-222222222222"
ARTIFACT_ID = "33333333-3333-4333-8333-333333333333"
OBSERVATION_ID = "44444444-4444-4444-8444-444444444444"
RESULT_ID = "55555555-5555-4555-8555-555555555555"
PROVIDER_PROFILE_ID = "66666666-6666-4666-8666-666666666666"

NOW = "2026-09-17T13:00:00Z"


def envelope(command_type: str, payload: dict, *, sequence: int, run_id: str = RUN_ID,
             actor_type: str = "AGENT", expected: int | None = None) -> dict:
    command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"harness:{run_id}:{sequence}"))
    command = {
        "contract_version": "sentinel.v0", "wire_type": "command_envelope",
        "command_id": command_id, "command_type": command_type, "run_id": run_id,
        "actor_type": actor_type,
        "actor_id": "OPERATOR_1" if actor_type == "OPERATOR" else f"{actor_type}_1",
        "correlation_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"correlation:{run_id}:{sequence}")),
        "idempotency_key": f"harness-{run_id[:8]}-{sequence}",
        "issued_at": NOW, "payload": payload,
    }
    if expected is not None:
        command["expected_state_version"] = expected
    return command


def perception_result(plan_id: str, plan_hash: str) -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "perception_result",
        "result_id": RESULT_ID, "request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "request")),
        "observation_id": OBSERVATION_ID, "artifact_id": ARTIFACT_ID,
        "artifact_sha256": "a" * 64, "artifact_source_kind": "SIMULATED",
        "judged_path": False, "provider_profile_id": PROVIDER_PROFILE_ID,
        "canonical_class": "rock", "source_class": "rock", "raw_score": 0.99,
        "score_semantics": "provider-defined", "status": "DETECTED", "provider": "simulated",
        "model": "simulated-detector", "model_revision": "v1", "prompt_revision": "v1",
        "transform_revision": "v1", "class_mapping_revision": "v0-rock-only",
        "latency_ms": 1, "diagnostic_artifact_id": None,
        "source_kind": "SIMULATED", "created_at": NOW,
        "station_capture": {
            "plan_id": plan_id, "plan_sha256": plan_hash, "plan_step": 1,
            "station_id": "LEFT_ALLEY_MIDDLE", "zone_id": "LEFT_ALLEY_MIDDLE",
            "marker_id": "TAG_LEFT_MIDDLE", "height_m": 1.2, "artifact_id": ARTIFACT_ID,
            "artifact_sha256": "a" * 64, "capture_index": 1, "captured_at": NOW,
        },
    }


class FakeWorker:
    def __init__(self, gateway: CommandGateway):
        self.gateway = gateway

    def dispatch(self, run_id: str, plan_id: str, station_id: str, expected: int):
        return self.gateway.submit(envelope("INSPECTION_DISPATCH", {
            "section_id": station_id, "mission_id": plan_id, "worker_id": "SIMULATED_WORKER",
        }, sequence=20, run_id=run_id, actor_type="WORKER", expected=expected))

    def preflight(self, run_id: str, plan_id: str, plan_hash: str):
        return SimulatedWorker(self.gateway).report_preflight(
            run_id, plan_id, plan_hash,
            {name: True for name in (
                "WIFI_INTERFACE", "DRONE_LINK", "BATTERY_TELEMETRY", "LIVE_VIDEO",
                "LAYOUT_MARKERS", "BOUNDED_AREA", "HEIGHT_ENVELOPE", "EMERGENCY_LATCH",
            )}, NOW,
        )

    def capture_and_perceive(self, run_id: str, plan_id: str, plan_hash: str):
        return self.gateway.submit(envelope("PERCEPTION_INGEST", {
            "perception_result": perception_result(plan_id, plan_hash),
        }, sequence=21, run_id=run_id, actor_type="WORKER"))


class CoordinatorBoundary:
    """Port adapter used by AgentLoop; mutations still go through gateway."""

    def __init__(self, repository: CoordinatorRepository, gateway: CommandGateway):
        self.repository = repository
        self.gateway = gateway

    def submit(self, command):
        return self.gateway.submit(command)

    def read_state(self, run_id):
        return self.repository.read_state(run_id).to_dict()

    def read_events(self, run_id, after_sequence, limit):
        return self.repository.read_events(run_id, after_sequence, limit)


def open_harness(tmp_path: Path) -> tuple[CoordinatorRepository, CommandGateway]:
    repo = CoordinatorRepository.open(tmp_path / "one-station.sqlite3")
    return repo, CommandGateway(repo)


def close(repo: CoordinatorRepository) -> None:
    repo.close()
