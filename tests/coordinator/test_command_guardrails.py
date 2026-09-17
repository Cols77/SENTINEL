from pathlib import Path

import pytest

from sentinel.coordinator.command_gateway import CommandGateway
from sentinel.coordinator.errors import CoordinatorError
from sentinel.coordinator.repository import CoordinatorRepository

RUN_ID = "11111111-1111-4111-8111-111111111111"
COMMAND_ID = "22222222-2222-4222-8222-222222222222"
CORRELATION_ID = "33333333-3333-4333-8333-333333333333"
PLAN_ID = "44444444-4444-4444-8444-444444444444"


def command(command_type, actor_type="OPERATOR", expected_state_version=0, payload=None):
    return {
        "contract_version": "sentinel.v0",
        "wire_type": "command_envelope",
        "command_id": COMMAND_ID,
        "command_type": command_type,
        "run_id": RUN_ID,
        "actor_type": actor_type,
        "actor_id": "OPERATOR_1",
        "correlation_id": CORRELATION_ID,
        "idempotency_key": "command-1",
        "expected_state_version": expected_state_version,
        "issued_at": "2026-09-17T13:00:00Z",
        "payload": payload or {},
    }


def operator_payload():
    return {
        "decision": "CONFIRM",
        "target": {
            "kind": "PLAN",
            "plan_reference": {
            "plan_id": PLAN_ID,
            "catalogue_id": "SURVEY_CATALOGUE_V1",
            "catalogue_revision": "REV_1",
            "plan_sha256": "a" * 64,
            "station_ids": ["LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE"],
            },
        },
    }


def test_malformed_typed_command_is_rejected_without_mutation(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID)
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("OPERATOR_DECIDE", payload={"decision": "CONFIRM"}))

    assert caught.value.code == "INVALID_CONTRACT"
    assert repository.read_events(RUN_ID, 0, 10) == []
    repository.close()


def test_operator_decision_rejects_stale_state_version(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID)
    repository.append_event(RUN_ID, "RUN_CREATED", {"run_id": RUN_ID})
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("OPERATOR_DECIDE", expected_state_version=0, payload=operator_payload()))

    assert caught.value.code == "STALE_STATE_VERSION"
    assert len(repository.read_events(RUN_ID, 0, 10)) == 1
    repository.close()


def test_agent_cannot_submit_operator_decision(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID)
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("OPERATOR_DECIDE", actor_type="AGENT", payload=operator_payload()))

    assert caught.value.code == "UNAUTHORIZED_ACTOR"
    repository.close()


def test_dispatch_requires_approved_plan_binding(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    gateway = CommandGateway(repository)

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("INSPECTION_DISPATCH", payload={
            "section_id": "LEFT_ALLEY_MIDDLE",
            "mission_id": PLAN_ID,
            "worker_id": "WORKER_FAKE",
        }))

    assert caught.value.code == "INVALID_STATE_TRANSITION"
    assert repository.read_events(RUN_ID, 0, 10) == []
    repository.close()


