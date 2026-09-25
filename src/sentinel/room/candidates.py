"""Plan 01 admits only persisted reads and deterministic conversational actions.

Plan 03 Task 2 adds ``build_observation_candidates``: OBSERVE candidates for
a room-wide or region count. This is a separate entry point, not a change to
``build_candidates`` above, which keeps offering only the Plan 01 safe kinds
(see ``tests/room/test_candidates.py``); ``RoomController`` adds the
observation candidates only while its observation capability is enabled.

Plan 03 residual task R (F2) adds the count answer routes that go with
them: ``count_answer_candidate`` (``ANSWER:COUNTS``, answered from this
session's own grounded observations) and an explicit incomplete answer for a
requested region that can no longer be observed.
"""
import hashlib
import json
import math

from .coverage import CoverageProjection
from .goals import GoalObjective
from .grounding import uncertain_objects
from .models import CandidateKind, GoalKind, GoalStatus, PhysicalCost, RoomCandidate
from .queries import READ_KINDS, ReadSnapshot, ToolOutcome, relevant_hash
from .semantic import SemanticPose, resolve_pose_id
from .site import RoomSite

_DESCRIPTIONS = {
    CandidateKind.READ_DRONE: "Read the last persisted drone telemetry with its timestamp.",
    CandidateKind.READ_OBSERVATIONS: "Read historical persisted observation evidence.",
    CandidateKind.READ_COUNTS: "Read a saved legacy survey report, retaining count uncertainty.",
    CandidateKind.ANSWER: "Render the available facts using deterministic cited templates.",
    CandidateKind.CLARIFY: "Ask for evidence or clarification; this session is read-only.",
    CandidateKind.ESCALATE: "Escalate this request to the operator.",
}


def can_answer(snapshot: ReadSnapshot) -> bool:
    if not snapshot.facts or snapshot.ledger is None or snapshot.ledger.status != GoalStatus.PENDING:
        return False
    if not snapshot.ledger.objectives:
        return False
    return can_answer_objectives(snapshot, snapshot.ledger.objectives)


def can_answer_objectives(snapshot: ReadSnapshot, objectives: tuple[GoalObjective, ...]) -> bool:
    """Whether persisted read facts answer every one of ``objectives``."""
    if objectives and not snapshot.facts:
        return False
    for objective in objectives:
        if objective.kind in (GoalKind.FOLLOW, GoalKind.LAND_NEAR) or objective.relation or objective.qualifier:
            return False
        if objective.terminal_condition not in ("REPORTED", "OBSERVED"):
            return False
        expected = {GoalKind.ANSWER_STATE: CandidateKind.READ_DRONE, GoalKind.COUNT: CandidateKind.READ_COUNTS,
                    GoalKind.FIND_OBSERVE: CandidateKind.READ_OBSERVATIONS}[objective.kind]
        # Plan 01 has historical session/run evidence, with no provenance that
        # binds it to a current camera view or a requested subregion. Descriptive
        # scope text cannot upgrade those facts into current-view observations.
        scope_kinds = {
            ("ROOM", CandidateKind.READ_DRONE): "AIRCRAFT_STATE",
            ("ROOM", CandidateKind.READ_COUNTS): "LEGACY_SURVEY_RUN",
            ("ROOM", CandidateKind.READ_OBSERVATIONS): "HISTORICAL_OBSERVATION",
        }
        scope_kind = scope_kinds.get((objective.spatial_scope, expected))
        facts = [f for f in snapshot.facts if f.kind == expected and f.scope_kind == scope_kind]
        if not facts:
            return False
        classes = {dict(f.values).get("class", dict(f.values).get("class_name")) for f in facts}
        if objective.target_classes and not set(objective.target_classes) <= classes:
            return False
    return True


#: A count is answered in flight either way; the capability returns home
#: after the answer, so an explicit "and return home" count is answerable too.
_COUNT_TERMINALS = ("REPORTED", "OBSERVED", "RETURN_HOME")


