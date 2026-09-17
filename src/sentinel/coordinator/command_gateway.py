"""Single typed command gateway for coordinator mutations."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Mapping

from sentinel.contracts import ContractValidationError, validate_document, validate_preflight_report

from .errors import CoordinatorError
from .approval import PlanApprovalService
from .tasks import AssignmentService, EvidenceTaskService
from .tasks import SimulatedPatroller
from .catalogue import CatalogueResolver
from .plans import PlanResolver


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
_TERMINAL_RUN_STATES = {"FAILED", "COMPLETED", "EMERGENCY"}


class CommandGateway:
    """Validate and submit every coordinator command through one entry point."""

    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _catalogue_fixture() -> Path:
        return Path(__file__).resolve().parents[3] / "docs" / "superpowers" / "specs" / "contracts" / "fixtures" / "survey-catalogue-v1.fixture.json"

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
        if row["status"] in _TERMINAL_RUN_STATES and command_type == "EMERGENCY_SIGNAL":
            raise CoordinatorError("INVALID_STATE_TRANSITION", "terminal runs cannot be overwritten")
        if row["status"] in {"FAILED", "COMPLETED"} or (
            row["status"] == "EMERGENCY" and not row["emergency_latched"]
        ):
            raise CoordinatorError("INVALID_STATE_TRANSITION", "terminal runs reject ordinary commands")
        if row["emergency_latched"] and command_type != "EMERGENCY_SIGNAL":
            raise CoordinatorError("EMERGENCY_LATCHED", "ordinary commands are disabled")
        if command_type == "OPERATOR_DECIDE" and command["actor_type"] != "OPERATOR":
            raise CoordinatorError("UNAUTHORIZED_ACTOR", "operator decisions require an operator actor")
        if command_type == "PREFLIGHT_INGEST" and command["actor_type"] != "WORKER":
            raise CoordinatorError("UNAUTHORIZED_ACTOR", "preflight ingress requires a worker actor")
        if command_type == "OPERATOR_DECIDE":
            target = command["payload"]["target"]
            if row["status"] != "WAITING_FOR_APPROVAL":
                raise CoordinatorError("INVALID_STATE_TRANSITION", "operator decisions require a waiting-for-approval run")
            if target["kind"] == "PLAN":
                reference = target["plan_reference"]
                pending = self.repository.connection.execute(
                    """SELECT station_ids_json FROM plans
                       WHERE run_id = ? AND plan_id = ? AND plan_sha256 = ?
                         AND catalogue_id = ? AND catalogue_revision = ?""",
                    (run_id, reference["plan_id"], reference["plan_sha256"], reference["catalogue_id"],
                     reference["catalogue_revision"]),
                ).fetchone()
                if pending is None or json.loads(pending["station_ids_json"]) != reference["station_ids"]:
                    raise CoordinatorError("INVALID_STATE_TRANSITION", "plan is not the pending approval target")
            else:
                pending = self.repository.connection.execute(
                    """SELECT 1 FROM tasks
                       WHERE run_id = ? AND task_id = ? AND status = 'ASSIGNMENT_PROPOSED'
                         AND proposed_patroller_id IS NOT NULL""",
                    (run_id, target["task_id"]),
                ).fetchone()
                if pending is None:
                    raise CoordinatorError("INVALID_STATE_TRANSITION", "task is not the pending approval target")
        if command_type == "INSPECTION_DISPATCH":
            payload = command["payload"]
            approved = self.repository.connection.execute(
                """SELECT p.station_ids_json
                   FROM plan_approvals pa
                   JOIN plans p ON p.plan_id = pa.plan_id AND p.run_id = pa.run_id
                                AND p.plan_sha256 = pa.plan_sha256
                   JOIN preflight_reports f ON f.run_id = pa.run_id AND f.plan_id = pa.plan_id
                                            AND f.plan_sha256 = pa.plan_sha256 AND f.passed = 1
                   WHERE pa.run_id = ? AND pa.plan_id = ? AND pa.action = 'CONFIRM'""",
                (run_id, payload["mission_id"]),
            ).fetchall()
            if not any(payload["section_id"] in json.loads(row["station_ids_json"]) for row in approved):
                raise CoordinatorError(
                    "INVALID_STATE_TRANSITION",
                    "inspection dispatch section is not bound to an approved immutable plan",
                )
        if command_type == "PREFLIGHT_INGEST":
            report = command["payload"]["preflight_report"]
            try:
                validate_preflight_report(report)
            except ContractValidationError as exc:
                raise CoordinatorError("INVALID_PREFLIGHT_REPORT", str(exc)) from exc
            if report["run_id"] != run_id:
                raise CoordinatorError("INVALID_PREFLIGHT_REPORT", "preflight run binding mismatch")
            approved = self.repository.connection.execute(
                """SELECT 1 FROM plan_approvals pa JOIN plans p ON p.plan_id = pa.plan_id
                   WHERE pa.run_id = ? AND pa.plan_id = ? AND pa.plan_sha256 = ?
                     AND p.plan_sha256 = pa.plan_sha256 AND pa.action = 'CONFIRM'""",
                (run_id, report["plan_id"], report["plan_sha256"]),
            ).fetchone()
            if approved is None:
                raise CoordinatorError("INVALID_PREFLIGHT_REPORT", "preflight plan is not the approved immutable plan")
        with self.repository.connection:
            if command_type == "AGENT_INTENT_PROPOSE" and command["payload"]["intent"] in {"INSPECT_SECTION", "REQUEST_OPERATOR_APPROVAL"}:
                entry_id = command["payload"]["catalogue_plan_id"]
                plan_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{entry_id}"))
                PlanResolver(self.repository, CatalogueResolver.from_fixture(self._catalogue_fixture())).resolve(
                    run_id, plan_id, "SURVEY_CATALOGUE_V1", entry_id
                )
                self.repository.append_event(
                    run_id, "PLAN_PROPOSED", {"plan_id": plan_id, "catalogue_plan_id": entry_id},
                    actor_type=command["actor_type"], actor_id=command["actor_id"],
                    correlation_id=command["correlation_id"], commit=False,
                )
                self.repository.append_event(
                    run_id, "PLAN_CREATED", {"plan_id": plan_id, "catalogue_plan_id": entry_id},
                    actor_type=command["actor_type"], actor_id=command["actor_id"],
                    correlation_id=command["correlation_id"], commit=False,
                )
                self.repository.connection.execute(
                    "UPDATE runs SET status = 'WAITING_FOR_APPROVAL' WHERE run_id = ?", (run_id,)
                )
            if command_type == "PREFLIGHT_INGEST":
                report = command["payload"]["preflight_report"]
                self.repository.connection.execute(
                    """INSERT INTO preflight_reports
                       (report_id, run_id, plan_id, plan_sha256, passed, report_json, checked_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (report["report_id"], run_id, report["plan_id"], report["plan_sha256"],
                     int(report["passed"]), json.dumps(report, sort_keys=True, separators=(",", ":")), report["checked_at"]),
                )
                if not report["passed"]:
                    self.repository.connection.execute("UPDATE runs SET status = 'FAILED' WHERE run_id = ?", (run_id,))
            if command_type == "EMERGENCY_SIGNAL":
                signal_id = command["payload"]["signal_id"]
                self.repository.connection.execute(
                    """INSERT INTO emergency_records(
                       signal_id, run_id, worker_id, latched_at, source_kind, reason_code
                       ) VALUES (?, ?, ?, ?, 'SIMULATED', 'EMERGENCY_SIGNAL')""",
                    (signal_id, run_id, command["actor_id"], command["issued_at"]),
                )
                self.repository.connection.execute(
                    "UPDATE runs SET status = 'EMERGENCY', emergency_latched = 1 WHERE run_id = ?",
                    (run_id,),
                )
            if command_type == "TASK_CREATE":
                payload = command["payload"]
                EvidenceTaskService(self.repository).create_task(
                    run_id, payload["perception_result_id"], payload["section_id"],
                    payload["evidence_artifact_id"],
                    task_id=str(uuid.uuid5(uuid.NAMESPACE_URL, command["command_id"])), commit=False,
                )
            if command_type == "TASK_RANK":
                payload = command["payload"]
                SimulatedPatroller(self.repository).rank_for_task(payload["task_id"], payload.get("scores", {"PATROLLER_1": 0.0}), commit=False)
            if command_type == "ASSIGNMENT_PROPOSE":
                payload = command["payload"]
                AssignmentService(self.repository).propose(payload["task_id"], payload["patroller_id"], payload["reason"], commit=False)
                self.repository.connection.execute(
                    "UPDATE runs SET status = 'WAITING_FOR_APPROVAL' WHERE run_id = ?", (run_id,)
                )
            if command_type == "OPERATOR_DECIDE" and command["payload"]["target"]["kind"] == "PLAN":
                target = command["payload"]["target"]["plan_reference"]
                plan = self.repository.connection.execute(
                    "SELECT plan_id, plan_sha256, catalogue_id, catalogue_revision, station_ids_json FROM plans WHERE run_id = ? AND plan_id = ?",
                    (run_id, target["plan_id"]),
                ).fetchone()
                if plan is None or plan["plan_sha256"] != target["plan_sha256"] or plan["catalogue_id"] != target["catalogue_id"] or plan["catalogue_revision"] != target["catalogue_revision"] or json.loads(plan["station_ids_json"]) != target["station_ids"]:
                    raise CoordinatorError("PLAN_HASH_MISMATCH", "approval does not bind the persisted plan")
                approval_id = str(uuid.uuid4())
                self.repository.connection.execute(
                    """INSERT INTO plan_approvals
                       (approval_id, run_id, plan_id, plan_sha256, actor_id, action, expected_state_version, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (approval_id, run_id, target["plan_id"], target["plan_sha256"], command["actor_id"],
                     command["payload"]["decision"], expected, command["issued_at"]),
                )
                self.repository.connection.execute(
                    "UPDATE runs SET status = ? WHERE run_id = ?",
                    ("WAITING_FOR_OBSERVATION" if command["payload"]["decision"] == "CONFIRM" else "RUNNING", run_id),
                )
            if command_type == "OPERATOR_DECIDE" and command["payload"]["target"]["kind"] == "TASK":
                payload = command["payload"]
                AssignmentService(self.repository).decide(
                    run_id, payload["target"]["task_id"], payload["decision"],
                    actor_id=command["actor_id"], expected_state_version=expected, commit=False,
                )
                task = self.repository.connection.execute(
                    "SELECT station_id FROM tasks WHERE task_id = ? AND run_id = ?",
                    (payload["target"]["task_id"], run_id),
                ).fetchone()
                if payload["decision"] == "REJECT":
                    run_status = "RUNNING"
                else:
                    approved = self.repository.connection.execute(
                        """SELECT p.station_ids_json FROM plans p
                           JOIN plan_approvals pa ON pa.plan_id = p.plan_id AND pa.plan_sha256 = p.plan_sha256
                           WHERE pa.run_id = ? AND pa.action = 'CONFIRM'""", (run_id,)
                    ).fetchall()
                    station_ids = set()
                    for plan_row in approved:
                        station_ids.update(json.loads(plan_row["station_ids_json"]))
                    if station_ids and task["station_id"] not in station_ids:
                        raise CoordinatorError("INVALID_STATE_TRANSITION", "task is not part of the approved plan")
                    assigned = self.repository.connection.execute(
                        "SELECT COUNT(DISTINCT station_id) FROM tasks WHERE run_id = ? AND status = 'ASSIGNED'",
                        (run_id,),
                    ).fetchone()[0]
                    run_status = "COMPLETED" if assigned >= (len(station_ids) or 1) else "RUNNING"
                self.repository.connection.execute(
                    "UPDATE runs SET status = ? WHERE run_id = ?", (run_status, run_id)
                )
            event_type = _EVENT_TYPES[command_type]
            if command_type == "OPERATOR_DECIDE":
                target_kind = command["payload"]["target"]["kind"]
                if target_kind == "TASK":
                    event_type = "PATROLLER_ASSIGNMENT_CONFIRMED" if command["payload"]["decision"] == "CONFIRM" else "OPERATOR_ASSIGNMENT_REJECTED"
                elif command["payload"]["decision"] == "REJECT":
                    event_type = "PLAN_REJECTED"
            event = self.repository.append_event(
                run_id,
                event_type,
                dict(command["payload"]),
                actor_type=command["actor_type"],
                actor_id=command["actor_id"],
                correlation_id=command["correlation_id"],
                commit=False,
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