def test_task_rank_gateway_persists_ranking_and_event(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "gateway.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    repository.connection.execute("INSERT INTO tasks(task_id, run_id, station_id, status, created_at) VALUES (?, ?, ?, 'PROPOSED', ?)", (PLAN_ID, RUN_ID, "LEFT_ALLEY_MIDDLE", "2026-09-17T00:00:00Z"))
    repository.connection.commit()
    gateway = CommandGateway(repository)
    ranked = command("TASK_RANK", actor_type="AGENT", payload={"task_id": PLAN_ID})

    result = gateway.submit(ranked)

    assert result["status"] == "COMPLETED"
    assert [row["patroller_id"] for row in repository.connection.execute("SELECT * FROM task_rankings ORDER BY rank_no").fetchall()] == ["PATROLLER_1"]
    assert repository.read_events(RUN_ID, 0, 10)[0]["event_type"] == "PATROLLER_ASSIGNMENT_PROPOSED"


def test_emergency_signal_persistently_latches_and_blocks_ordinary_commands(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "emergency.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    gateway = CommandGateway(repository)
    signal = command(
        "EMERGENCY_SIGNAL",
        actor_type="OPERATOR",
        payload={"signal_id": "88888888-8888-4888-8888-888888888888"},
    )

    result = gateway.submit(signal)

    assert result["status"] == "COMPLETED"
    row = repository.connection.execute(
        "SELECT status, emergency_latched FROM runs WHERE run_id = ?", (RUN_ID,)
    ).fetchone()
    assert (row["status"], row["emergency_latched"]) == ("EMERGENCY", 1)
    record = repository.connection.execute(
        "SELECT signal_id, worker_id FROM emergency_records WHERE run_id = ?", (RUN_ID,)
    ).fetchone()
    assert (record["signal_id"], record["worker_id"]) == (signal["payload"]["signal_id"], "OPERATOR_1")
    assert repository.read_events(RUN_ID, 0, 10)[0]["event_type"] == "EMERGENCY_LATCHED"

    with pytest.raises(CoordinatorError) as caught:
        gateway.submit(command("AGENT_INTENT_PROPOSE", actor_type="AGENT", expected_state_version=1, payload={"intent": "NO_ACTION_REQUIRED"}))
    assert caught.value.code == "EMERGENCY_LATCHED"
    repository.close()


def test_operator_rejection_leaves_run_refinement_eligible(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "reject.sqlite3")
    repository.create_run(RUN_ID, status="RUNNING")
    repository.connection.execute(
        """INSERT INTO tasks(task_id, run_id, station_id, status, proposed_patroller_id, created_at)
           VALUES (?, ?, ?, 'ASSIGNMENT_PROPOSED', ?, ?)""",
        (PLAN_ID, RUN_ID, "LEFT_ALLEY_MIDDLE", "PATROLLER_1", "2026-09-17T00:00:00Z"),
    )
    repository.connection.commit()

    CommandGateway(repository).submit(command("OPERATOR_DECIDE", payload={
        "decision": "REJECT", "target": {"kind": "TASK", "task_id": PLAN_ID},
    }))

    assert repository.connection.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (PLAN_ID,)
    ).fetchone()[0] == "PROPOSED"
    assert repository.connection.execute(
        "SELECT status FROM runs WHERE run_id = ?", (RUN_ID,)
    ).fetchone()[0] == "RUNNING"
    repository.close()


def test_emergency_latch_survives_repository_reopen(tmp_path: Path):
    database = tmp_path / "reopen-emergency.sqlite3"
    repository = CoordinatorRepository.open(database)
    repository.create_run(RUN_ID, status="RUNNING")
    gateway = CommandGateway(repository)
    gateway.submit(command("EMERGENCY_SIGNAL", payload={"signal_id": "88888888-8888-4888-8888-888888888888"}))
    repository.close()

    reopened = CoordinatorRepository.open(database)
    row = reopened.connection.execute(
        "SELECT status, emergency_latched FROM runs WHERE run_id = ?", (RUN_ID,)
    ).fetchone()
    assert (row["status"], row["emergency_latched"]) == ("EMERGENCY", 1)
    with pytest.raises(CoordinatorError) as caught:
        CommandGateway(reopened).submit(command("TASK_RANK", actor_type="AGENT", expected_state_version=1, payload={"task_id": PLAN_ID}))
    assert caught.value.code == "EMERGENCY_LATCHED"
    reopened.close()


@pytest.mark.parametrize("status", ["FAILED", "COMPLETED", "EMERGENCY"])
def test_terminal_run_rejects_ordinary_mutation(tmp_path: Path, status: str):
    repository = CoordinatorRepository.open(tmp_path / f"terminal-{status}.sqlite3")
    repository.create_run(RUN_ID, status=status)
    if status == "EMERGENCY":
        repository.connection.execute(
            "UPDATE runs SET emergency_latched = 0 WHERE run_id = ?", (RUN_ID,)
        )
        repository.connection.execute(
            "INSERT INTO tasks(task_id, run_id, station_id, status, created_at) VALUES (?, ?, ?, 'PROPOSED', ?)",
            (PLAN_ID, RUN_ID, "LEFT_ALLEY_MIDDLE", "2026-09-17T00:00:00Z"),
        )
        repository.connection.commit()

    with pytest.raises(CoordinatorError) as caught:
        CommandGateway(repository).submit(command(
            "TASK_RANK", actor_type="AGENT", payload={"task_id": PLAN_ID},
        ))

    assert caught.value.code == "INVALID_STATE_TRANSITION"
    assert repository.read_events(RUN_ID, 0, 10) == []
    repository.close()


def test_terminal_run_rejects_new_emergency_signal_without_overwrite(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "terminal-emergency.sqlite3")
    repository.create_run(RUN_ID, status="COMPLETED")

    with pytest.raises(CoordinatorError) as caught:
        CommandGateway(repository).submit(command(
            "EMERGENCY_SIGNAL", payload={"signal_id": "88888888-8888-4888-8888-888888888888"},
        ))

    assert caught.value.code == "INVALID_STATE_TRANSITION"
    row = repository.connection.execute(
        "SELECT status, emergency_latched FROM runs WHERE run_id = ?", (RUN_ID,)
    ).fetchone()
    assert (row["status"], row["emergency_latched"]) == ("COMPLETED", 0)
    assert repository.connection.execute("SELECT COUNT(*) FROM emergency_records").fetchone()[0] == 0
    repository.close()


def test_emergency_status_rejects_new_emergency_signal_without_overwrite(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "terminal-emergency-status.sqlite3")
    repository.create_run(RUN_ID, status="EMERGENCY")
    repository.connection.execute(
        "UPDATE runs SET emergency_latched = 0 WHERE run_id = ?", (RUN_ID,)
    )
    repository.connection.commit()

    with pytest.raises(CoordinatorError) as caught:
        CommandGateway(repository).submit(command(
            "EMERGENCY_SIGNAL", payload={"signal_id": "88888888-8888-4888-8888-888888888888"},
        ))

    assert caught.value.code == "INVALID_STATE_TRANSITION"
    row = repository.connection.execute(
        "SELECT status, emergency_latched FROM runs WHERE run_id = ?", (RUN_ID,)
    ).fetchone()
    assert (row["status"], row["emergency_latched"]) == ("EMERGENCY", 0)
    assert repository.connection.execute("SELECT COUNT(*) FROM emergency_records").fetchone()[0] == 0
    repository.close()