def build_candidates(snapshot: ReadSnapshot, *, observation_legal: bool = False) -> tuple[RoomCandidate, ...]:
    if snapshot.terminal_state:
        return ()
    kinds = (*READ_KINDS, CandidateKind.ANSWER, CandidateKind.CLARIFY, CandidateKind.ESCALATE)
    candidates = []
    physical_mission_reads = (CandidateKind.READ_OBSERVATIONS, CandidateKind.READ_COUNTS)
    for kind in kinds:
        if kind == CandidateKind.ANSWER and not can_answer(snapshot):
            continue
        digest = relevant_hash(kind, snapshot)
        prior_reads = tuple(o for o in snapshot.outcomes if o.candidate_id == kind.value
                            and kind in physical_mission_reads and o.code in ("READ_SUCCEEDED", "NO_EVIDENCE"))
        if observation_legal and kind in physical_mission_reads:
            seen = {fact.fact_id for outcome in prior_reads for fact in outcome.facts}
            available = {fact.fact_id for fact in snapshot.sources if fact.kind == kind}
            if not (prior_reads and available - seen):
                continue
        if any(o.candidate_id == kind.value and o.relevant_state_sha256 == digest
               and (o.refusal_code is not None or (kind in READ_KINDS and o.code in ("READ_SUCCEEDED", "NO_EVIDENCE")))
               for o in snapshot.outcomes):
            continue
        candidates.append(RoomCandidate(kind.value, kind, snapshot.state_version, digest,
                                        _DESCRIPTIONS[kind], _DESCRIPTIONS[kind],
                                        tuple(f.fact_id for f in snapshot.facts) if kind == CandidateKind.ANSWER else ()))
    return tuple(candidates)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _region_pose_id(scope: str) -> str | None:
    """The pose_id a user-requested "h/d/z" region resolves to, or None for
    a scope this function does not generate a region view for (ROOM,
    CURRENT_VIEW, or anything malformed). Deliberately reproduces
    ``semantic._pose_id``'s format without importing that private name;
    ``tests/room/test_coverage.py`` cross-checks it against the real
    resolver."""
    parts = scope.split("/")
    if len(parts) != 3:
        return None
    horizontal, depth, height = parts
    try:
        SemanticPose(horizontal, depth, height)
    except ValueError:
        return None
    return f"{horizontal}-{depth}-{height}-toward_targets"


def count_scope(objective: GoalObjective) -> tuple[str | None, str | None]:
    """How a COUNT objective's answer is scoped: ``("ROOM", None)`` for a
    room-wide count, ``("VIEWPOINT", pose_id)`` for a requested semantic
    region (answered from that region's declared pose), ``(None, None)`` for
    any scope this session has no count route for."""
    if objective.spatial_scope == "ROOM":
        return "ROOM", None
    pose_id = _region_pose_id(objective.spatial_scope)
    return ("VIEWPOINT", pose_id) if pose_id is not None else (None, None)


def _observed(outcomes: tuple[ToolOutcome, ...], candidate_id: str) -> bool:
    return any(o.candidate_id == candidate_id and o.code == "OBSERVE_SUCCEEDED" for o in outcomes)


def _room_classes_have_coverage(objective: GoalObjective, coverage: CoverageProjection) -> bool:
    """Fix round 4 (Q3): whether every static class this objective targets
    actually has a declared coverage row somewhere in the room. A site whose
    cells only ever require "computer" declares no coverage at all for
    "phone" -- ``coverage.complete`` is vacuously true for a class no cell
    mentions, so without this a requested class with zero declared coverage
    would complete with a confident zero that nothing ever established."""
    classes = {c for c in objective.target_classes if c != "person"}
    covered = {cell_class for _, cell_class, _ in coverage.by_cell_and_class}
    return classes <= covered


def _unplaced_classes(outcomes: tuple[ToolOutcome, ...], *, pose_id: str | None = None) -> frozenset[str]:
    """Fix round 4 (Q4): the static classes with a detection this session
    saw but could not place in room coordinates, within scope -- every
    ``OBSERVE_SUCCEEDED`` frame for a room-wide count, or only the frames
    captured at ``pose_id`` for a region count. An unplaced sighting is still
    evidence an object may exist; a count that silently ignores it must
    never be offered as complete."""
    classes = set()
    for outcome in outcomes:
        if outcome.code != "OBSERVE_SUCCEEDED" or outcome.count_evidence is None:
            continue
        if pose_id is not None and outcome.count_evidence.pose_id != pose_id:
            continue
        classes.update(item.class_name for item in outcome.count_evidence.unplaced)
    return frozenset(classes)


