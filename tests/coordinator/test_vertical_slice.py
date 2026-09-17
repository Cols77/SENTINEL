import json
import uuid
from pathlib import Path

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"
OBSERVATION_ID = "22222222-2222-4222-8222-222222222222"
ARTIFACT_ID = "33333333-3333-4333-8333-333333333333"
RESULT_ID = "44444444-4444-4444-8444-444444444444"
TASK_ID = "55555555-5555-4555-8555-555555555555"


def envelope(command_type, payload, expected_state_version, sequence):
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "command_envelope",
        "command_id": f"66666666-6666-4666-8666-66666666666{sequence}",
        "command_type": command_type,
        "run_id": RUN_ID,
        "actor_type": "OPERATOR" if command_type == "OPERATOR_DECIDE" else "AGENT",
        "actor_id": "OPERATOR_1" if command_type == "OPERATOR_DECIDE" else "AGENT_1",
        "correlation_id": f"77777777-7777-4777-8777-77777777777{sequence}",
        "idempotency_key": f"vertical-{sequence}",
        "expected_state_version": expected_state_version,
        "issued_at": "2026-09-17T13:00:00Z",
        "payload": payload,
    }


def test_one_station_simulated_slice_persists_evidence_task_and_assignment(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "vertical-slice.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    repository.connection.execute(
        "INSERT INTO artifacts(artifact_id, run_id, media_type, byte_size, sha256, storage_key, created_at, source_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ARTIFACT_ID, RUN_ID, "image/jpeg", 4, "a" * 64, "simulated/frame.jpg", "2026-09-17T13:00:00Z", "SIMULATED"),
    )
    repository.connection.execute(
        "INSERT INTO observations(observation_id, run_id, station_id, artifact_id, observed_at, source_kind) VALUES (?, ?, ?, ?, ?, ?)",
        (OBSERVATION_ID, RUN_ID, "LEFT_ALLEY_MIDDLE", ARTIFACT_ID, "2026-09-17T13:00:00Z", "SIMULATED"),
    )
    repository.connection.execute(
        "INSERT INTO perception_references(result_id, run_id, observation_id, artifact_id, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (RESULT_ID, RUN_ID, OBSERVATION_ID, ARTIFACT_ID, json.dumps({"status": "COMPLETED"}), "2026-09-17T13:00:00Z"),
    )
    repository.connection.commit()
    gateway = CommandGateway(repository)

    create_command = envelope("TASK_CREATE", {
        "perception_result_id": RESULT_ID,
        "section_id": "LEFT_ALLEY_MIDDLE",
        "evidence_artifact_id": ARTIFACT_ID,
    }, 0, 1)
    gateway.submit(create_command)
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, create_command["command_id"]))
    gateway.submit(envelope("TASK_RANK", {"task_id": task_id}, 1, 2))
    gateway.submit(envelope("ASSIGNMENT_PROPOSE", {
        "task_id": task_id, "patroller_id": "PATROLLER_1", "reason": "highest deterministic score",
    }, 2, 3))
    gateway.submit(envelope("OPERATOR_DECIDE", {
        "decision": "CONFIRM",
        "target": {"kind": "TASK", "task_id": task_id},
    }, 3, 4))

    task = repository.connection.execute(
        "SELECT status, proposed_patroller_id, perception_result_id FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert (task["status"], task["proposed_patroller_id"], task["perception_result_id"]) == (
        "ASSIGNED", "PATROLLER_1", RESULT_ID
    )
    assert [event["event_type"] for event in repository.read_events(RUN_ID, 0, 10)] == [
        "TASK_CREATED", "PATROLLER_ASSIGNMENT_PROPOSED", "PATROLLER_ASSIGNMENT_PROPOSED", "PATROLLER_ASSIGNMENT_CONFIRMED",
    ]
    assert repository.connection.execute("SELECT state_version FROM runs WHERE run_id = ?", (RUN_ID,)).fetchone()[0] == 4
    repository.close()
