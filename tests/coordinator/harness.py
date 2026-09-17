"""Deterministic coordinator harness for the one-station acceptance slice."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"
ARTIFACT_ID = "33333333-3333-4333-8333-333333333333"
OBSERVATION_ID = "22222222-2222-4222-8222-222222222222"
RESULT_ID = "44444444-4444-4444-8444-444444444444"


def envelope(command_type: str, payload: dict, *, sequence: int, run_id: str = RUN_ID, actor_type: str = "AGENT", expected: int | None = None) -> dict:
    return {
        "contract_version": "sentinel.v0", "wire_type": "command_envelope",
        "command_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"harness:{run_id}:{sequence}")),
        "command_type": command_type, "run_id": run_id, "actor_type": actor_type,
        "actor_id": "OPERATOR_1" if actor_type == "OPERATOR" else "AGENT_1",
        "correlation_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"correlation:{run_id}:{sequence}")),
        "idempotency_key": f"harness-{run_id[:8]}-{sequence}",
        "issued_at": "2026-09-17T13:00:00Z", "payload": payload,
        **({"expected_state_version": expected} if expected is not None else {}),
    }


def seeded_one_station(tmp_path: Path) -> tuple[CoordinatorRepository, CommandGateway]:
    repo = CoordinatorRepository.open(tmp_path / "one-station.sqlite3")
    repo.create_run(RUN_ID, status="RUNNING")
    repo.connection.execute(
        "INSERT INTO artifacts(artifact_id, run_id, media_type, byte_size, sha256, storage_key, created_at, source_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ARTIFACT_ID, RUN_ID, "image/jpeg", 4, "a" * 64, "simulated/frame.jpg", "2026-09-17T13:00:00Z", "SIMULATED"),
    )
    repo.connection.execute(
        "INSERT INTO observations(observation_id, run_id, station_id, artifact_id, observed_at, source_kind) VALUES (?, ?, ?, ?, ?, ?)",
        (OBSERVATION_ID, RUN_ID, "LEFT_ALLEY_MIDDLE", ARTIFACT_ID, "2026-09-17T13:00:00Z", "SIMULATED"),
    )
    repo.connection.execute(
        "INSERT INTO perception_references(result_id, run_id, observation_id, artifact_id, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (RESULT_ID, RUN_ID, OBSERVATION_ID, ARTIFACT_ID, json.dumps({"status": "COMPLETED"}), "2026-09-17T13:00:00Z"),
    )
    repo.connection.commit()
    return repo, CommandGateway(repo)


def close(repo: CoordinatorRepository) -> None:
    repo.close()
