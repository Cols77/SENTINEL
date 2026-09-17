"""Canonical immutable preflight ingress and deterministic worker."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sentinel.contracts import ContractValidationError, validate_document

from .errors import CoordinatorError

CHECK_NAMES = (
    "WIFI_INTERFACE", "DRONE_LINK", "BATTERY_TELEMETRY", "LIVE_VIDEO",
    "LAYOUT_MARKERS", "BOUNDED_AREA", "HEIGHT_ENVELOPE", "EMERGENCY_LATCH",
)


@dataclass(frozen=True)
class PreflightReport:
    report_id: str
    run_id: str
    plan_id: str
    plan_sha256: str
    checks: tuple[Mapping[str, Any], ...]
    checked_at: str

    @property
    def passed(self) -> bool:
        return all(bool(check["passed"]) for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "passed": self.passed,
            "checks": [dict(check) for check in self.checks],
            "checked_at": self.checked_at,
        }


def make_preflight_report(run_id: str, plan_id: str, plan_sha256: str, checks: Mapping[str, bool], checked_at: str) -> PreflightReport:
    if tuple(checks) != CHECK_NAMES:
        raise CoordinatorError("INVALID_PREFLIGHT_REPORT", "preflight must contain exactly the eight canonical checks")
    report = PreflightReport(
        report_id=str(uuid.uuid4()),
        run_id=run_id,
        plan_id=plan_id,
        plan_sha256=plan_sha256,
        checks=tuple({"name": name, "passed": bool(checks[name]), "checked_at": checked_at} for name in CHECK_NAMES),
        checked_at=checked_at,
    )
    try:
        validate_document({"contract_version": "sentinel.v0", "wire_type": "worker_dispatch", "operation_id": str(uuid.uuid4()), "run_id": run_id, "mission_id": str(uuid.uuid4()), "worker_id": "SIMULATED_WORKER", "idempotency_key": "preflight-check-1", "choreography_id": "SURVEY_V0", "plan_reference": {"plan_id": plan_id, "catalogue_id": "SURVEY_CATALOGUE_V1", "catalogue_revision": "REV_1", "plan_sha256": plan_sha256, "station_ids": ["LEFT_ALLEY_MIDDLE"]}, "approval_reference": {"approval_id": str(uuid.uuid4()), "plan_id": plan_id, "plan_sha256": plan_sha256, "worker_id": "SIMULATED_WORKER", "choreography_id": "SURVEY_V0", "run_id": run_id, "expected_state_version": 0, "approved_at": checked_at, "expires_at": checked_at}, "preflight_report": report.to_dict()})
    except ContractValidationError as exc:
        raise CoordinatorError("INVALID_PREFLIGHT_REPORT", str(exc)) from exc
    return report


class SimulatedWorker:
    """Worker that only reports structured facts through CoordinatorPort.submit."""

    source_kind = "SIMULATED"

    def __init__(self, port):
        self.port = port

    def report_preflight(self, run_id: str, plan_id: str, plan_sha256: str, checks: Mapping[str, bool], checked_at: str) -> Mapping[str, Any]:
        report = make_preflight_report(run_id, plan_id, plan_sha256, checks, checked_at)
        command = {
            "contract_version": "sentinel.v0", "wire_type": "command_envelope",
            "command_id": str(uuid.uuid4()), "command_type": "PREFLIGHT_INGEST",
            "run_id": run_id, "actor_type": "WORKER", "actor_id": "SIMULATED_WORKER",
            "correlation_id": report.report_id, "idempotency_key": "preflight-" + report.report_id,
            "issued_at": checked_at, "payload": {"preflight_report": report.to_dict()},
        }
        return self.port.submit(command)
