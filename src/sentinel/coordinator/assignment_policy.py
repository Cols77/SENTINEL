"""Patroller-choice facts. A near-tie between rank 1 and rank 2 is a judgment point;
a clear gap is settled by rule. Today the gateway only ever produces a single patroller
(blocker A3) and only accepts rank 1 (A2); see the consolidated WP0 proposal, item P-2."""
from __future__ import annotations

from typing import Sequence

ASSIGNMENT_CLEAR_GAP = 0.10
_GAP_BANDS = ((ASSIGNMENT_CLEAR_GAP, "clear"), (0.05, "close"), (0.01, "very_close"))


def rank_gap(scores: Sequence[float]) -> float | None:
    return None if len(scores) < 2 else round(scores[0] - scores[1], 6)


def gap_band(gap: float | None) -> str:
    if gap is None:
        return "single"
    for floor, name in _GAP_BANDS:
        if gap >= floor:
            return name
    return "tie"


def is_near_tie(scores: Sequence[float]) -> bool:
    gap = rank_gap(scores)
    return gap is not None and gap < ASSIGNMENT_CLEAR_GAP


def assignment_facts(scores: Sequence[float]) -> dict:
    return {"patrollers_ranked": len(scores), "rank_gap_band": gap_band(rank_gap(scores)),
            "near_tie": is_near_tie(scores)}