def count_objective_ready(objective: GoalObjective, coverage: CoverageProjection,
                          outcomes: tuple[ToolOutcome, ...]) -> bool:
    """Whether a COUNT objective can be answered as complete: a room-wide
    count once declared coverage is complete *and* every target class this
    objective actually names has some declared coverage (fix round 4, Q3), a
    region count once its requested region was actually observed
    (``OBSERVE_SUCCEEDED``, which the controller records only after
    verifying pose, capture and authorized perception). Either scope is
    withheld while any of this objective's target classes still has a
    detection this session could not place (fix round 4, Q4) -- that
    sighting is evidence the count is not actually done, never silently
    reported as a confident zero."""
    scope, pose_id = count_scope(objective)
    classes = {c for c in objective.target_classes if c != "person"}
    if scope == "ROOM":
        unplaced = _unplaced_classes(outcomes)
        if unplaced if not classes else (unplaced & classes):
            return False
        return coverage.complete and _room_classes_have_coverage(objective, coverage)
    if scope == "VIEWPOINT":
        if not _observed(outcomes, f"OBSERVE:{objective.objective_id}:{pose_id}"):
            return False
        unplaced = _unplaced_classes(outcomes, pose_id=pose_id)
        return not (unplaced if not classes else (unplaced & classes))
    return False


def count_answer_candidate(snapshot: ReadSnapshot, coverage: CoverageProjection) -> RoomCandidate | None:
    """The grounded ``ANSWER:COUNTS`` candidate (Plan 03 residual task R,
    F2): offered once every pending COUNT objective is ready
    (``count_objective_ready``) and every other objective is answerable from
    persisted reads. Its answer is projected from this session's own
    observations -- never from a legacy survey report. A qualified or
    relational count ("man", "near the table") is never answered this way:
    grounded counting cannot substantiate the qualifier."""
    ledger = snapshot.ledger
    if snapshot.terminal_state or ledger is None or ledger.status != GoalStatus.PENDING:
        return None
    counts = tuple(o for o in ledger.objectives if o.kind == GoalKind.COUNT)
    if not counts or any(o.relation or o.qualifier or o.terminal_condition not in _COUNT_TERMINALS
                         for o in counts):
        return None
    if not all(count_objective_ready(o, coverage, snapshot.outcomes) for o in counts):
        return None
    others = tuple(o for o in ledger.objectives if o.kind != GoalKind.COUNT)
    if any(is_search_objective(o) for o in others):
        return None  # a ledger with a search is answered by ANSWER:SEARCH (it renders the counts too)
    if others and not can_answer_objectives(snapshot, others):
        return None
    candidate_id = "ANSWER:COUNTS"
    observed = sorted(o.event_id for o in snapshot.outcomes if o.code == "OBSERVE_SUCCEEDED")
    digest = _digest({"candidate_id": candidate_id, "coverage": list(coverage.by_cell_and_class),
                      "observed": observed, "facts": relevant_hash(CandidateKind.ANSWER, snapshot)})
    if _answer_suppressed(snapshot.outcomes, candidate_id, digest):
        return None
    description = ("Answer every count objective from this session's own grounded observations "
                   "(confirmed/uncertain objects, people lower bounds and coverage caveats).")
    return RoomCandidate(candidate_id, CandidateKind.ANSWER, snapshot.state_version, digest, description,
                         description, coverage.evidence_refs + tuple(observed))


def _resolved_position(site: RoomSite, pose_id: str):
    return resolve_pose_id(site, pose_id)


def _uncovered_cell_ids(coverage: CoverageProjection) -> set[str]:
    return {entry.split(":", 1)[0] for entry in coverage.uncovered}


def _benefit(cell_ids, coverage: CoverageProjection) -> int:
    """How many currently-uncovered (cell, class) pairs a view over these
    cells would newly resolve, per the coverage snapshot passed in -- this
    is a single-call estimate, not a rolling simulation across the other
    candidates generated alongside it."""
    return sum(1 for entry in coverage.uncovered if entry.split(":", 1)[0] in cell_ids)


def _coverage_gain_description(pose_id: str, site: RoomSite, coverage: CoverageProjection) -> str:
    """Describe the unresolved coverage a declared pose can add this turn."""
    visible = set(dict(site.pose_visibility).get(pose_id, ()))
    pairs = tuple(entry for entry in coverage.uncovered if entry.split(":", 1)[0] in visible)
    cells = sorted({entry.split(":", 1)[0] for entry in pairs})
    if not cells:
        return "no declared unexplored cells are mapped to this pose"
    cell_word = "cell" if len(cells) == 1 else "cells"
    pair_word = "pair" if len(pairs) == 1 else "pairs"
    sample = ", ".join(cells[:3])
    if len(cells) > 3:
        sample += f", +{len(cells) - 3} more"
    return (f"adds a view of {len(cells)} unexplored {cell_word} ({sample}); "
            f"could resolve {len(pairs)} unresolved cell/class {pair_word}")
