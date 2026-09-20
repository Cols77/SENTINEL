"""Read-only projection of a pending architecture-E plan proposal."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
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
        """SELECT state_version, status, pending_approval_kind, pending_approval_id
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
        "review_identity": {
            "plan_id": pending_plan_id,
            "expected_state_version": int(run["state_version"]),
        },
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


def read_audit_jsonl(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read valid object records while preserving malformed lines in place."""
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    audit_path = Path(path)
    if not audit_path.exists():
        return records, warnings
    for line_number, line in enumerate(
        audit_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(
                f"audit line {line_number} is invalid JSON: {exc.msg}"
            )
            continue
        if not isinstance(record, dict):
            warnings.append(f"audit line {line_number} must be a JSON object")
            continue
        records.append(record)
    return records, warnings


def _proposal_for_plan(
    repository: Any, run_id: str, plan_id: str
) -> tuple[dict, str] | None:
    rows = repository.connection.execute(
        """SELECT payload_json, recorded_at
           FROM events
           WHERE run_id = ? AND event_type = 'PLAN_PROPOSED'
           ORDER BY sequence_no DESC""",
        (run_id,),
    )
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("plan_id") == plan_id:
            return payload, row["recorded_at"]
    return None


def _plan_reference(
    repository: Any, run_id: str, plan_id: str
) -> dict[str, Any] | None:
    plan = repository.connection.execute(
        """SELECT plan_id, plan_sha256, catalogue_id, catalogue_revision,
                  station_ids_json
           FROM plans WHERE run_id = ? AND plan_id = ?""",
        (run_id, plan_id),
    ).fetchone()
    if plan is None:
        return None
    return {
        "plan_id": plan["plan_id"],
        "catalogue_id": plan["catalogue_id"],
        "catalogue_revision": plan["catalogue_revision"],
        "plan_sha256": plan["plan_sha256"],
        "station_ids": json.loads(plan["station_ids_json"]),
    }


def _review_key(
    run_id: str, plan_id: str, state_version: int, decision: str, actor_id: str
) -> str:
    return f"plan-review-{run_id}-{plan_id}-{state_version}-{decision}-{actor_id}"


def _review_record(
    run_id: str,
    state_version: int,
    decision: str,
    actor_id: str,
    plan_id: str,
    proposed_label: str,
    proposed_at_text: str,
    reviewed_at: datetime,
) -> dict[str, Any]:
    proposed_at = datetime.fromisoformat(proposed_at_text.replace("Z", "+00:00"))
    return {
        "record": "plan_review",
        "run_id": run_id,
        "state_version": state_version,
        "decision": decision,
        "actor_id": actor_id,
        "plan_id": plan_id,
        "proposed_label": proposed_label,
        "seconds_to_decision": max(
            0.0, (_as_utc(reviewed_at) - _as_utc(proposed_at)).total_seconds()
        ),
    }


_REVIEW_ID_FIELDS = (
    "record", "run_id", "state_version", "decision", "actor_id", "plan_id",
    "proposed_label",
)


def _same_review(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(field) == right.get(field) for field in _REVIEW_ID_FIELDS)


def _write_review_once(sink: Any, record: dict[str, Any]) -> None:
    """Best-effort deduplication for the repository's readable audit sinks.

    SQLite mutation and an external audit sink cannot be one transaction.
    Recovery therefore identifies a committed review from coordinator data,
    then avoids a duplicate when the sink exposes its in-memory records or
    JSONL path. Custom sinks may provide ``contains(record)`` for the same
    behavior; opaque write-only sinks remain at-least-once.
    """
    contains = getattr(sink, "contains", None)
    if callable(contains) and contains(record):
        return
    records = getattr(sink, "records", None)
    if isinstance(records, list) and any(
        isinstance(existing, dict) and _same_review(existing, record)
        for existing in records
    ):
        return
    path = getattr(sink, "path", None)
    if path is not None:
        existing_records, _warnings = read_audit_jsonl(path)
        if any(_same_review(existing, record) for existing in existing_records):
            return
        audit_path = Path(path)
        if audit_path.exists() and audit_path.stat().st_size:
            with audit_path.open("rb") as handle:
                handle.seek(-1, 2)
                terminated = handle.read(1) == b"\n"
            if not terminated:
                # Preserve the malformed/truncated bytes and delimit the next
                # append so the recovered record is independently valid JSONL.
                with audit_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n")
    sink.write(record)


def _recover_review(
    repository: Any,
    run_id: str,
    plan_id: str,
    expected_state_version: int,
    decision: str,
    actor_id: str,
    sink: Any,
) -> dict[str, Any] | None:
    approval = repository.connection.execute(
        """SELECT plan_id, plan_sha256, action, expected_state_version, created_at
           FROM plan_approvals
           WHERE run_id = ? AND plan_id = ? AND actor_id = ?
             AND expected_state_version = ?
           ORDER BY rowid DESC LIMIT 1""",
        (run_id, plan_id, actor_id, expected_state_version),
    ).fetchone()
    if approval is None:
        return None
    if approval["action"] != decision:
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW",
            f"plan was already reviewed as {approval['action']}",
        )

    state_version = expected_state_version
    reference = _plan_reference(repository, run_id, plan_id)
    proposal = _proposal_for_plan(repository, run_id, plan_id)
    if (
        reference is None
        or reference["plan_sha256"] != approval["plan_sha256"]
        or proposal is None
    ):
        return None
    payload = {
        "decision": decision,
        "target": {"kind": "PLAN", "plan_reference": reference},
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    key = _review_key(run_id, plan_id, state_version, decision, actor_id)
    event = repository.connection.execute(
        """SELECT event_id, payload_json FROM events
           WHERE run_id = ? AND state_version = ? AND event_type = ?
             AND actor_type = 'OPERATOR' AND actor_id = ?""",
        (
            run_id,
            state_version + 1,
            "PLAN_APPROVED" if decision == "CONFIRM" else "PLAN_REJECTED",
            actor_id,
        ),
    ).fetchone()
    prior = repository.connection.execute(
        """SELECT result_json FROM idempotency_keys
           WHERE run_id = ? AND actor_type = 'OPERATOR' AND actor_id = ?
             AND idempotency_key = ? AND command_type = 'OPERATOR_DECIDE'
             AND expected_state_version = ? AND payload_sha256 = ?""",
        (
            run_id,
            actor_id,
            key,
            state_version,
            hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        ),
    ).fetchone()
    if event is None or json.loads(event["payload_json"]) != payload or prior is None:
        return None
    result = json.loads(prior["result_json"])
    if result.get("event_id") != event["event_id"]:
        return None

    proposal_payload, proposed_at_text = proposal
    proposed_label = proposal_payload.get("catalogue_plan_id")
    if not isinstance(proposed_label, str) or not proposed_label:
        return None
    reviewed_at = datetime.fromisoformat(approval["created_at"].replace("Z", "+00:00"))
    _write_review_once(
        sink,
        _review_record(
            run_id, state_version, decision, actor_id, plan_id, proposed_label,
            proposed_at_text, reviewed_at,
        ),
    )
    return result


def submit_plan_review(
    repository: Any,
    gateway: Any,
    run_id: str,
    decision: str,
    *,
    plan_id: str,
    expected_state_version: int,
    sink: Any,
    clock: Callable[[], datetime] = _utc_now,
    actor_id: str = "OPERATOR_1",
) -> dict[str, Any]:
    """Confirm or reject the exact persisted plan currently awaiting review.

    The gateway remains the sole mutation boundary. This function reconstructs
    the immutable persisted plan reference, submits one valid
    ``OPERATOR_DECIDE`` envelope, and records review telemetry after success.
    If the gateway committed but its response or the later audit write was
    lost, a retry verifies the matching approval, event, and idempotency result
    before returning that result and repairing the audit. The two stores are
    deliberately not presented as one atomic transaction.
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
    exact_pending = (
        run["status"] == "WAITING_FOR_APPROVAL"
        and run["pending_approval_kind"] == "PLAN"
        and run["pending_approval_id"] == plan_id
        and int(run["state_version"]) == expected_state_version
    )
    if not exact_pending:
        recovered = _recover_review(
            repository, run_id, plan_id, expected_state_version,
            decision, actor_id, sink,
        )
        if recovered is not None:
            return recovered
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW",
            "review identity is not the current or an exactly resolved plan review",
        )

    plan_reference = _plan_reference(repository, run_id, plan_id)
    proposal = _proposal_for_plan(repository, run_id, plan_id)
    if plan_reference is None or proposal is None:
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW", "pending plan or proposal event is missing"
        )

    proposal_payload, proposed_at_text = proposal
    proposed_label = proposal_payload.get("catalogue_plan_id")
    if not isinstance(proposed_label, str) or not proposed_label:
        raise CoordinatorError(
            "INVALID_PLAN_REVIEW", "pending proposal has no catalogue plan id"
        )

    state_version = expected_state_version
    reviewed_at = _as_utc(clock())
    key = _review_key(run_id, plan_id, state_version, decision, actor_id)
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
    _write_review_once(
        sink,
        _review_record(
            run_id, state_version, decision, actor_id, plan_id, proposed_label,
            proposed_at_text, reviewed_at,
        ),
    )
    return result


__all__ = ["pending_proposal_review", "read_audit_jsonl", "submit_plan_review"]
