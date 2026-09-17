"""Fail-closed plan proposal, operator approval, and dispatch binding."""

from __future__ import annotations

import uuid
import json
from typing import Any, Mapping

from .errors import CoordinatorError
from .events import utc_now


class PlanApprovalService:
    """Persist plan lifecycle facts and require an exact immutable approval."""

    def __init__(self, repository):
        self.repository = repository

    def propose(self, run_id: str, plan: Mapping[str, Any]) -> dict[str, Any]:
        required = ("plan_id", "plan_sha256", "station_ids")
        if any(key not in plan for key in required):
            raise CoordinatorError("INVALID_CONTRACT", "plan proposal is incomplete")
        existing = self.repository.connection.execute(
            "SELECT run_id, plan_sha256, canonical_json FROM plans WHERE plan_id = ?",
            (plan["plan_id"],),
        ).fetchone()
        if existing is not None:
            if (existing["run_id"], existing["plan_sha256"]) != (run_id, plan["plan_sha256"]):
                raise CoordinatorError("PLAN_HASH_MISMATCH", "plan ID is already bound to another immutable plan")
            return dict(plan)
        canonical = dict(plan)
        canonical["station_ids"] = list(canonical["station_ids"])
        with self.repository.connection:
            self.repository.connection.execute(
                """INSERT INTO plans(plan_id, run_id, catalogue_id, catalogue_revision,
                   plan_sha256, mode, station_ids_json, canonical_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan["plan_id"], run_id, plan.get("catalogue_id", "SURVEY_CATALOGUE_V1"),
                 plan.get("catalogue_revision", "REV_1"), plan["plan_sha256"],
                 plan.get("mode", "MARKER_VERIFIED"), json.dumps(list(plan["station_ids"])),
                 json.dumps(canonical, sort_keys=True, separators=(",", ":")), utc_now()),
            )
        self.repository.set_pending_approval(run_id, "PLAN")
        self.repository.append_event(run_id, "PLAN_CREATED", dict(plan))
        return dict(plan)

    def approve(
        self,
        run_id: str,
        plan_id: str,
        plan_sha256: str,
        *,
        actor_id: str,
        expected_state_version: int,
    ) -> dict[str, Any]:
        if not actor_id:
            raise CoordinatorError("INVALID_CONTRACT", "operator identity is required")
        row = self.repository.connection.execute(
            "SELECT state_version FROM runs WHERE run_id = ? AND status = 'WAITING_FOR_APPROVAL'",
            (run_id,),
        ).fetchone()
        if row is None or row["state_version"] != expected_state_version:
            raise CoordinatorError("STALE_STATE_VERSION", "approval is not current")
        plan = self.repository.connection.execute(
            "SELECT plan_sha256 FROM plans WHERE run_id = ? AND plan_id = ?",
            (run_id, plan_id),
        ).fetchone()
        if plan is None or plan["plan_sha256"] != plan_sha256:
            raise CoordinatorError("PLAN_HASH_MISMATCH", "approval does not bind the persisted plan")
        approval_id = str(uuid.uuid4())
        with self.repository.connection:
            self.repository.connection.execute(
                """INSERT INTO plan_approvals
                   (approval_id, run_id, plan_id, plan_sha256, actor_id, action,
                    expected_state_version, created_at)
                   VALUES (?, ?, ?, ?, ?, 'CONFIRM', ?, ?)""",
                (approval_id, run_id, plan_id, plan_sha256, actor_id, expected_state_version, utc_now()),
            )
        event = self.repository.append_event(
            run_id,
            "PLAN_APPROVED",
            {"approval_id": approval_id, "plan_id": plan_id, "plan_sha256": plan_sha256},
            actor_type="OPERATOR",
            actor_id=actor_id,
        )
        return {"approval_id": approval_id, "event": event, "plan_id": plan_id, "plan_sha256": plan_sha256}

    def dispatch_is_allowed(self, run_id: str, plan_id: str, plan_sha256: str) -> bool:
        row = self.repository.connection.execute(
            """SELECT 1 FROM plan_approvals
               WHERE run_id = ? AND plan_id = ? AND plan_sha256 = ? AND action = 'CONFIRM'
               AND EXISTS (SELECT 1 FROM plans WHERE plan_id = plan_approvals.plan_id AND plan_sha256 = plan_approvals.plan_sha256)""",
            (run_id, plan_id, plan_sha256),
        ).fetchone()
        return row is not None

    def require_dispatch_approval(self, run_id: str, plan_id: str, plan_sha256: str) -> None:
        if not self.dispatch_is_allowed(run_id, plan_id, plan_sha256):
            raise CoordinatorError("INVALID_STATE_TRANSITION", "dispatch requires exact operator-approved plan")