#: Refusals decided by the choice gate itself (``assess_choice`` and the
#: provider call), not by the world: Jev was unsure, malformed or
#: unreachable, while the action stays physically possible. Task 4b item 1:
#: they must not burn an OBSERVE or grounded ANSWER candidate for the rest of
#: the session (live 2026-09-24: center refused at 0.44 was never offered
#: again). A repeat of the same refusal on an unchanged digest still ends
#: the session through the controller's persisted NO_PROGRESS rule.
CONFIDENCE_GATE_REFUSALS = frozenset({
    "LOW_CONFIDENCE", "THIN_MARGIN", "MALFORMED_ANSWER", "ILLEGAL_OR_MALFORMED",
    "MISSING_OR_NON_MAX_SELECTION", "PROVIDER_ERROR",
})

_NON_BURNING_REFUSALS = CONFIDENCE_GATE_REFUSALS | {"STALE_DECISION"}


def _burns(outcome: ToolOutcome) -> bool:
    """Whether a refused outcome suppresses its candidate: physical, tool
    and supervisor refusals do; confidence-gate refusals do not."""
    return outcome.refusal_code is not None and outcome.refusal_code not in _NON_BURNING_REFUSALS


def _observe_suppressed(outcomes: tuple[ToolOutcome, ...], candidate_id: str, digest: str) -> bool:
    return any(o.candidate_id == candidate_id and o.relevant_state_sha256 == digest
               and (_burns(o) or o.code == "OBSERVE_SUCCEEDED")
               for o in outcomes)


def _answer_suppressed(outcomes: tuple[ToolOutcome, ...], candidate_id: str, digest: str) -> bool:
    return any(o.candidate_id == candidate_id and o.relevant_state_sha256 == digest and _burns(o)
               for o in outcomes)


def _resolves_text(pose_id: str, site: RoomSite, coverage: CoverageProjection) -> str:
    """``resolves 2 unexplored cell/class pairs (left-strip: computer, phone)``
    for the cells ``site.pose_visibility`` declares this pose can see."""
    cells = {cell for declared, cell_ids in site.pose_visibility if declared == pose_id for cell in cell_ids}
    by_cell: dict[str, list[str]] = {}
    for entry in sorted(coverage.uncovered):
        cell_id, _, class_name = entry.partition(":")
        if cell_id in cells:
            by_cell.setdefault(cell_id, []).append(class_name)
    count = sum(len(classes) for classes in by_cell.values())
    if not count:
        return "resolves no unexplored cell/class pair"
    entries = [(cell_id, class_name) for cell_id, classes in by_cell.items() for class_name in classes]
    detail = "; ".join(f"{cell_id}: {class_name}" for cell_id, class_name in entries[:3])
    if len(entries) > 3:
        detail += f"; +{len(entries) - 3} more"
    return f"resolves {count} unexplored cell/class pair{'' if count == 1 else 's'} ({detail})"


def _observe_digest(objective_id: str, pose_id: str, coverage: CoverageProjection) -> str:
    return _digest({"objective_id": objective_id, "pose_id": pose_id, "coverage": list(coverage.by_cell_and_class)})


