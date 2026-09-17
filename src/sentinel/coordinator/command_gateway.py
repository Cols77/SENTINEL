"""Single typed command gateway for coordinator mutations."""

from __future__ import annotations

from typing import Any, Mapping

from sentinel.contracts import ContractValidationError, validate_document

from .errors import CoordinatorError


_EVENT_TYPES = {
    "RUN_CREATE": "RUN_CREATED",
    "AGENT_INTENT_PROPOSE": "AGENT_INTENT_PROPOSED",
    "INSPECTION_DISPATCH": "DRONE_DISPATCH_REQUESTED",
    "PERCEPTION_INGEST": "DETECTION_RECORDED",
    "TASK_CREATE": "TASK_CREATED",
    "TASK_RANK": "PATROLLER_ASSIGNMENT_PROPOSED",
    "OPERATOR_DECIDE": "PLAN_APPROVED",
    "EMERGENCY_SIGNAL": "EMERGENCY_LATCHED",
    "OPERATION_RECONCILE": "OPERATION_RECONCILED_SUCCEEDED",
    "FORCE_RUN_CREATE": "RUN_CREATED",
}


class CommandGateway:
    """Validate and submit every coordinator command through one entry point."""

    def __init__(self, repository):
        self.repository = repository

    def submit(self, command: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(command, Mapping):
            raise CoordinatorError("INVALID_CONTRACT", "command must be an object")
        try:
            validate_document(command)
        except ContractValidationError as exc:
            raise CoordinatorError("INVALID_CONTRACT", str(exc)) from exc

        run_id = command["run_id"]
        row = self.repository.connection.execute(
            "SELECT state_version, status, emergency_latched FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise CoordinatorError("INVALID_STATE_TRANSITION", "run does not exist")
        expected = command.get("expected_state_version")
        if expected is not None and expected != row["state_version"]:
            raise CoordinatorError(
                "STALE_STATE_VERSION",
                f"expected {expected}, current {row['state_version']}",
            )
        command_type = command["command_type"]
        if row["emergency_latched"] and command_type != "EMERGENCY_SIGNAL":
            raise CoordinatorError("EMERGENCY_LATCHED", "ordinary commands are disabled")
        if command_type == "OPERATOR_DECIDE" and command["actor_type"] != "OPERATOR":
            raise CoordinatorError("UNAUTHORIZED_ACTOR", "operator decisions require an operator actor")
        if command_type == "INSPECTION_DISPATCH":
            approved = self.repository.connection.execute(
                """SELECT 1 FROM plan_approvals pa JOIN plans p ON p.plan_id = pa.plan_id
                   WHERE pa.run_id = ? AND pa.action = 'CONFIRM' AND pa.plan_sha256 = p.plan_sha256
                   LIMIT 1""",
                (run_id,),
            ).fetchone()
            if approved is None:
                raise CoordinatorError(
                    "INVALID_STATE_TRANSITION",
                    "inspection dispatch requires an approved immutable plan",
                )
        event_type = _EVENT_TYPES[command_type]
        event = self.repository.append_event(
            run_id,
            event_type,
            dict(command["payload"]),
            actor_type=command["actor_type"],
            actor_id=command["actor_id"],
            correlation_id=command["correlation_id"],
        )
        return {
            "contract_version": "sentinel.v0",
            "wire_type": "result_envelope",
            "command_id": command["command_id"],
            "run_id": run_id,
            "status": "COMPLETED",
            "state_version": event["state_version"],
            "event_id": event["event_id"],
            "error_code": None,
            "payload": {"accepted_command_type": command_type},
            "recorded_at": event["recorded_at"],
        }
