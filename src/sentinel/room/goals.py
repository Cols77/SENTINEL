"""Bounded fixed-choice goal compilation; this module never executes actions.

Entity references name *future verified results*, not detections already proven
by compilation. Dependencies must be satisfied before a consumer can use them.
The accepted event carries the complete source snapshot and compiler profile.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re
import time
import uuid

from sentinel.jev_choice import assess_choice
from sentinel.judgment import ChoiceJudge

from .models import GoalKind, GoalStatus, RoomSnapshot
from .store import RoomStore, VersionConflict


SCHEMA_VERSION = "room.goal-compile.v1"
SUPPORTED_CLASSES = ("computer", "person", "phone", "pen")
_SAFE = {
    "CLARIFICATION_REQUIRED": "The request cannot be represented unambiguously; ask for clarification.",
    "UNSUPPORTED": "The requested goal, class, relationship or condition is unsupported.",
}
_PHYSICAL = (GoalKind.FOLLOW, GoalKind.LAND_NEAR)
_FAMILY = {
    "ANSWER_STATE": "Answer a read-only question about drone state, battery or position.",
    "COUNT": "Count specified supported classes in the requested scope.",
    "FIND_OBSERVE": "Find, observe, validate a detection, or verify a supported relationship.",
    "FOLLOW": "Follow one resolved target for a bounded interval, then return.",
    "LAND_NEAR": "Observe one resolved target, then land at a certified zone near that same target.",
}
_TERMINALS = {
    GoalKind.ANSWER_STATE: ("REPORTED",),
    GoalKind.COUNT: ("REPORTED", "RETURN_HOME"),
    GoalKind.FIND_OBSERVE: ("OBSERVED", "VALIDATED", "RELATION_VERIFIED", "RETURN_HOME"),
    GoalKind.FOLLOW: ("FOLLOW_BOUNDED_RETURN",),
    GoalKind.LAND_NEAR: ("LANDED_AT_CERTIFIED_ZONE",),
}
_VERBS = r"(?:find|follow|land|count|observe|inspect|look|validate|verify|report|tell|what|how)\b"
_CLAUSE_BOUNDARY = re.compile(
    rf"[;.!?\n]+|,\s*(?=(?:then\s+)?{_VERBS})|"
    rf"\b(?:and\s+then|and|then)\s+(?=(?:please\s+)?{_VERBS})",
    re.IGNORECASE,
)
_ALIASES = re.compile(r"\b(?:cell\s+phones?|smartphones?|humans?|people)\b", re.IGNORECASE)
_HOLDING_PHONE = re.compile(
    r"\bhold(?:s|ing)?\s+(?:(?:a|an|the|his|her|their)\s+)?phones?\b", re.IGNORECASE,
)
_PRONOUN_FORMS = {
    "he": "him", "him": "him", "she": "her", "her": "her",
    "they": "them", "them": "them", "it": "it", "you": "you",
}
_PRONOUNS = re.compile(r"\b(?:he|him|she|her|they|them|it|you)\b", re.IGNORECASE)


@dataclass(frozen=True)
class EntityRef:
    """A result reference, optionally anchored to a pose or prior actor identity.

    VERIFIED_DESIGNATED_PERSON uses relative_to for the actor whose identity
    must be retained through verification; NEAREST_PHONE uses a pose reference.
    """
    ref_id: str
    source_objective_id: str | None
    entity_role: str
    relative_to: str | None


@dataclass(frozen=True)
class GoalObjective:
    objective_id: str
    ordinal: int
    kind: GoalKind
    target_classes: tuple[str, ...]
    qualifier: str | None
    relation: str | None
    spatial_scope: str
    terminal_condition: str
    subject_ref: EntityRef | None
    target_ref: EntityRef | None
    depends_on_objective_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_classes", tuple(self.target_classes))
        object.__setattr__(self, "depends_on_objective_ids", tuple(self.depends_on_objective_ids))
        if self.kind in _PHYSICAL and (
            self.target_ref is None or self.target_ref.source_objective_id is None
            or self.target_ref.source_objective_id not in self.depends_on_objective_ids
        ):
            raise ValueError("physical goals require a resolved target and its observation dependency")


@dataclass(frozen=True)
class GoalLedger:
    request_text: str
    objectives: tuple[GoalObjective, ...]
    status: GoalStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "objectives", tuple(self.objectives))

    @property
    def physical_objectives(self) -> tuple[GoalObjective, ...]:
        """Goals eligible for later capability-gated candidate generation."""
        if self.status != GoalStatus.PENDING:
            return ()
        return tuple(goal for goal in self.objectives if goal.kind in _PHYSICAL)


@dataclass(frozen=True)
class _Clause:
    clause_id: str
    ordinal: int
    text: str


class _Rejected(Exception):
    def __init__(self, reason: str, status: GoalStatus = GoalStatus.CLARIFICATION_REQUIRED):
        self.reason = reason
        self.status = status


_CLASS_NOUNS = {
    "computer": r"computers?|laptops?|pcs?|desktops?",
    "phone": r"phones?|smartphones?|mobiles?|cellphones?",
    "pen": r"pens?",
    "person": r"persons?|people|men|man|women|woman|humans?",
}


def _named_classes(text: str) -> tuple[str, ...]:
    """Supported classes named outright in ``text``, in order of first mention."""
    hits = []
    for name, pattern in _CLASS_NOUNS.items():
        match = re.search(rf"\b(?:{pattern})\b", text, re.IGNORECASE)
        if match:
            hits.append((match.start(), name))
    return tuple(name for _, name in sorted(hits))


#: Goal compilation only interprets the request; it moves nothing (the room
#: loop's read/observe gate is also 0.5). Driving Mission Control's Jev
#: rehearsal, the real model scored single steps of "count the phones and
#: computers in the room" at 0.55-0.74, so 0.8 (and even 0.6) refused it.
#: A below-gate lean toward a real label is re-asked once (``reasks``).
GOAL_ACT_AT = 0.50


class GoalCompiler:
    def __init__(
        self, judge: ChoiceJudge, store: RoomStore, *, input_limit_bytes: int,
        prompt_revision: str, act_at: float = GOAL_ACT_AT, min_margin: float = 0.15,
        reasks: int = 1,
    ) -> None:
        self.act_at, self.min_margin, self.reasks = act_at, min_margin, reasks
        if type(input_limit_bytes) is not int or input_limit_bytes <= 0:
            raise ValueError("input_limit_bytes must be a positive integer")
        if not isinstance(prompt_revision, str) or not prompt_revision.strip():
            raise ValueError("prompt_revision must name a versioned prompt profile")
        self.judge = judge
        self.store = store
        self.input_limit_bytes = input_limit_bytes
        self.prompt_revision = prompt_revision

    def compile(self, session_id: str, request_text: str, snapshot: RoomSnapshot) -> GoalLedger:
        if snapshot.session_id != session_id:
            raise ValueError("snapshot belongs to a different session")
        if snapshot.state_version != self.store.session_version(session_id):
            raise VersionConflict("goal compilation requires the current session snapshot")
        accepted = tuple(
            event for event in self.store.events(session_id)
            if event.event_type == "GOAL_LEDGER_ACCEPTED"
        )
        goal_revision = accepted[-1].payload["goal_revision"] if accepted else 0
        if snapshot.goal_revision != goal_revision:
            raise VersionConflict("snapshot goal revision differs from the persisted accepted ledger")
        return _Compilation(self, session_id, request_text, snapshot).run()


class _Compilation:
    """Per-call state, isolated even when a compiler is shared by callers."""

    def __init__(self, compiler: GoalCompiler, session_id: str, request: str, snapshot: RoomSnapshot):
        self.compiler = compiler
        self.session_id = session_id
        self.request = request
        self.snapshot = snapshot
        self.correlation_id = str(uuid.uuid4())
        self.attempts: list[dict] = []
        self.accepted_choices: list[dict] = []
        self.goals: list[GoalObjective] = []
        self.clauses: tuple[_Clause, ...] = ()
        self.clause: _Clause | None = None
        self.drone = EntityRef(
            f"drone:{session_id}:state:{snapshot.state_version}:pose:{snapshot.pose_revision}",
            None, "CURRENT_DRONE", None,
        )

    def run(self) -> GoalLedger:
        reason = None
        try:
            if not self.request.strip():
                raise _Rejected("EMPTY_INPUT")
            if len(self.request.encode("utf-8")) > self.compiler.input_limit_bytes:
                raise _Rejected("INPUT_LIMIT_EXCEEDED")
            self.clauses = _propose_clauses(self.request)
            if not self.clauses:
                raise _Rejected("EMPTY_CLAUSE_SET")
            remaining = list(self.clauses)
            # Each accepted NEXT removes exactly one clause. DONE is requested
            # once more after exhaustion; provider responses cannot grow the set.
            while True:
                self.clause = None
                options = {f"NEXT_OBJECTIVE:{clause.clause_id}": clause.text for clause in remaining}
                options["DONE"] = "All requested clauses have been represented, in request order."
                selected = self.choose("next_objective", options)
                if selected == "DONE":
                    if remaining:
                        raise _Rejected("UNREPRESENTED_CLAUSES")
                    break
                if selected != f"NEXT_OBJECTIVE:{remaining[0].clause_id}":
                    raise _Rejected("REQUEST_ORDER_CHANGED")
                self.clause = remaining.pop(0)
                self.build_objective()
            self.choose("consistency", {
                "CONSISTENT": (
                    "Every objective, qualifier, class subset, relation, scope, reference and terminal "
                    "condition faithfully represents the original request in its original order. "
                    "No contradictory scopes, omitted request, invented meaning or ambiguous pronoun remains."
                ),
            })
            ledger = GoalLedger(self.request, tuple(self.goals), GoalStatus.PENDING)
        except _Rejected as rejected:
            reason = rejected.reason
            ledger = GoalLedger(self.request, (), rejected.status)
        self.persist(ledger, reason)
        return ledger

    def choose(self, name: str, options: dict[str, str]) -> str:
        criteria = {**options, **_SAFE}
        state = {
            "schema_version": SCHEMA_VERSION,
            "request_text": self.request,
            "snapshot": asdict(self.snapshot),
            "clauses": [asdict(clause) for clause in self.clauses],
            "clause_id": self.clause.clause_id if self.clause else None,
            "normalized_clause": _normalize(self.clause.text) if self.clause else None,
            "tentative_objectives": [asdict(goal) for goal in self.goals],
            "accepted_choices": self.accepted_choices,
        }
        state = json.loads(json.dumps(state))
        instruction = (
            "Select exactly one controller-owned label for " + name + ". Preserve request order, "
            "qualifiers and identity. Future result references are obligations, not verified evidence. "
            "Choose CLARIFICATION_REQUIRED for ambiguity and UNSUPPORTED for unavailable semantics."
        )
        for attempt in range(1 + self.compiler.reasks):
            started = time.monotonic_ns()
            provider_error = None
            try:
                answer = self.compiler.judge.choice(
                    deepcopy(state), instruction, criteria=dict(criteria), name=name,
                )
            except Exception as exc:  # provider text/credentials must not enter audit records
                provider_error = type(exc).__name__
                answer = None
            assessment = assess_choice(
                answer, criteria, escalation_label="CLARIFICATION_REQUIRED",
                act_at=self.compiler.act_at, min_margin=self.compiler.min_margin,
            )
            audit_assessment = asdict(assessment)
            # Labels remain values, never provider-controlled payload keys/header pairs.
            audit_assessment["probabilities"] = [
                {"label": label, "probability": probability} for label, probability in assessment.probabilities
            ]
            self.attempts.append({
                "name": name, "state": state, "instruction": instruction, "criteria": criteria,
                "assessment": audit_assessment, "provider_error": provider_error, "attempt": attempt + 1,
                "latency_ms": (time.monotonic_ns() - started) // 1_000_000,
            })
            # A single-option confirmation (one controller label vs clarify/unsupported)
            # is binary: accept when the model put gate-level probability on that label
            # even if its scalar confidence is lower (real model: NEW_ENTITY p 0.61,
            # scalar below 0.5, twice, in Mission Control's Jev rehearsal).
            if (assessment.outcome == "LOW_CONFIDENCE" and len(options) == 1
                    and assessment.selected_label in options
                    and (assessment.selected_probability or 0.0) >= self.compiler.act_at):
                assessment = replace(assessment, final_label=assessment.selected_label,
                                     outcome="ACCEPTED_CONFIRMATION")
                self.attempts[-1]["assessment"]["final_label"] = assessment.final_label
                self.attempts[-1]["assessment"]["outcome"] = assessment.outcome
                break
            # Re-ask once only when the model leaned to a real label but below the gate
            # (or the provider failed); an explicit CLARIFICATION/UNSUPPORTED stands.
            retryable = provider_error is not None or (
                assessment.outcome == "LOW_CONFIDENCE" and assessment.selected_label not in _SAFE)
            if assessment.final_label not in _SAFE or not retryable:
                break
            instruction = (instruction + " A previous answer for " + name + " leaned to "
                           + str(assessment.selected_label) + " without enough confidence; "
                           "re-read the request and answer again.")
        if assessment.final_label in _SAFE:
            raise _Rejected(provider_error or assessment.outcome, GoalStatus(assessment.final_label))
        self.accepted_choices.append({
            "clause_id": self.clause.clause_id if self.clause else None,
            "name": name, "label": assessment.final_label,
        })
        return assessment.final_label

    def build_objective(self) -> None:
        assert self.clause is not None
        kind = GoalKind(self.choose("goal_family", _FAMILY))
        classes: tuple[str, ...] = ()
        qualifier = relation = None
        if kind != GoalKind.ANSWER_STATE:
            classes = self.select_classes()
            if kind in _PHYSICAL and len(classes) != 1:
                raise _Rejected("PHYSICAL_TARGET_MUST_BE_SINGULAR")
            selected = self.choose("qualifier", {
                "NONE": "No actor qualifier is requested.",
                "MAN": "Retain man; only a designated actor can substantiate this qualifier.",
                "DESIGNATED": "A specifically designated actor, never an arbitrary person box.",
            })
            qualifier = None if selected == "NONE" else selected.lower()
            if qualifier in ("man", "designated") and classes != ("person",):
                raise _Rejected("QUALIFIER_CLASS_MISMATCH")
            if re.search(r"\bman\b", self.clause.text, re.IGNORECASE) and qualifier != "man":
                raise _Rejected("EXPLICIT_QUALIFIER_DROPPED")
            selected = self.choose("relation", {
                "NONE": "No supported relation is requested.",
                "HOLDS_PHONE": "Verify that the designated person holds the phone; proximity is insufficient.",
                "NEXT_TO_DRONE": "Find the nearest phone relative to this explicit drone pose snapshot.",
            })
            relation = None if selected == "NONE" else selected
            if _HOLDING_PHONE.search(_normalize(self.clause.text)) and relation != "HOLDS_PHONE":
                raise _Rejected("EXPLICIT_HOLDING_RELATION_DROPPED")
            if re.search(r"\bnext\s+to\s+you\b", self.clause.text, re.IGNORECASE) and relation != "NEXT_TO_DRONE":
                raise _Rejected("RELATIVE_POSE_ATTACHMENT_DROPPED")
        scope = self.select_scope()
        objective_id = f"g{self.snapshot.goal_revision + 1}:{self.clause.clause_id}"
        dependencies: tuple[str, ...] = ()
        target = None
        if kind == GoalKind.ANSWER_STATE:
            self.decide_in_code("entity_reference", "CURRENT_DRONE")
            subject = target = self.drone
        elif kind in _PHYSICAL:
            if relation == "NEXT_TO_DRONE":
                self.decide_in_code("relative_reference", "CURRENT_DRONE")
            target, producer = self.bind_target(classes, relative_drone=relation == "NEXT_TO_DRONE")
            if producer is None:
                producer = self.observation(
                    objective_id + ":observe", classes, qualifier, relation, scope, bind_new=True,
                )
                self.goals.append(producer)
                target = producer.subject_ref
            elif relation in ("NEXT_TO_DRONE", "HOLDS_PHONE"):
                # An attached relation is a new evidence obligation even when
                # its subject is already bound. Preserve the chosen identity.
                dependencies = tuple(dict.fromkeys((target.source_objective_id, producer.objective_id)))
                if target == producer.subject_ref:
                    qualifier = qualifier or producer.qualifier
                observed = self.observation(
                    objective_id + ":observe", classes, qualifier, relation, scope, bind_new=True,
                )
                refined_subject = target
                if relation == "HOLDS_PHONE":
                    refined_subject = replace(observed.subject_ref, relative_to=target.ref_id)
                producer = replace(
                    observed, subject_ref=refined_subject, depends_on_objective_ids=dependencies,
                )
                self.goals.append(producer)
                target = producer.subject_ref
            subject = self.drone
            dependencies = tuple(dict.fromkeys((*dependencies, target.source_objective_id, producer.objective_id)))
            # Anaphora carry the selected actor's qualifier; they cannot broaden
            # a verified designated-man result back to any generic person.
            if target == producer.subject_ref:
                qualifier = qualifier or producer.qualifier
        elif _reference_pronouns(self.clause.text, relative_drone=relation == "NEXT_TO_DRONE"):
            if relation == "NEXT_TO_DRONE":
                self.decide_in_code("relative_reference", "CURRENT_DRONE")
            target, producer = self.bind_target(classes, relative_drone=relation == "NEXT_TO_DRONE")
            subject = target
            dependencies = tuple(dict.fromkeys((target.source_objective_id, producer.objective_id)))
            if target == producer.subject_ref:
                qualifier = qualifier or producer.qualifier
            if relation == "HOLDS_PHONE":
                verified = self.observation(
                    objective_id, classes, qualifier, relation, scope, bind_new=True,
                )
                subject = EntityRef(
                    verified.subject_ref.ref_id, objective_id, verified.subject_ref.entity_role,
                    target.ref_id,
                )
                target = verified.target_ref
            elif relation == "NEXT_TO_DRONE":
                relative = self.observation(
                    objective_id, classes, qualifier, relation, scope, bind_new=True,
                )
                target = relative.target_ref
        else:
            observed = self.observation(objective_id, classes, qualifier, relation, scope)
            subject, target = observed.subject_ref, observed.target_ref
        text = self.clause.text.lower() if self.clause else ""
        if kind == GoalKind.COUNT:
            # The request's own wording decides it; the real model split
            # REPORTED/RETURN_HOME at 0.49-0.66 (8/17 re-asked) on "count the
            # phones and computers in the room", which says neither.
            returns = re.search(r"\b(return|come\s+back|go\s+back|home)\b", self.request, re.IGNORECASE)
            terminal = self.decide_in_code("terminal_condition", "RETURN_HOME" if returns else "REPORTED")
        elif (kind == GoalKind.FIND_OBSERVE and relation is None
              and not re.search(r"\b(validat|verif|confirm|double.?check|closer|inspect|zoom|"
                                r"return|come\s+back|go\s+back|home)", text)):
            terminal = self.decide_in_code("terminal_condition", "OBSERVED")
        else:
            terminal = self.choose("terminal_condition", {
                label: label.replace("_", " ").lower() for label in _TERMINALS[kind]
            })
        if relation == "HOLDS_PHONE" and kind == GoalKind.FIND_OBSERVE and terminal not in (
            "RELATION_VERIFIED", "RETURN_HOME",
        ):
            raise _Rejected("RELATION_REQUIRES_VERIFICATION")
        self.goals.append(GoalObjective(
            objective_id, len(self.goals) + 1, kind, classes, qualifier, relation, scope, terminal,
            subject, target, dependencies,
        ))

    def decide_in_code(self, name: str, label: str) -> str:
        """A choice the request's structure already determines: recorded, never asked."""
        self.accepted_choices.append({
            "clause_id": self.clause.clause_id if self.clause else None,
            "name": name, "label": "CODE:" + label,
        })
        return label

    def select_classes(self) -> tuple[str, ...]:
        selected = self.choose("target_classes", {
            **{f"CLASS:{name}": f"Only {name}; aliases resolve to this supported class." for name in SUPPORTED_CLASSES},
            "CLASS_GROUP": "Select an explicit supported subset, one class at a time; devices has no default set.",
        })
        if selected != "CLASS_GROUP":
            return (selected.removeprefix("CLASS:"),)
        named = _named_classes(self.clause.text if self.clause else "")
        if len(named) >= 2:
            # The clause names two or more supported classes outright ("phones and
            # computers"): resolve them deterministically instead of one Jev call per
            # class, which the real model answered inconsistently (UNSUPPORTED at 0.41
            # for GROUP_DONE after including both, Mission Control Jev rehearsal).
            self.accepted_choices.append({
                "clause_id": self.clause.clause_id if self.clause else None,
                "name": "class_group", "label": "LEXICAL:" + ",".join(named),
            })
            return named
        remaining = list(SUPPORTED_CLASSES)
        included = []
        while True:
            selected = self.choose("class_group", {
                **{f"INCLUDE:{name}": f"Include {name} in the explicitly requested subset." for name in remaining},
                "GROUP_DONE": "The explicit supported subset is complete.",
            })
            if selected == "GROUP_DONE":
                if not included:
                    raise _Rejected("EMPTY_CLASS_GROUP")
                return tuple(included)
            name = selected.removeprefix("INCLUDE:")
            remaining.remove(name)
            included.append(name)

    def select_scope(self) -> str:
        scope = self.choose("spatial_scope", {
            "ROOM": "Whole room coverage.", "CURRENT_VIEW": "Objects visible from the current viewpoint.",
            "REGION": "A semantic viewpoint selected by all three axes; counts concern its visible objects.",
        })
        if scope != "REGION":
            return scope
        axes = []
        for axis, labels in (("x", ("LEFT", "CENTER", "RIGHT")),
                             ("y", ("REAR", "MIDDLE", "FRONT")),
                             ("z", ("BOTTOM", "MIDDLE", "TOP"))):
            axes.append(self.choose(f"scope_{axis}", {label: label.lower() for label in labels}))
        return "/".join(axes)

    def observation(self, objective_id, classes, qualifier, relation, scope, *, bind_new=False) -> GoalObjective:
        target = None
        role = "OBSERVED_" + "_".join(name.upper() for name in classes)
        relative_to = None
        if relation == "HOLDS_PHONE":
            if classes != ("person",):
                raise _Rejected("HOLDING_REQUIRES_PERSON_SUBJECT")
            if not bind_new:
                self.decide_in_code("entity_reference", "DESIGNATED_PERSON")
            role = "VERIFIED_DESIGNATED_PERSON"
            target = EntityRef(objective_id + ":phone", objective_id, "RELATED_PHONE", None)
        elif relation == "NEXT_TO_DRONE":
            if classes != ("phone",):
                raise _Rejected("NEXT_TO_DRONE_REQUIRES_PHONE")
            if not bind_new:
                self.decide_in_code("entity_reference", "CURRENT_DRONE")
            role, target, relative_to = "NEAREST_PHONE", self.drone, self.drone.ref_id
        else:
            if not bind_new:
                # One structurally determined answer: asking it scored the real
                # model's lowest confidence (NEW_ENTITY p 0.56-0.59, scalar 0.34).
                self.decide_in_code("entity_reference", "NEW_ENTITY")
        return GoalObjective(
            objective_id, len(self.goals) + 1, GoalKind.FIND_OBSERVE, classes, qualifier, relation, scope,
            "RELATION_VERIFIED" if relation == "HOLDS_PHONE" else "OBSERVED",
            EntityRef(objective_id + ":subject", objective_id, role, relative_to), target, (),
        )

    def bind_target(
        self, classes: tuple[str, ...], *, relative_drone: bool = False,
    ) -> tuple[EntityRef | None, GoalObjective | None]:
        assert self.clause is not None
        pronouns = _reference_pronouns(self.clause.text, relative_drone=relative_drone)
        all_references = {}
        for goal in self.goals:
            if goal.kind != GoalKind.FIND_OBSERVE:
                continue
            if goal.subject_ref is not None:
                if goal.subject_ref.entity_role == "VERIFIED_DESIGNATED_PERSON":
                    # Verification refines the same actor; its prior, unverified
                    # reference is not a second possible person antecedent.
                    all_references.pop(goal.subject_ref.relative_to, None)
                all_references[goal.subject_ref.ref_id] = (goal.subject_ref, goal, goal.target_classes)
            if goal.target_ref and goal.target_ref.entity_role == "RELATED_PHONE":
                all_references[goal.target_ref.ref_id] = (goal.target_ref, goal, ("phone",))
        if pronouns:
            # Grammatical compatibility is assessed before the model-selected
            # class. Choosing phone cannot erase a pen that is also a possible
            # antecedent for "it". Preserve distinct hypotheses of one class too.
            if len(set(pronouns)) != 1 or "you" in pronouns:
                raise _Rejected("AMBIGUOUS_ENTITY_REFERENCE")
            compatible = {
                key: value for key, value in all_references.items()
                if (pronouns[0] in ("him", "her") and value[2] == ("person",))
                or (pronouns[0] == "it" and "person" not in value[2])
                or pronouns[0] == "them"
            }
            if len(compatible) != 1:
                raise _Rejected("AMBIGUOUS_ENTITY_REFERENCE")
            if next(iter(compatible.values()))[2] != classes:
                raise _Rejected("PRONOUN_CLASS_MISMATCH")
            all_references = compatible
        references = {key: value[:2] for key, value in all_references.items() if value[2] == classes}
        options = {f"REF:{key}": f"Use exactly result {key}; never substitute another target." for key in references}
        if not pronouns:
            options["NEW_TARGET"] = "Create an explicit observation prerequisite for a new target hypothesis."
        selected = self.choose("entity_reference", options)
        return (None, None) if selected == "NEW_TARGET" else references[selected.removeprefix("REF:")]

    def persist(self, ledger: GoalLedger, reason: str | None) -> None:
        metadata = {
            "schema_version": SCHEMA_VERSION, "prompt_revision": self.compiler.prompt_revision,
            "input_limit_bytes": self.compiler.input_limit_bytes,
            "input_bytes": len(self.request.encode("utf-8")),
            "request_sha256": hashlib.sha256(self.request.encode("utf-8")).hexdigest(),
            "snapshot": asdict(self.snapshot),
        }
        events = []
        for index, attempt in enumerate(self.attempts):
            events.append(self.event("GOAL_CHOICE_ATTEMPT", {**metadata, **attempt}, index))
        final = {**metadata, "status": ledger.status, "reason": reason}
        accepted = ledger.status == GoalStatus.PENDING
        if accepted:
            final.update(ledger=asdict(ledger), goal_revision=self.snapshot.goal_revision + 1)
        events.append(self.event(
            "GOAL_LEDGER_ACCEPTED" if accepted else "GOAL_COMPILE_REJECTED", final, len(events),
        ))
        self.compiler.store.append_events(
            session_id=self.session_id, expected_version=self.snapshot.state_version, events=events,
        )

    def event(self, event_type: str, payload: dict, index: int) -> dict:
        return {
            "event_type": event_type, "payload": payload, "correlation_id": self.correlation_id,
            "causation_id": None, "idempotency_key": f"{self.correlation_id}:{index}",
        }


def _normalize(text: str) -> str:
    return _ALIASES.sub(
        lambda match: "person" if match.group().lower() in ("human", "humans", "people") else "phone",
        text,
    )


def _reference_pronouns(text: str, *, relative_drone: bool = False) -> tuple[str, ...]:
    """Normalize subject/object forms through one dispatch and binding path."""
    if relative_drone:
        text = re.sub(r"\bnext\s+to\s+you\b", "", text, flags=re.IGNORECASE)
    return tuple(_PRONOUN_FORMS[match.group().lower()] for match in _PRONOUNS.finditer(text))


def _propose_clauses(request: str) -> tuple[_Clause, ...]:
    parts = []
    for fragment in _CLAUSE_BOUNDARY.split(request):
        text = fragment.strip(" ,")
        if not text:
            continue
        # Return is a terminal condition in the public goal vocabulary.
        if parts and re.match(r"^(?:(?:and|then)\s+)?return\b", text, re.IGNORECASE):
            parts[-1] += "; " + text
        else:
            parts.append(text)
    return tuple(_Clause(f"c{index}", index, text) for index, text in enumerate(parts, 1))
