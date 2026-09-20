"""Read-only projection of a pending architecture-E plan proposal."""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any, Callable

from .errors import CoordinatorError
from .legal_tools import CataloguePlan, SELECT_PLAN


def pending_proposal_review(
    repository: Any,
    run_id: str,
    catalogue: Sequence[CataloguePlan],
    audit_records: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Describe the persisted plan awaiting approval without changing state."""
    run = repository.connection.execute(
        """SELECT status, pending_approval_kind, pending_approval_id
           FROM runs WHERE run_id = ?""",
        (run_id,),
    ).fetchone()
    if (
        run is None
        or run["status"] != "WAITING_FOR_APPROVAL"
        or run["pending_approval_kind"] != "PLAN"
        or run["pending_approval_id"] is None
    ):
        return None

    pending_plan_id = run["pending_approval_id"]
    plan = repository.connection.execute(
        """SELECT station_ids_json FROM plans
           WHERE run_id = ? AND plan_id = ?""",
        (run_id, pending_plan_id),
    ).fetchone()
    if plan is None:
        return None

    proposal = None
    for row in repository.connection.execute(
        """SELECT state_version, payload_json FROM events
           WHERE run_id = ? AND event_type = 'PLAN_PROPOSED'
           ORDER BY sequence_no DESC""",
        (run_id,),
    ):
        payload = json.loads(row["payload_json"])
        if payload.get("plan_id") == pending_plan_id:
            proposal = (row["state_version"], payload.get("catalogue_plan_id"))
            break
    if proposal is None:
        return None

    proposal_state_version, proposed_label = proposal
    by_label = {candidate.plan_id: candidate for candidate in catalogue}
    proposed = by_label.get(proposed_label)
    if proposed is None or tuple(json.loads(plan["station_ids_json"])) != proposed.station_ids:
        return None

    recommendation = None
    expected_version = proposal_state_version - 1
    for record in audit_records:
        if (
            record.get("record") == "proposal_recommendation"
            and record.get("run_id") == run_id
            and record.get("state_version") == expected_version
            and record.get("decision_point") == SELECT_PLAN
            and record.get("label") == proposed_label
            and isinstance(record.get("reason"), str)
            and record["reason"].strip()
            and isinstance(record.get("facts"), dict)
        ):
            recommendation = {
                "reason": record["reason"],
                "facts": dict(record["facts"]),
            }

    return {
        "run_id": run_id,
        "proposed_plan": {
            "label": proposed.plan_id,
            "description": proposed.description,
            "recommendation": recommendation,
        },
        "other_plans": [
            {"label": candidate.plan_id, "description": candidate.description}
            for candidate in catalogue
            if candidate.plan_id != proposed.plan_id
        ],
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def submit_plan_review(
    repository: Any,
    gateway: Any,
    run_id: str,
    decision: str,
    *,
    sink: Any,
    clock: Callable[[], datetime] = _utc_now,
    actor_id: str = "OPERATOR_1",
) -> dict[str, Any]:
    """Confirm or reject the exact persisted plan currently awaiting review.

    The gateway remains the sole mutation boundary.  This function only
    reconstructs the immutable persisted plan reference, submits one valid
    ``OPERATOR_DECIDE`` envelope, and records review telemetry after success.
    """
    if decision not in {"CONFIRM", "REJECT"}:
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW", "decision must be CONFIRM or REJECT"
        )

    run = repository.connection.execute(
        """SELECT state_version, status, pending_approval_kind,
                  pending_approval_id
           FROM runs WHERE run_id = ?""",
        (run_id,),
    ).fetchone()
    if run is None:
        raise CoordinatorError("INVALID_PLAN_REVIEW", "run does not exist")
    if (
        run["status"] != "WAITING_FOR_APPROVAL"
        or run["pending_approval_kind"] != "PLAN"
        or run["pending_approval_id"] is None
    ):
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW", "run has no plan awaiting approval"
        )

    plan_id = run["pending_approval_id"]
    plan = repository.connection.execute(
        """SELECT plan_id, plan_sha256, catalogue_id, catalogue_revision,
                  station_ids_json
           FROM plans WHERE run_id = ? AND plan_id = ?""",
        (run_id, plan_id),
    ).fetchone()
    proposal_rows = repository.connection.execute(
        """SELECT payload_json, recorded_at
           FROM events
           WHERE run_id = ? AND event_type = 'PLAN_PROPOSED'
           ORDER BY sequence_no DESC""",
        (run_id,),
    ).fetchall()
    proposal = None
    for row in proposal_rows:
        payload = json.loads(row["payload_json"])
        if payload.get("plan_id") == plan_id:
            proposal = (payload, row["recorded_at"])
            break
    if plan is None or proposal is None:
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW", "pending plan or proposal event is missing"
        )

    proposal_payload, proposed_at_text = proposal
    proposed_label = proposal_payload.get("catalogue_plan_id")
    if not isinstance(proposed_label, str) or not proposed_label:
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW", "pending proposal has no catalogue plan id"
        )

    state_version = int(run["state_version"])
    reviewed_at = _as_utc(clock())
    key = f"plan-review-{run_id}-{state_version}-{decision}-{actor_id}"
    plan_reference = {
        "plan_id": plan["plan_id"],
        "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }
    command = {
        "contract_version": "sentinel.v0",
        "wire_type": "command_envelope",
        "command_id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
        "command_type": "OPERATOR_DECIDE",
        "run_id": run_id,
        "actor_type": "OPERATOR",
        "actor_id": actor_id,
        "correlation_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "correlation:" + key)),
        "idempotency_key": key,
        "expected_state_version": state_version,
        "issued_at": _wire_timestamp(reviewed_at),
        "payload": {
            "decision": decision,
            "target": {"kind": "PLAN", "plan_reference": plan_reference},
        },
    }

    result = gateway.submit(command)
    proposed_at = datetime.fromisoformat(proposed_at_text.replace("Z", "+00:00"))
    sink.write({
        "record": "plan_review",
        "run_id": run_id,
        "state_version": state_version,
        "decision": decision,
        "actor_id": actor_id,
        "proposed_label": proposed_label,
        "seconds_to_decision": max(
            0.0, (reviewed_at - _as_utc(proposed_at)).total_seconds()
        ),
    })
    return result


__all__ = ["pending_proposal_review", "submit_plan_review"]