def _observe_candidate(objective: GoalObjective, pose_id: str, description: str, site: RoomSite,
                       coverage: CoverageProjection, outcomes: tuple[ToolOutcome, ...],
                       snapshot_version: int, origin=None) -> RoomCandidate | None:
    candidate_id = f"OBSERVE:{objective.objective_id}:{pose_id}"
    digest = _observe_digest(objective.objective_id, pose_id, coverage)
    if _observe_suppressed(outcomes, candidate_id, digest):
        return None
    resolved = _resolved_position(site, pose_id)
    if resolved is None:
        # A pose that fails to resolve (malformed ID, or no semantic frame)
        # is never offered as a candidate -- defaulting its physical cost to
        # zero would let an unresolvable declared view sort ahead of every
        # legitimate one in the benefit/cost/ID ordering.
        return None
    # From the aircraft's current position when known (so the order and
    # Jev's reading favour a short hop over a cross-room one), else home.
    start = origin if origin is not None else site.home.nominal_pose
    distance_cm = math.hypot(resolved.x_cm - start.x_cm, resolved.y_cm - start.y_cm)
    # Task 4b item 2: what this view resolves and roughly how far it is, so
    # Jev has a reason to prefer one region over an otherwise identical one.
    description = (f"{description.rstrip('.')}: {_resolves_text(pose_id, site, coverage)}; "
                   f"Approximate direct distance from the drone's "
                   f"{'current position' if origin is not None else 'nominal home'}: {distance_cm:.0f} cm; "
                   "battery cost is assessed by the supervisor.")
    failures = [o for o in outcomes if o.candidate_id == candidate_id and o.refusal_code is not None]
    if failures:
        last = failures[-1]
        description += (f" Tried {len(failures)} time(s) before; last attempt failed ({last.code}"
                        + (f": {last.detail}" if last.detail else "") + ").")
    return RoomCandidate(candidate_id, CandidateKind.OBSERVE, snapshot_version, digest, description, description,
                         coverage.evidence_refs, (), PhysicalCost(distance_cm=distance_cm, requires_airborne=True))


def _cells_containing(site: RoomSite, x_cm: float, y_cm: float) -> tuple[str, ...]:
    def inside(polygon) -> bool:
        hit = False
        for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
            if (ay > y_cm) != (by > y_cm) and x_cm < ax + (y_cm - ay) * (bx - ax) / (by - ay):
                hit = not hit
        return hit
    return tuple(cell.cell_id for cell in site.coverage_cells if inside(tuple(cell.polygon_xy_cm)))


def _second_view_candidates(objective: GoalObjective, site: RoomSite, coverage: CoverageProjection,
                            outcomes: tuple[ToolOutcome, ...], snapshot_version: int,
                            origin=None) -> list[RoomCandidate]:
    """OBSERVE candidates that give an uncertain object its second view.

    A room count confirms an object only once two distinct declared poses
    have placed it (``counting``); one sweep's three looks are one view. Live
    2026-09-25: centre-rear declares all six cells, so its one sweep made
    coverage complete, no pose was offered again and the phone it saw stayed
    "1 uncertain". Every declared pose not yet observed that sees the cell
    an uncertain object was fused in is offered, most objects first; the
    next frames go through the same fusion. ANSWER:COUNTS stays offered."""
    frames = tuple(frame for o in outcomes if o.kind == CandidateKind.OBSERVE.value and o.code == "OBSERVE_SUCCEEDED"
                   for frame in (o.count_evidences or ((o.count_evidence,) if o.count_evidence else ())))
    pending = [(obj, _cells_containing(site, obj.x_cm, obj.y_cm))
               for obj in uncertain_objects(frames, tuple(objective.target_classes))]
    pending = [(obj, cells) for obj, cells in pending if cells]
    if not pending:
        return []
    visited = {pose for o in outcomes if (pose := _observed_pose_id(o)) is not None}
    scored = []
    for pose_id, cell_ids in sorted(site.pose_visibility):
        if pose_id in visited:
            continue
        targets = [(obj, cells) for obj, cells in pending if set(cells) & set(cell_ids)]
        if not targets:
            continue
        by_class: dict[str, int] = {}
        for obj, _ in targets:
            by_class[obj.class_name] = by_class.get(obj.class_name, 0) + 1
        what = ", ".join(f"{n} uncertain {name}" for name, n in sorted(by_class.items()))
        cells = sorted({cell for _, cells in targets for cell in cells if cell in cell_ids})
        seen = sorted({pose for obj, _ in targets for pose in obj.seen_from})
        description = (f"Observe declared pose {pose_id} for objective {objective.objective_id}: a second view of "
                       f"{what} in {', '.join(cells)} (so far seen only from {', '.join(seen) or 'one view'}); "
                       "a second distinct view is what lets the count confirm it, and its detections are merged "
                       "with the earlier ones.")
        candidate = _observe_candidate(objective, pose_id, description, site, coverage, outcomes, snapshot_version,
                                       origin)
        if candidate is not None:
            scored.append((len(targets), candidate))
    scored.sort(key=lambda item: (-item[0], item[1].physical_cost.distance_cm, item[1].candidate_id))
    return [candidate for _, candidate in scored]


