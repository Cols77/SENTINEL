"""Read-only projection of a pending architecture-E plan proposal."""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

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


__all__ = ["pending_proposal_review"]
