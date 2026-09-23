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


def build_candidates(snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
    if snapshot.terminal_state:
        return ()
    kinds = (*READ_KINDS, CandidateKind.ANSWER, CandidateKind.CLARIFY, CandidateKind.ESCALATE)
    candidates = []
    for kind in kinds:
        if kind == CandidateKind.ANSWER and not can_answer(snapshot):
            continue
        digest = relevant_hash(kind, snapshot)
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


def count_objective_ready(objective: GoalObjective, coverage: CoverageProjection,
                          outcomes: tuple[ToolOutcome, ...]) -> bool:
    """Whether a COUNT objective can be answered as complete: a room-wide
    count once declared coverage is complete, a region count once its
    requested region was actually observed (``OBSERVE_SUCCEEDED``, which the
    controller records only after verifying pose, capture and authorized
    perception)."""
    scope, pose_id = count_scope(objective)
    if scope == "ROOM":
        return coverage.complete
    if scope == "VIEWPOINT":
        return _observed(outcomes, f"OBSERVE:{objective.objective_id}:{pose_id}")
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
    if not counts or any(o.relation or o.qualifier or o.terminal_condition not in ("REPORTED", "OBSERVED")
                         for o in counts):
        return None
    if not all(count_objective_ready(o, coverage, snapshot.outcomes) for o in counts):
        return None
    others = tuple(o for o in ledger.objectives if o.kind != GoalKind.COUNT)
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


def _observe_suppressed(outcomes: tuple[ToolOutcome, ...], candidate_id: str, digest: str) -> bool:
    return any(o.candidate_id == candidate_id and o.relevant_state_sha256 == digest
               and (o.refusal_code is not None or o.code == "OBSERVE_SUCCEEDED")
               for o in outcomes)


def _answer_suppressed(outcomes: tuple[ToolOutcome, ...], candidate_id: str, digest: str) -> bool:
    return any(o.candidate_id == candidate_id and o.relevant_state_sha256 == digest and o.refusal_code is not None
               for o in outcomes)


def _observe_digest(objective_id: str, pose_id: str, coverage: CoverageProjection) -> str:
    return _digest({"objective_id": objective_id, "pose_id": pose_id, "coverage": list(coverage.by_cell_and_class)})


def _observe_candidate(objective: GoalObjective, pose_id: str, description: str, site: RoomSite,
                       coverage: CoverageProjection, outcomes: tuple[ToolOutcome, ...],
                       snapshot_version: int) -> RoomCandidate | None:
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
    home = site.home.nominal_pose
    distance_cm = math.hypot(resolved.x_cm - home.x_cm, resolved.y_cm - home.y_cm)
    return RoomCandidate(candidate_id, CandidateKind.OBSERVE, snapshot_version, digest, description, description,
                         coverage.evidence_refs, (), PhysicalCost(distance_cm=distance_cm, requires_airborne=True))


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


def build_observation_candidates(objectives: tuple[GoalObjective, ...], site: RoomSite,
                                 coverage: CoverageProjection, outcomes: tuple[ToolOutcome, ...],
                                 snapshot_version: int) -> tuple[RoomCandidate, ...]:
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
                continue
            description = f"Observe declared pose {pose_id}, the region requested for objective {objective.objective_id}."
            candidate = _observe_candidate(objective, pose_id, description, site, coverage, outcomes, snapshot_version)
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
            description = (f"Observe declared pose {pose_id}: resolves {benefit} currently unresolved "
                           f"coverage cell/class pair(s) for objective {objective.objective_id}.")
            candidate = _observe_candidate(objective, pose_id, description, site, coverage, outcomes, snapshot_version)
            if candidate is not None:
                scored.append((benefit, candidate))
        scored.sort(key=lambda item: (-item[0], item[1].physical_cost.distance_cm, item[1].candidate_id))
        objective_candidates = [candidate for _, candidate in scored]
        if not objective_candidates and not coverage.complete:
            fallback = _incomplete_answer_candidate(objective, coverage, outcomes, snapshot_version)
            if fallback is not None:
                objective_candidates = [fallback]
        room_candidates.extend(objective_candidates)

    return tuple(region_candidates) + tuple(room_candidates)