def _incomplete_answer_candidate(objective: GoalObjective, coverage: CoverageProjection,
                                 outcomes: tuple[ToolOutcome, ...], snapshot_version: int, *,
                                 region_pose_id: str | None = None) -> RoomCandidate | None:
    candidate_id = f"ANSWER:{objective.objective_id}:INCOMPLETE"
    digest = _digest({"objective_id": objective.objective_id, "coverage": list(coverage.by_cell_and_class)})
    if _answer_suppressed(outcomes, candidate_id, digest):
        return None
    if region_pose_id is None:
        description = (f"No legal declared view can resolve objective {objective.objective_id}'s remaining "
                       f"coverage ({', '.join(coverage.uncovered)}); answer explicitly incomplete rather than loop.")
    else:
        description = (f"The region requested for objective {objective.objective_id} (declared pose "
                       f"{region_pose_id}) cannot be observed now; answer explicitly incomplete rather than loop.")
    return RoomCandidate(candidate_id, CandidateKind.ANSWER, snapshot_version, digest, description, description,
                         coverage.evidence_refs)


# ---------------------------------------------------------------------------
# Task B item 2b: autonomous search ("find the phone")
# ---------------------------------------------------------------------------


def is_search_objective(objective: GoalObjective) -> bool:
    """A FIND_OBSERVE objective this session can search for by flying: an
    unqualified, non-relational find whose terminal condition is OBSERVED,
    scoped to the room or to one requested semantic region. Every other
    FIND_OBSERVE (a qualifier, a relation, a VALIDATED closer look) keeps its
    existing route."""
    return (objective.kind == GoalKind.FIND_OBSERVE and objective.terminal_condition == "OBSERVED"
            and not objective.relation and not objective.qualifier and bool(objective.target_classes)
            and (objective.spatial_scope == "ROOM" or _region_pose_id(objective.spatial_scope) is not None))


def search_pose_ids(objective: GoalObjective, site: RoomSite) -> tuple[str, ...]:
    """The declared poses a search objective may observe: every pose the site
    declares visibility for (a room-wide search), or the one requested
    region's pose (a region search)."""
    declared = tuple(sorted(pose_id for pose_id, _ in site.pose_visibility))
    if objective.spatial_scope == "ROOM":
        return declared
    pose_id = _region_pose_id(objective.spatial_scope)
    return (pose_id,) if pose_id is not None else ()


def _observed_pose_id(outcome: ToolOutcome) -> str | None:
    if outcome.code != "OBSERVE_SUCCEEDED":
        return None
    if outcome.count_evidence is not None:
        return outcome.count_evidence.pose_id
    return outcome.candidate_id.rsplit(":", 1)[-1]


def search_sightings(objective: GoalObjective, site: RoomSite,
                     outcomes: tuple[ToolOutcome, ...]) -> tuple[tuple[ToolOutcome, object], ...]:
    """Every grounded detection of a target class in this search's scope.

    Each entry is ``(outcome, item)`` where ``item`` is a placed
    ``SpatialEstimate`` or an ``UnplacedDetection`` -- a sighting the session
    saw but could not place is still a sighting, never silently dropped. A
    frame whose pose was stale at capture is still evidence the object was
    seen, so it counts here (its detections are then unplaced)."""
    scope = set(search_pose_ids(objective, site))
    classes = set(objective.target_classes)
    sightings = []
    for outcome in outcomes:
        evidence = outcome.count_evidence
        if outcome.code != "OBSERVE_SUCCEEDED" or evidence is None or evidence.pose_id not in scope:
            continue
        sightings.extend((outcome, item) for item in (*evidence.estimates, *evidence.unplaced)
                         if item.class_name in classes)
    return tuple(sightings)


