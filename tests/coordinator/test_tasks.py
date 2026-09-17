from pathlib import Path

import pytest

from sentinel.coordinator.repository import CoordinatorRepository
from sentinel.coordinator.tasks import AssignmentService, EvidenceTaskService, SimulatedPatroller

from sentinel.coordinator.errors import CoordinatorError

RUN_ID = "11111111-1111-4111-8111-111111111111"
RESULT_ID = "22222222-2222-4222-8222-222222222222"
ARTIFACT_ID = "33333333-3333-4333-8333-333333333333"
TASK_ID = "44444444-4444-4444-8444-444444444444"


def seed(repo):
    repo.create_run(RUN_ID)
    repo.connection.execute("INSERT INTO artifacts VALUES (?, ?, 'image/jpeg', 10, ?, 'x', '2026-09-17T00:00:00Z', 'SIMULATED')", (ARTIFACT_ID, RUN_ID, "a" * 64))
    repo.connection.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?, 'SIMULATED')", ("55555555-5555-4555-8555-555555555555", RUN_ID, "LEFT_ALLEY_MIDDLE", ARTIFACT_ID, "2026-09-17T00:00:00Z"))
    repo.connection.execute("INSERT INTO perception_references VALUES (?, ?, ?, ?, ? ,?)", (RESULT_ID, RUN_ID, "55555555-5555-4555-8555-555555555555", ARTIFACT_ID, '{"status":"COMPLETED"}', "2026-09-17T00:00:00Z"))
    repo.connection.commit()


def test_task_creation_requires_hash_bound_eligible_evidence(tmp_path: Path):
    repo = CoordinatorRepository.open(tmp_path / "tasks.sqlite3")
    seed(repo)
    task = EvidenceTaskService(repo).create_task(RUN_ID, RESULT_ID, "LEFT_ALLEY_MIDDLE", ARTIFACT_ID, task_id=TASK_ID)
    assert task["task_id"] == TASK_ID
    assert task["status"] == "PROPOSED"
    with pytest.raises(CoordinatorError) as caught:
        EvidenceTaskService(repo).create_task(RUN_ID, RESULT_ID, "RIGHT_ALLEY_MIDDLE", ARTIFACT_ID)
    assert caught.value.code == "INVALID_STATE_TRANSITION"


def test_simulated_patroller_ranking_is_deterministic_and_assignment_is_bound(tmp_path: Path):
    repo = CoordinatorRepository.open(tmp_path / "ranking.sqlite3")
    seed(repo)
    EvidenceTaskService(repo).create_task(RUN_ID, RESULT_ID, "LEFT_ALLEY_MIDDLE", ARTIFACT_ID, task_id=TASK_ID)
    ranking = SimulatedPatroller(repo).rank_for_task(TASK_ID, {"PATROLLER_2": 0.7, "PATROLLER_1": 0.9})
    assert [item["patroller_id"] for item in ranking] == ["PATROLLER_1", "PATROLLER_2"]
    result = AssignmentService(repo).propose(TASK_ID, "PATROLLER_1", "rank-1 candidate")
    assert result["status"] == "PROPOSED"
    with pytest.raises(CoordinatorError) as caught:
        AssignmentService(repo).propose(TASK_ID, "PATROLLER_9", "not ranked")
    assert caught.value.code == "INVALID_STATE_TRANSITION"


def test_direct_assignment_decision_validates_decision_and_actor(tmp_path: Path):
    repo = CoordinatorRepository.open(tmp_path / "decision.sqlite3")
    seed(repo)
    EvidenceTaskService(repo).create_task(RUN_ID, RESULT_ID, "LEFT_ALLEY_MIDDLE", ARTIFACT_ID, task_id=TASK_ID)
    SimulatedPatroller(repo).rank_for_task(TASK_ID, {"PATROLLER_1": 0.9})
    AssignmentService(repo).propose(TASK_ID, "PATROLLER_1", "rank-1 candidate")
    repo.append_event(RUN_ID, "TASK_CREATED", {"task_id": TASK_ID})

    with pytest.raises(CoordinatorError) as caught:
        AssignmentService(repo).decide(RUN_ID, TASK_ID, "MAYBE", actor_id="OPERATOR_1", expected_state_version=1)
    assert caught.value.code == "INVALID_CONTRACT"

    with pytest.raises(CoordinatorError) as caught:
        AssignmentService(repo).decide(RUN_ID, TASK_ID, "CONFIRM", actor_id="AGENT_1", expected_state_version=1)
    assert caught.value.code == "UNAUTHORIZED_ACTOR"
