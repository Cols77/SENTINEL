"""Plan 01 admits only persisted reads and deterministic conversational actions."""
from .models import CandidateKind, GoalKind, GoalStatus, RoomCandidate
from .queries import READ_KINDS, ReadSnapshot, relevant_hash

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
    for objective in snapshot.ledger.objectives:
        if objective.kind in (GoalKind.FOLLOW, GoalKind.LAND_NEAR) or objective.relation or objective.qualifier:
            return False
        if objective.terminal_condition not in ("REPORTED", "OBSERVED") or objective.spatial_scope not in ("ROOM", "CURRENT_VIEW"):
            return False
        expected = {GoalKind.ANSWER_STATE: CandidateKind.READ_DRONE, GoalKind.COUNT: CandidateKind.READ_COUNTS,
                    GoalKind.FIND_OBSERVE: CandidateKind.READ_OBSERVATIONS}[objective.kind]
        facts = [f for f in snapshot.facts if f.kind == expected]
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
