"""Replanning support (P3a Task 9) — **deliberately NOT wired into `legal_set`**.

`covering_plans` below is a pure, tested helper that P3a-2 will need. Nothing
imports it into the decision path, and `REPLAN` is not a decision point in
this branch. Read this before re-attempting the wiring.

## Why the detection trigger was dropped

The plan specified: an out-of-plan detection scoring at or above
`EVIDENCE_TASK_SCORE`, for which some catalogue plan covers both the current
stations and the new one, becomes a `REPLAN` judgment point. Implemented
exactly as written, that rule fires far more often than intended, and three
independent reasons say not to ship it.

1. **It degenerates on the v1 catalogue.** `PLAN_MARKER_VERIFIED_6` contains
   all six real stations, so it is a superset of *every* station set. Hence
   `covering_plans(...)` is non-empty for every real out-of-plan station, and
   *every* qualifying out-of-plan detection routes to `REPLAN`. The rule reads
   as a narrow special case and behaves as the general case.
   `test_the_v1_catalogue_makes_the_covering_test_degenerate` pins this.

2. **It silently overrides an operator-approved decision.** The v1 decision
   fixture labels out-of-plan detections `escalate`, approved 2026-09-20 and
   recorded in `fixtures/decisions/README.md`. The turn generator encodes the
   same expectation for a third of its 24 `EVIDENCE_GATE` variants
   (`OUTSIDE_STATIONS`, `expected_point="EVIDENCE_GATE"`). Wiring `REPLAN`
   turns those turns into `REPLAN` and breaks the frozen golden fixture --
   which is on the Never-edit list, is P2-owned, and is now merged to master.
   Changing it would alter the demo path before the 2026-09-25 freeze.

3. **On the merits it is the wrong trigger anyway.** `INSPECTION_DISPATCH` is
   plan-gated: the gateway rejects a dispatch whose section is not in an
   approved plan. So a detection outside the approved plan is an anomaly in
   the evidence, not a workflow event -- and answering an unexpected artifact
   by automatically proposing a *larger* mission is silent scope growth. The
   approved behaviour, escalating to a human, is the safer one.

The trigger the operator actually asked for ("replan mid-run") is a new
operator message during the run. That needs `OPERATOR_MESSAGE` -- proposal
item **P-5** in `2026-09-20_wp0-proposal-p3-decision-surface-amendments.md`,
which is ungranted. So `REPLAN` is a blocked slice, like the closer look
(P-7), and belongs to P3a-2 behind that consent. Per Global Constraints,
whatever the gateway cannot do today is not built: the current behaviour is
pinned by a characterization test instead
(`test_characterization_out_of_plan_detection_escalates_by_rule`).

## Note for P3a-2

Reason 1 does not disappear with the rich catalogue (proposal item P-3) -- it
changes shape. `PLAN_VERIFIED_FULL_SITE` is still a superset of everything,
so a covering-set test alone will still match. A usable rule needs a tighter
criterion than "some plan covers it": the *smallest* covering plan, a bound on
how many stations may be added, or an explicit operator instruction. Prefer
the last -- it is the only one that reflects an actual change of intent.
"""
from __future__ import annotations

from typing import Any, Sequence


def covering_plans(catalogue: Sequence[Any], station_id: str, current_station_ids: Sequence[str],
                    already_proposed: Sequence[str]) -> tuple[Any, ...]:
    """Catalogue entries whose ``station_ids`` cover ``current_station_ids``
    plus ``station_id``, excluding anything already proposed in this run
    (blocker A9: re-proposing raises a raw ``sqlite3.IntegrityError`` through
    the gateway). Ordered fewest stations first, then by plan id, so the
    result is deterministic regardless of catalogue order.

    Not called from the decision path -- see the module docstring."""
    needed = set(current_station_ids) | {station_id}
    matches = [plan for plan in catalogue
               if plan.plan_id not in already_proposed and needed <= set(plan.station_ids)]
    return tuple(sorted(matches, key=lambda plan: (len(plan.station_ids), plan.plan_id)))


__all__ = ["covering_plans"]