def _search_candidates(objective: GoalObjective, site: RoomSite, coverage: CoverageProjection,
                       outcomes: tuple[ToolOutcome, ...], snapshot_version: int,
                       origin=None) -> list[RoomCandidate]:
    if search_sightings(objective, site, outcomes):
        return []  # found (confirmed or uncertain): stop searching
    observed = {_observed_pose_id(outcome) for outcome in outcomes}
    classes = ", ".join(objective.target_classes)
    candidates = []
    for pose_id in search_pose_ids(objective, site):
        if pose_id in observed:
            continue
        gain = _benefit(set(dict(site.pose_visibility).get(pose_id, ())), coverage)
        coverage_note = (f"; {_coverage_gain_description(pose_id, site, coverage)}" if gain else "")
        description = (f"Search for {classes}: observe unvisited declared search viewpoint {pose_id} "
                       f"(objective {objective.objective_id}); this advances the search"
                       f"{coverage_note}.")
        candidate = _observe_candidate(objective, pose_id, description, site, coverage, outcomes, snapshot_version,
                                       origin)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def search_objective_ready(objective: GoalObjective, site: RoomSite, coverage: CoverageProjection,
                           outcomes: tuple[ToolOutcome, ...]) -> bool:
    """A search can be answered once a target detection is grounded, or once
    no declared region is left that could still be observed for it."""
    return (bool(search_sightings(objective, site, outcomes))
            or not _search_candidates(objective, site, coverage, outcomes, 0))


def search_answer_candidate(snapshot: ReadSnapshot, site: RoomSite,
                            coverage: CoverageProjection) -> RoomCandidate | None:
    """``ANSWER:SEARCH``: offered once every search objective is ready (found,
    or every reachable region observed), every COUNT objective is ready and
    every other objective is answerable from persisted reads. Answering
    requests the supervisor's safe return before the answer is recorded."""
    ledger = snapshot.ledger
    if snapshot.terminal_state or ledger is None or ledger.status != GoalStatus.PENDING:
        return None
    searches = tuple(o for o in ledger.objectives if is_search_objective(o))
    if not searches or not all(search_objective_ready(o, site, coverage, snapshot.outcomes) for o in searches):
        return None
    counts = tuple(o for o in ledger.objectives if o.kind == GoalKind.COUNT)
    if any(o.relation or o.qualifier or o.terminal_condition not in _COUNT_TERMINALS
           or not count_objective_ready(o, coverage, snapshot.outcomes) for o in counts):
        return None
    others = tuple(o for o in ledger.objectives if o.kind != GoalKind.COUNT and not is_search_objective(o))
    if others and not can_answer_objectives(snapshot, others):
        return None
    candidate_id = "ANSWER:SEARCH"
    observed = sorted(o.event_id for o in snapshot.outcomes if o.code == "OBSERVE_SUCCEEDED")
    digest = _digest({"candidate_id": candidate_id, "coverage": list(coverage.by_cell_and_class),
                      "observed": observed, "facts": relevant_hash(CandidateKind.ANSWER, snapshot)})
    if _answer_suppressed(snapshot.outcomes, candidate_id, digest):
        return None
    found = [o.objective_id for o in searches if search_sightings(o, site, snapshot.outcomes)]
    description = ("Answer the search from this session's own grounded observations -- where the object was "
                   "seen, its placement and confidence (uncertain stays uncertain), or 'not found in observed "
                   f"regions' with the coverage note -- then return home. Found for: {', '.join(found) or 'none'}.")
    return RoomCandidate(candidate_id, CandidateKind.ANSWER, snapshot.state_version, digest, description,
                         description, coverage.evidence_refs + tuple(observed))


