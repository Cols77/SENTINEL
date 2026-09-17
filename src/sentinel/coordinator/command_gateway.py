"""Single typed command gateway for coordinator mutations."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from sentinel.contracts import ContractValidationError, validate_document

from .errors import CoordinatorError
from .approval import PlanApprovalService


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
    "PREFLIGHT_INGEST": "PREFLIGHT_REPORTED",
    "ASSIGNMENT_PROPOSE": "PATROLLER_ASSIGNMENT_PROPOSED",
}


class CommandGateway:
    """Validate and submit every coordinator command through one entry point."""

    def __init__(self, repository):
        self.repository = repository

    def submit(self, command: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(command, Mapping):
            raise CoordinatorError("INVALID_CONTRACT", "command must be an object")
        # Idempotency is checked before payload validation so a reused scope is
        # never reinterpreted as a different command merely because its payload
        # no longer validates under the current contract revision.
        if {"run_id", "actor_type", "actor_id", "idempotency_key"} <= command.keys():
            prior = self.repository.connection.execute(
                """SELECT command_type, expected_state_version, payload_sha256, result_json
                   FROM idempotency_keys
                   WHERE run_id = ? AND actor_type = ? AND actor_id = ? AND idempotency_key = ?""",
                (command["run_id"], command["actor_type"], command["actor_id"], command["idempotency_key"]),
            ).fetchone()
            if prior is not None:
                payload_json = json.dumps(command.get("payload", {}), sort_keys=True, separators=(",", ":"))
                payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                fingerprint = (command.get("command_type"), command.get("expected_state_version"), payload_sha256)
                if fingerprint != (prior["command_type"], prior["expected_state_version"], prior["payload_sha256"]):
                    raise CoordinatorError("IDEMPOTENCY_KEY_CONFLICT", "scope was reused with a different command fingerprint")
                return json.loads(prior["result_json"])
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
        command_type = command["command_type"]
        payload_json = json.dumps(command["payload"], sort_keys=True, separators=(",", ":"))
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        prior = self.repository.connection.execute(
            """SELECT command_type, expected_state_version, payload_sha256, result_json
               FROM idempotency_keys
               WHERE run_id = ? AND actor_type = ? AND actor_id = ? AND idempotency_key = ?""",
            (run_id, command["actor_type"], command["actor_id"], command["idempotency_key"]),
        ).fetchone()
        if prior is not None:
            fingerprint = (command_type, expected, payload_sha256)
            if fingerprint != (prior["command_type"], prior["expected_state_version"], prior["payload_sha256"]):
                raise CoordinatorError(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "scope was reused with a different command fingerprint",
                )
            return json.loads(prior["result_json"])
        if expected is not None and expected != row["state_version"]:
            raise CoordinatorError(
                "STALE_STATE_VERSION",
                f"expected {expected}, current {row['state_version']}",
            )
        if row["emergency_latched"] and command_type != "EMERGENCY_SIGNAL":
            raise CoordinatorError("EMERGENCY_LATCHED", "ordinary commands are disabled")
        if command_type == "OPERATOR_DECIDE" and command["actor_type"] != "OPERATOR":
            raise CoordinatorError("UNAUTHORIZED_ACTOR", "operator decisions require an operator actor")
        if command_type == "PREFLIGHT_INGEST" and command["actor_type"] != "WORKER":
            raise CoordinatorError("UNAUTHORIZED_ACTOR", "preflight ingress requires a worker actor")
        if command_type == "INSPECTION_DISPATCH":
            payload = command["payload"]
            if {"plan_id", "plan_sha256"} <= payload.keys():
                PlanApprovalService(self.repository).require_dispatch_approval(
                    run_id, payload["plan_id"], payload["plan_sha256"]
                )
            approved = self.repository.connection.execute(
                """SELECT 1 FROM plan_approvals pa JOIN plans p ON p.plan_id = pa.plan_id
                   WHERE pa.run_id = ? AND pa.action = 'CONFIRM' AND pa.plan_sha256 = p.plan_sha256
                   LIMIT 1""",
                (run_id,),
            ).fetchone()
            if approved is None and not ({"plan_id", "plan_sha256"} <= payload.keys()):
                raise CoordinatorError(
                    "INVALID_STATE_TRANSITION",
                    "inspection dispatch requires an approved immutable plan",
                )
        if command_type == "PREFLIGHT_INGEST":
            report = command["payload"]["preflight_report"]
            expected_names = {"WIFI_INTERFACE", "DRONE_LINK", "BATTERY_TELEMETRY", "LIVE_VIDEO", "LAYOUT_MARKERS", "BOUNDED_AREA", "HEIGHT_ENVELOPE", "EMERGENCY_LATCH"}
            checks = report.get("checks", [])
            names = {item.get("name") for item in checks if isinstance(item, Mapping)}
            if len(checks) != 8 or names != expected_names:
                raise CoordinatorError("INVALID_PREFLIGHT_REPORT", "preflight must contain exactly eight canonical checks")
            if report["run_id"] != run_id:
                raise CoordinatorError("INVALID_PREFLIGHT_REPORT", "preflight run binding mismatch")
        event_type = _EVENT_TYPES[command_type]
        event = self.repository.append_event(
            run_id,
            event_type,
            dict(command["payload"]),
            actor_type=command["actor_type"],
            actor_id=command["actor_id"],
            correlation_id=command["correlation_id"],
        )
        result = {
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
        with self.repository.connection:
            self.repository.connection.execute(
                """INSERT INTO idempotency_keys(
                   run_id, actor_type, actor_id, idempotency_key, command_type,
                   expected_state_version, payload_sha256, result_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    command["actor_type"],
                    command["actor_id"],
                    command["idempotency_key"],
                    command_type,
                    expected,
                    payload_sha256,
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                ),
            )
        return result
