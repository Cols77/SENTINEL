"""Operator-load measurement against the 'approvals only' target (P3a)."""
from __future__ import annotations

from typing import Iterable

_APPROVAL_EVENTS = frozenset({"PLAN_APPROVED", "PLAN_REJECTED",
                              "PATROLLER_ASSIGNMENT_CONFIRMED", "OPERATOR_ASSIGNMENT_REJECTED"})


def operator_touches(audit_records: Iterable[dict], events: Iterable[dict]) -> dict:
    """approvals: approval-flow decisions. choices: judgment-point picks other than 'escalate'.
    escalations: judgment turns the executor handed to the operator. total = approvals + choices.
    approvals_only: no escalation and no choice. broken_by: decision points that escalated, in order, deduplicated."""
    approvals = sum(1 for e in events if e.get("event_type") in _APPROVAL_EVENTS)
    choices, escalations, broken_by = 0, 0, []
    for record in audit_records:
        if record.get("record") == "operator_choice" and record.get("label") != "escalate":
            choices += 1
        if record.get("record") == "judgment" and (record.get("executor") or {}).get("chooser") == "OPERATOR":
            escalations += 1
            if record["decision_point"] not in broken_by:
                broken_by.append(record["decision_point"])
    return {"approvals": approvals, "choices": choices, "escalations": escalations,
            "total": approvals + choices, "approvals_only": escalations == 0 and choices == 0,
            "broken_by": broken_by}
