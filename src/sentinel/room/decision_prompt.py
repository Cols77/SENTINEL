"""Mission context for Jev's fixed-choice room action decision."""

from .coverage import CoverageProjection, CoverageStatus
from .models import CandidateKind, RoomCandidate
from .queries import ReadSnapshot


_RESOLVED = {CoverageStatus.OBSERVED.value, CoverageStatus.NOT_RESOLVABLE.value}
_MAX_REQUEST_CHARS = 400
_MAX_OBJECTIVES = 8
_MAX_OBJECTIVE_CHARS = 140
_MAX_UNRESOLVED = 12
_MAX_PAIR_CHARS = 70
_MAX_OBSERVE_IDS = 8
_MAX_ID_CHARS = 70
_MAX_ATTEMPTS = 6
_MAX_DETAIL_CHARS = 160


def _clip(value: object, limit: int) -> str:
    rendered = str(value)
    return rendered if len(rendered) <= limit else rendered[:limit - 1] + "…"


def _sample(values: tuple[str, ...], *, limit: int, width: int, name: str) -> str:
    shown = ", ".join(_clip(value, width) for value in values[:limit])
    omitted = len(values) - limit
    return shown + (f" (+{omitted} more {name})" if omitted > 0 else "")


def build_decision_instruction(snapshot: ReadSnapshot, coverage: CoverageProjection,
                               localization: dict | None,
                               candidates: tuple[RoomCandidate, ...]) -> str:
    """Describe progress and priorities without granting free-form action planning."""
    objectives = () if snapshot.ledger is None else snapshot.ledger.objectives
    request_text = snapshot.ledger.request_text if snapshot.ledger is not None else snapshot.request_text
    objective_lines = []
    for objective in sorted(objectives, key=lambda item: item.ordinal):
        classes = ", ".join(objective.target_classes) or "none"
        detail = (f"{objective.objective_id}: {objective.kind.value}; classes: {classes}; "
                  f"scope: {objective.spatial_scope}; terminal: {objective.terminal_condition}")
        if objective.qualifier:
            detail += f"; qualifier: {objective.qualifier}"
        if objective.relation:
            detail += f"; relation: {objective.relation}"
        objective_lines.append(detail)

    statuses_by_cell: dict[str, list[str]] = {}
    for cell_id, _class_name, status in coverage.by_cell_and_class:
        statuses_by_cell.setdefault(cell_id, []).append(status)
    explored_cells = sum(CoverageStatus.OBSERVED.value in statuses
                         for statuses in statuses_by_cell.values())
    resolved_cells = sum(all(status in _RESOLVED for status in statuses)
                         for statuses in statuses_by_cell.values())
    resolved_pairs = sum(status in _RESOLVED for _, _, status in coverage.by_cell_and_class)
    status_by_pair = {f"{cell_id}:{class_name}": status
                      for cell_id, class_name, status in coverage.by_cell_and_class}
    unresolved = _sample(tuple(f"{pair} ({status_by_pair[pair]})" for pair in sorted(coverage.uncovered)),
                         limit=_MAX_UNRESOLVED, width=_MAX_PAIR_CHARS, name="unresolved pairs")
    observation_ids = _sample(tuple(sorted(candidate.candidate_id for candidate in candidates
                                           if candidate.kind == CandidateKind.OBSERVE)),
                              limit=_MAX_OBSERVE_IDS, width=_MAX_ID_CHARS, name="OBSERVE IDs")
    if localization is None:
        localization_line = "Localization: unavailable."
    else:
        localization_line = (
            f"Localization: {localization.get('state', 'unknown')}; "
            f"last tag fix age: {localization.get('last_tag_fix_age_s')} s; "
            f"position uncertainty: {localization.get('position_uncertainty_cm')} cm; "
            f"margin before mandatory recovery: "
            f"{localization.get('margin_before_mandatory_recovery_pct')}%.")

    attempts = [f"{_clip(o.candidate_id, _MAX_ID_CHARS)}: "
                + ("succeeded" if o.refusal_code is None else
                   f"failed ({o.code}" + (f": {_clip(o.detail, _MAX_DETAIL_CHARS)}" if o.detail else "") + ")")
                for o in getattr(snapshot, "outcomes", ()) if o.kind == CandidateKind.OBSERVE][-_MAX_ATTEMPTS:]
    events = [_clip(e, _MAX_DETAIL_CHARS) for e in (localization or {}).get("recent_positioning_events") or ()]

    return "\n".join((
        "Choose exactly one legal candidate ID from the supplied choices; never coordinates.",
        f"Operator request: {_clip(request_text, _MAX_REQUEST_CHARS)}",
        "Current objectives: " + (_sample(tuple(objective_lines), limit=_MAX_OBJECTIVES,
                                          width=_MAX_OBJECTIVE_CHARS, name="objectives") or "none"),
        (f"Explored cells: {explored_cells} of {len(statuses_by_cell)}. "
         f"Resolved cells: {resolved_cells} of {len(statuses_by_cell)}. "
         f"Resolved cell/class pairs: {resolved_pairs} of {len(coverage.by_cell_and_class)}.")
        if statuses_by_cell else "Coverage cells: none declared.",
        "Unexplored or unresolved cell/class pairs: " + (unresolved or "none"),
        localization_line,
        "Recent positioning events: " + ("; ".join(events) or "none"),
        "Recent observation attempts (oldest first): " + ("; ".join(attempts) or "none"),
        "Admissible OBSERVE candidate IDs: " + (observation_ids or "none"),
        "Prefer observing unexplored zones for counts: choose an admissible OBSERVE that adds coverage before "
        "ANSWER or ESCALATE. An OBSERVE that gives an uncertain object its second view is progress too, even "
        "when count coverage is complete: a count confirms an object only from two distinct poses. An unvisited "
        "declared search viewpoint is progress even when count coverage is complete. Answer only when no "
        "admissible observation adds coverage or a second view for a count, or no unvisited search viewpoint "
        "can advance a search; use an explicit incomplete answer when that is the legal route. "
        "Re-anchor when the position is stale. When an observation failed because positioning was lost or "
        "the drone arrived off the pose, do not simply repeat it: prefer a different admissible pose (ideally "
        "one closer to the tags or home), or re-anchor first. If positioning keeps failing and the remaining "
        "views are unreachable, answer explicitly incomplete with what was observed. A detector failure is not "
        "a positioning problem; that pose may be retried. Escalate only when "
        "nothing else can make progress (no admissible observation, read, re-anchor, or answer).",
        "Reads are historical. Never compose factual values.",
    ))