def build_observation_candidates(objectives: tuple[GoalObjective, ...], site: RoomSite,
                                 coverage: CoverageProjection, outcomes: tuple[ToolOutcome, ...],
                                 snapshot_version: int, origin=None) -> tuple[RoomCandidate, ...]:
    """OBSERVE candidates for COUNT objectives.

    User-requested semantic regions are generated first, in their original
    ordinal order, unconditionally (they were explicitly asked for, so they
    are never gated on coverage benefit). A region already observed
    successfully is never offered again (its answer route is
    ``count_answer_candidate``); a region that can no longer be offered
    without having been observed (refused, unresolvable) gets one explicit
    incomplete-answer candidate instead (residual F2). For a room-wide count, only the
    legal site-declared views (``site.pose_visibility``) that would resolve
    at least one currently-uncovered coverage cell/class pair are offered,
    ordered by descending benefit, then physical cost, then stable pose ID.
    A room-wide objective coverage still leaves incomplete, with nothing
    left able to improve it (every useful view already suppressed, or none
    ever existed), gets one explicit incomplete-answer candidate instead of
    silently looping.

    This stays separate from ``build_candidates``; ``RoomController`` offers
    it (and ``count_answer_candidate``) only while observation is enabled.
    """
    count_objectives = sorted((o for o in objectives if o.kind == GoalKind.COUNT), key=lambda o: o.ordinal)
    region_candidates: list[RoomCandidate] = []
    room_objectives: list[GoalObjective] = []
    for objective in count_objectives:
        pose_id = _region_pose_id(objective.spatial_scope)
        if pose_id is not None:
            if _observed(outcomes, f"OBSERVE:{objective.objective_id}:{pose_id}"):
                # Fix round 4 (Q4): the region was actually flown, but a
                # detection of one of its target classes still could not be
                # placed -- never leave this objective with no route at all;
                # offer the explicit incomplete answer instead of silently
                # stalling (count_answer_candidate withholds ANSWER:COUNTS
                # while any pending COUNT objective is not ready).
                if not count_objective_ready(objective, coverage, outcomes):
                    candidate = _incomplete_answer_candidate(objective, coverage, outcomes, snapshot_version,
                                                             region_pose_id=pose_id)
                    if candidate is not None:
                        region_candidates.append(candidate)
                continue
            description = (f"Observe declared pose {pose_id}, the requested region for objective "
                           f"{objective.objective_id}; {_coverage_gain_description(pose_id, site, coverage)}.")
            candidate = _observe_candidate(objective, pose_id, description, site, coverage, outcomes, snapshot_version,
                                           origin)
            if candidate is None:
                candidate = _incomplete_answer_candidate(objective, coverage, outcomes, snapshot_version,
                                                         region_pose_id=pose_id)
            if candidate is not None:
                region_candidates.append(candidate)
        elif objective.spatial_scope == "ROOM":
            room_objectives.append(objective)

    room_candidates: list[RoomCandidate] = []
    for objective in room_objectives:
        scored: list[tuple[int, RoomCandidate]] = []
        for pose_id, cell_ids in sorted(site.pose_visibility):
            benefit = _benefit(set(cell_ids), coverage)
            if benefit <= 0:
                continue
            description = (f"Observe declared pose {pose_id} for objective {objective.objective_id}: "
                           f"{_coverage_gain_description(pose_id, site, coverage)}.")
            candidate = _observe_candidate(objective, pose_id, description, site, coverage, outcomes, snapshot_version,
                                           origin)
            if candidate is not None:
                scored.append((benefit, candidate))
        # Coverage gained per distance flown (each metre costs one extra
        # unit), so a nearby zone beats a slightly better one across the
        # room: fewer long fixless hops, which the supervisor refuses.
        scored.sort(key=lambda item: (-item[0] / (1 + item[1].physical_cost.distance_cm / 100),
                                      item[1].physical_cost.distance_cm, item[1].candidate_id))
        objective_candidates = [candidate for _, candidate in scored]
        # Fix round 4 (Q3/Q4): fall back to the explicit incomplete answer
        # whenever this objective is not ready to complete -- not only when
        # declared cell coverage itself is incomplete, but also when a
        # target class has no declared coverage at all, or a detection of
        # one of its target classes could not be placed. Every one of those
        # cases already has no further scored OBSERVE candidate to offer
        # instead (no pose can newly resolve a class no cell declares, or an
        # already-unplaceable detection).
        if not objective_candidates and not count_objective_ready(objective, coverage, outcomes):
            fallback = _incomplete_answer_candidate(objective, coverage, outcomes, snapshot_version)
            if fallback is not None:
                objective_candidates = [fallback]
        offered = {candidate.candidate_id for candidate in objective_candidates}
        objective_candidates.extend(candidate for candidate in _second_view_candidates(
            objective, site, coverage, outcomes, snapshot_version, origin) if candidate.candidate_id not in offered)
        room_candidates.extend(objective_candidates)

    # Task B item 2b: one-checkpoint OBSERVE candidates over the declared
    # regions for every search objective still looking. Jev chooses the
    # order; a region already observed (by any objective) is never offered
    # again, and a found target stops the search.
    search_candidates: list[RoomCandidate] = []
    offered = {c.candidate_id for c in (*region_candidates, *room_candidates)}
    for objective in sorted((o for o in objectives if is_search_objective(o)), key=lambda o: o.ordinal):
        for candidate in _search_candidates(objective, site, coverage, outcomes, snapshot_version, origin):
            if candidate.candidate_id not in offered:
                offered.add(candidate.candidate_id)
                search_candidates.append(candidate)

    return tuple(region_candidates) + tuple(room_candidates) + tuple(search_candidates)
