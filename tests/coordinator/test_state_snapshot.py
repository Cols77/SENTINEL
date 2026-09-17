from pathlib import Path
from uuid import UUID

from sentinel.contracts import StateSnapshot, validate_document
from sentinel.coordinator.repository import CoordinatorRepository


RUN_ID = "11111111-1111-4111-8111-111111111111"


def test_state_snapshot_versions_and_legal_actions_are_derived(tmp_path: Path):
    repository = CoordinatorRepository.open(tmp_path / "state.sqlite3")
    repository.create_run(RUN_ID)

    initial = repository.read_state(RUN_ID)
    assert isinstance(initial, StateSnapshot)
    assert initial.run_status == "NEW"
    assert initial.state_version == 0
    assert initial.legal_operator_actions == []
    validate_document(initial.to_dict())

    repository.append_event(RUN_ID, "RUN_CREATED", {"run_id": RUN_ID})
    advanced = repository.read_state(RUN_ID)
    assert advanced.state_version == 1
    assert advanced.latest_event_sequence == 1

    repository.set_pending_approval(RUN_ID, "PLAN")
    pending = repository.read_state(RUN_ID)
    assert pending.run_status == "WAITING_FOR_APPROVAL"
    assert pending.legal_operator_actions == ["CONFIRM", "REJECT"]
    validate_document(pending.to_dict())
    repository.close()
