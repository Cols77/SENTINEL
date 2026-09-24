"""One version-checked, audited read-only room action per turn."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import re
import sys
import time
from typing import Callable, TYPE_CHECKING
import uuid

from sentinel.jev_choice import assess_choice
from sentinel.judgment import ChoiceJudge
from sentinel.cube.energy import ExecutionMode
from sentinel.cube.localization import LocalizationState
from sentinel.cube.supervised_wait import SupervisedWait
from sentinel.cube.step_executor import ExecutorState, TerminalOutcome
from sentinel.models import Position

from .candidates import (build_candidates, build_observation_candidates, count_answer_candidate, count_scope,
                         is_search_objective, search_answer_candidate, search_pose_ids, search_sightings)
from .coverage import CellObservation, CoverageProjection, project_coverage, render_coverage_note
from .decision_prompt import build_decision_instruction
from .grounding import CountGeometry, ground_frame, project_objective_count, render_objective_count
from .ingest import ingest_result
from .models import CandidateKind, GoalKind, GoalStatus, PhysicalCost, RoomCandidate, TerminalState
from .queries import READ_KINDS, ReadFact, ReadQueries, ReadSnapshot, ToolOutcome, relevant_hash, render_answer
from .semantic import resolve_pose_id, semantic_region_of
from .spatial import PoseEvidence, semantic_heading_to_compass_deg
from .store import RoomStore, VersionConflict
from .wiring import CapturedRequest

if TYPE_CHECKING:
    from sentinel.survey.store import SurveyStore

#: Fix round 3 (F1): a safe-return leg that comes back non-terminal (an
#: SDK-uncertain command mid-route, or a recovery episode that resolves
#: without landing) is retried this many times -- via a fresh
#: ``_request_safe_return`` each time, which recomputes the checkpoint from
#: the aircraft's current measured state -- before ``_finalize_terminal``
#: gives up and escalates through ``SafetySupervisor.refresh_failure`` (a
#: forced emergency landing that always yields a ``TerminalOutcome``).
_MAX_RETURN_ATTEMPTS = 5

#: Fix round 3 (F3): tolerances for crediting an OBSERVE checkpoint's
#: *measured* post-dispatch pose against the pose its candidate declared.
#: The declared-metadata check (``_checkpoint_targets_declared_pose``) can
#: pass even when the checkpoint never actually moved the aircraft; these
#: tolerances gate a second, independent check against the checkpoint's own
#: reported position (``CheckpointOutcome.pose``) and the tracker's current
#: heading before coverage is ever credited.
_POSE_POSITION_TOLERANCE_CM = 5.0
_POSE_HEADING_TOLERANCE_DEG = 5.0
#: A real checkpoint arrival counts as reaching its declared pose within these.
_ARRIVAL_XY_TOLERANCE_CM = 30.0
_ARRIVAL_Z_TOLERANCE_CM = 40.0
_ARRIVAL_HEADING_TOLERANCE_DEG = 10.0

#: Task 4b item 4: reply-shape refusals re-asked once, like a provider error.
_MALFORMED_OUTCOMES = ("MALFORMED_ANSWER", "ILLEGAL_OR_MALFORMED", "MISSING_OR_NON_MAX_SELECTION")


def _heading_within_tolerance(a_deg: float, b_deg: float, tolerance_deg: float = _POSE_HEADING_TOLERANCE_DEG) -> bool:
    """Fix round 3 (F6): compare headings modularly, never by exact equality
    -- a planner or tracker that reports headings normalized to [0, 360)
    must never be refused merely because ``resolve_semantic_pose`` normalizes
    its own yaw to (-180, 180]."""
    delta = (a_deg - b_deg + 180.0) % 360.0 - 180.0
    return abs(delta) <= tolerance_deg


#: The one capability under which a REAL-mode session may fly OBSERVE
#: checkpoints (count-and-return and its autonomous search).
OBSERVATION_CAPABILITY = "count_return"


def real_observation_admitted(supervisor) -> bool:
    """Whether a REAL-mode supervisor's validated binding authorizes OBSERVE.

    True only when the supervisor flies ``count_return`` and its last binding
    validation admitted a matching ``count_return`` grant that is PROMOTED,
    or TRIAL inside an ACCEPTANCE-kind manifest. Every other case -- no
    validation yet, a failed one, another capability, closer inspection --
    keeps observation disabled.
    """
    grant = getattr(supervisor, "admitted_grant", None)
    if grant is None or getattr(supervisor, "capability", None) != OBSERVATION_CAPABILITY:
        return False
    if grant.capability != OBSERVATION_CAPABILITY:
        return False
    if grant.state == "PROMOTED":
        return True
    manifest = getattr(supervisor, "acceptance_manifest", None)
    return (grant.state == "TRIAL" and manifest is not None
            and getattr(manifest, "session_kind", None) == "ACCEPTANCE")


@dataclass(frozen=True)
class AcceptancePolicy:
    """Task V2: per-kind acceptance thresholds for Jev's top legal choice.

    The room loop's candidates are bounded: an OBSERVE is one supervised
    checkpoint, a REANCHOR is the supervisor's own stationary recovery, a
    read changes nothing, and physical safety never rests on Jev (every
    movement is admitted or refused by the SafetySupervisor; mandatory
    recovery and return bypass Jev). Those kinds accept at a lower
    confidence; a final ANSWER slightly higher; anything else -- CLARIFY and
    ESCALATE end the session, and any other kind -- keeps the strict bar.
    A choice below its bar is re-asked once with the refusal as feedback
    (``reask_once``) before the turn counts as refused; never more.
    """

    strict_act_at: float = .8
    observe_act_at: float = .5
    reanchor_act_at: float = .5
    read_act_at: float = .5
    answer_act_at: float = .6
    min_margin: float = .15
    reask_once: bool = True

    def __post_init__(self):
        for name in ("strict_act_at", "observe_act_at", "reanchor_act_at", "read_act_at", "answer_act_at",
                     "min_margin"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"acceptance threshold {name} must be a finite probability")
        if type(self.reask_once) is not bool:
            raise ValueError("reask_once must be a boolean")

    @classmethod
    def strict(cls, act_at: float = .8, min_margin: float = .15) -> "AcceptancePolicy":
        """One bar for every kind, no re-ask (the controller's original policy)."""
        return cls(act_at, act_at, act_at, act_at, act_at, min_margin, False)

    def act_at_for(self, kind) -> float:
        if kind == CandidateKind.OBSERVE:
            return self.observe_act_at
        if kind == CandidateKind.REANCHOR:
            return self.reanchor_act_at
        if kind in READ_KINDS:
            return self.read_act_at
        if kind == CandidateKind.ANSWER:
            return self.answer_act_at
        return self.strict_act_at

    def as_config(self) -> dict:
        return asdict(self)


def observe_kind_gate(assessment, candidates, act_at: float, min_margin: float):
    """Accept Jev's top OBSERVE when observing, as a kind, is the confident choice.

    Equivalent regions split Jev's probability (live 2026-09-24: center 0.44,
    right 0.21, left 0.20 -- 0.85 on observing), so the per-label gate refused
    a decision Jev was confident in. The kind's summed probability must clear
    the observe bar and lead every other kind's sum by the margin; Jev's own
    top label (always the argmax, see ``assess_choice``) is the one flown.
    """
    if assessment.outcome not in ("LOW_CONFIDENCE", "THIN_MARGIN"):
        return assessment
    chosen = candidates.get(assessment.selected_label)
    if chosen is None or chosen.kind != CandidateKind.OBSERVE:
        return assessment
    mass: dict = {}
    for label, probability in assessment.probabilities:
        candidate = candidates.get(label)
        kind = candidate.kind if candidate is not None else label
        mass[kind] = mass.get(kind, 0.0) + probability
    observe = mass.pop(CandidateKind.OBSERVE, 0.0)
    if observe < act_at or observe - max(mass.values(), default=0.0) < min_margin:
        return assessment
    return replace(assessment, final_label=assessment.selected_label, outcome="ACCEPTED",
                   confidence=observe, confidence_source="KIND_MASS")


@dataclass(frozen=True)
class TurnOutcome:
    candidate_id: str
    accepted: bool
    event_ids: tuple[str, ...]
    terminal_state: TerminalState | None
    terminal_source: str | None = None
    terminal_reason: str | None = None
    persistence_error: str | None = None



def _failure(code: str, detail: str) -> dict:
    """A failed OBSERVE payload with a plain reason Jev reads next turn."""
    return {"code": code, "refusal": code, "detail": detail}


def _arrival_miss(resolved, pose, health) -> str:
    if pose is None:
        return "the drone's position after the move is unknown"
    off_cm = math.hypot(pose.x_cm - resolved.x_cm, pose.y_cm - resolved.y_cm)
    turn = abs((health.heading_deg - resolved.yaw_deg + 180) % 360 - 180)
    return (f"the drone arrived {off_cm:.0f} cm (height {abs(pose.z_cm - resolved.z_cm):.0f} cm, "
            f"heading {turn:.0f} deg) away from the pose, too far to credit coverage")

class RoomController:
    def __init__(self, store: RoomStore, judge: ChoiceJudge, *,
                 survey_store: SurveyStore | None = None, survey_run_id: str | None = None,
                 queries: ReadQueries | None = None, act_at: float = .8, min_margin: float = .15,
                 candidate_builder: Callable[[ReadSnapshot], tuple[RoomCandidate, ...]] | None = None,
                 executor: Callable[[RoomCandidate, ReadSnapshot], tuple[ReadFact, ...]] | None = None,
                 ingest_pending: Callable[[str], object] | None = None,
                 supervisor=None, flight_executor=None, provider_wait_s: float = 10.0,
                 resolve_checkpoint=None, resolve_sweep_checkpoints=None,
                 perception_capture=None, perception_provider=None,
                 count_geometry: CountGeometry | None = None, acceptance: AcceptancePolicy | None = None):
        for value in (act_at, min_margin):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("choice thresholds must be finite probabilities")
        if acceptance is not None and not isinstance(acceptance, AcceptancePolicy):
            raise ValueError("acceptance must be an AcceptancePolicy")
        #: Task V2: without an explicit policy every kind keeps ``act_at``
        #: and nothing is re-asked (the original behaviour).
        self.acceptance = acceptance if acceptance is not None else AcceptancePolicy.strict(act_at, min_margin)
        if acceptance is not None:
            act_at, min_margin = acceptance.strict_act_at, acceptance.min_margin
        self.store, self.judge = store, judge
        query_clock = ({"clock": supervisor.clock, "clock_domain_id": supervisor.clock_domain_id}
                       if supervisor is not None else {})
        self.queries = queries if queries is not None else ReadQueries(
            store, survey_store=survey_store, survey_run_id=survey_run_id, **query_clock)
        self.act_at, self.min_margin = act_at, min_margin
        self.executor = executor
        self.ingest_pending = ingest_pending
        if (supervisor is None) != (flight_executor is None):
            raise ValueError("supervisor and flight executor must be supplied together")
        if type(provider_wait_s) not in (int, float) or not math.isfinite(provider_wait_s) or provider_wait_s <= 0:
            raise ValueError("provider wait must be finite and positive")
        self.supervisor, self.flight_executor, self.provider_wait_s = supervisor, flight_executor, provider_wait_s
        # Observation execution (checkpoint + correlated perception round trip) is
        # a separate opt-in runtime boundary from the base supervisor/executor
        # pairing above: either all three observation collaborators are supplied
        # or none are, and OBSERVE candidates are only ever offered when they are.
        # This is Plan 03 Task 4's "capability" -- there is no distinct
        # CapabilityGrant string for it (see task report); it rides on the same
        # SafetySupervisor mode/grant machinery every other checkpoint already uses.
        observation_collaborators = (resolve_checkpoint, perception_capture, perception_provider)
        if any(c is not None for c in observation_collaborators) and not all(c is not None for c in observation_collaborators):
            raise ValueError("resolve_checkpoint, perception_capture and perception_provider "
                             "must be supplied together")
        self.resolve_checkpoint, self.perception_capture = resolve_checkpoint, perception_capture
        self.resolve_sweep_checkpoints = tuple(resolve_sweep_checkpoints or ())
        if self.resolve_sweep_checkpoints and resolve_checkpoint is None:
            raise ValueError("sweep checkpoints require resolve_checkpoint")
        self.perception_provider = perception_provider
        # Residual F2: the camera model and per-class support declarations
        # grounded counting places static detections with. Optional: without
        # it every static detection is persisted -- and answered -- as
        # unplaced, never guessed into a position.
        if count_geometry is not None and not isinstance(count_geometry, CountGeometry):
            raise ValueError("count_geometry must be a CountGeometry")
        self.count_geometry = count_geometry
        self.candidate_builder = candidate_builder if candidate_builder is not None else self._legal_candidates

    @property
    def _observation_enabled(self) -> bool:
        # Plan 03 Task 4 enabled OBSERVE for replay/simulation only (fix
        # round 1, Q8). Task B (room-live-adapters, item 2): REAL mode enables
        # it only for a session admitted under the count_return capability by
        # a matching PROMOTED grant, or a TRIAL grant inside its acceptance
        # run -- and only once the supervisor's own binding validation
        # actually admitted that grant (``admitted_grant``). A REAL-mode
        # session must never fly an OBSERVE checkpoint just because some
        # other grant (e.g. ADAPTIVE_FLIGHT) and the observation
        # collaborators happen to be present.
        if not (self.supervisor is not None and self.flight_executor is not None
                and self.resolve_checkpoint is not None and self.perception_capture is not None
                and self.perception_provider is not None):
            return False
        if self.supervisor.mode == ExecutionMode.SIMULATION:
            return True
        if self.supervisor.mode == ExecutionMode.REAL:
            return real_observation_admitted(self.supervisor)
        return False

    def _observation_objectives(self, snapshot):
        if snapshot.ledger is None or snapshot.ledger.status != GoalStatus.PENDING:
            return ()
        return snapshot.ledger.objectives

    def _coverage(self, snapshot: ReadSnapshot, *, pose_id: str | None = None) -> CoverageProjection:
        """Rebuild the coverage projection purely from replayed OBSERVE outcomes.

        With ``pose_id`` (residual F2, a region-scope count), only the cells
        that pose declares visible, and only observations taken from it, are
        projected: a viewpoint answer's coverage caveat describes that
        viewpoint, never the whole room.

        Residual F2: *counts* are now grounded per detection (see
        ``_count_projections``); *coverage* keeps the "declared view visited"
        semantic described below -- a cell is resolved by an authorized
        observation from a pose that declares it visible, not by polygon
        containment of individual detections.

        A pose declares visibility of a set of coverage cells
        (``RoomSite.pose_visibility``); once an OBSERVE checkpoint at that pose
        has completed and its perception result was ingested, every
        (cell, class) pair that pose declares is resolved for that flight --
        this session never infers *which* class a detection belongs to a
        specific cell (that per-detection geometric grounding is future work;
        see the task report). Fix round 1 (finding S2) ratifies this as the
        Task 4/Plan 03 completion semantic explicitly: "region visited =
        resolved", not per-detection geometric grounding. The per-detection
        (x, y) estimates grounded counting now persists are *not* tested for
        containment in ``CoverageCell.polygon_xy_cm``; any reader of
        ``coverage.complete`` (including Task 5's replay baseline) must treat
        it as this weaker "region visited" guarantee.
        ``observed_at`` uses the perception result's own measured capture
        time (fix round 1, Q12) when the outcome carries one; only the
        durable ROOM_TOOL_OUTCOME event ID is used as a fallback for outcomes
        recorded before this fix. Fix round 2 (Q5): this method still trusts
        the ``pose_id`` parsed out of a replayed ``OBSERVE_SUCCEEDED``
        outcome's ``candidate_id`` -- but ``_execute_observation`` now only
        ever records ``OBSERVE_SUCCEEDED`` once it has verified, before
        dispatch, that the resolved checkpoint plan's own endpoint/heading
        match that exact declared pose (``_checkpoint_targets_declared_pose``)
        and, after dispatch, that the outcome's own per-command record shows
        that plan's CAPTURE actually completed (``_capture_completed``) --
        never a mid-plan recovery/re-dispatch outcome that only looks
        successful at the top level. So the pose_id this method parses back
        out of a persisted outcome is no longer merely asserted by the
        candidate that was chosen; it is the pose that dispatch itself
        confirmed the checkpoint reached.
        """
        site = self.supervisor.site
        cells = site.coverage_cells
        if pose_id is not None:
            visible = set(dict(site.pose_visibility).get(pose_id, ()))
            cells = tuple(cell for cell in cells if cell.cell_id in visible)
        cells_by_id = {cell.cell_id: cell for cell in cells}
        declared_views_by_cell: dict[str, list[str]] = {}
        for declared_pose_id, cell_ids in site.pose_visibility:
            for cell_id in cell_ids:
                declared_views_by_cell.setdefault(cell_id, []).append(declared_pose_id)
        observations = []
        for outcome in snapshot.outcomes:
            if outcome.kind != CandidateKind.OBSERVE.value or outcome.code != "OBSERVE_SUCCEEDED":
                continue
            frames = outcome.count_evidences or ((outcome.count_evidence,) if outcome.count_evidence else ())
            if frames and any(frame.pose.stale for frame in frames):
                # Fix round 4 (Q4): a frame grounded from a pose already
                # known stale at capture time placed nothing (spatial.py
                # invalidates every estimate outright for it) -- crediting
                # its declared cells would let the room report "complete"
                # while none of that frame's detections were ever counted.
                # An outcome persisted before this fix carries no
                # count_evidence at all and is still credited, unchanged.
                continue
            observed_pose_id = outcome.candidate_id.rsplit(":", 1)[-1]
            if pose_id is not None and observed_pose_id != pose_id:
                continue
            observed_times = tuple(frame.captured_at for frame in frames) or (outcome.observed_at or outcome.event_id,)
            for observed_at in observed_times:
                for cell_id in dict(site.pose_visibility).get(observed_pose_id, ()):
                    cell = cells_by_id.get(cell_id)
                    if cell is None:
                        continue
                    for class_name in cell.required_classes:
                        observations.append(CellObservation(cell_id, class_name, observed_pose_id, "OBSERVED",
                                                             observed_at, evidence_refs=(outcome.event_id,)))
        return project_coverage(cells, tuple(observations),
                                declared_views_by_cell={k: tuple(v) for k, v in declared_views_by_cell.items()})

    def _count_objectives(self, snapshot: ReadSnapshot):
        return tuple(sorted((o for o in self._observation_objectives(snapshot) if o.kind == GoalKind.COUNT),
                            key=lambda o: o.ordinal))

    def _count_projections(self, snapshot: ReadSnapshot):
        """Residual F2: the session COUNT fact -- one grounded
        ``ObjectiveCount`` per pending COUNT objective, projected purely from
        the count evidence every ``OBSERVE_SUCCEEDED`` outcome persisted.

        A room-wide objective counts across every observed frame against the
        room's coverage; a region objective counts only the frames taken
        from its requested region's declared pose, against that pose's own
        coverage cells. A scope with no count route contributes nothing."""
        frames = tuple(frame for o in snapshot.outcomes
                       if o.kind == CandidateKind.OBSERVE.value and o.code == "OBSERVE_SUCCEEDED"
                       for frame in (o.count_evidences or ((o.count_evidence,) if o.count_evidence else ())))
        projections = []
        for objective in self._count_objectives(snapshot):
            scope, pose_id = count_scope(objective)
            if scope == "ROOM":
                projections.append(project_objective_count(objective, frames, self._coverage(snapshot),
                                                           scope="ROOM"))
            elif scope == "VIEWPOINT":
                projections.append(project_objective_count(
                    objective, tuple(f for f in frames if f.pose_id == pose_id),
                    self._coverage(snapshot, pose_id=pose_id), scope="VIEWPOINT", pose_id=pose_id))
        return tuple(projections)

    def _closer_unavailable_candidates(self, snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
        """Plan 03 Task 3 (closer-inspection viewpoints) is deferred: any goal
        that can only be satisfied by getting closer -- a FIND_OBSERVE
        objective whose terminal condition requires validation -- can never be
        offered an executable closer candidate. Rather than let Jev loop
        between CLARIFY/ESCALATE, this reports that limitation explicitly.

        Fix round 1 (Q11): this limitation is a per-objective fact, not a
        whole-session stop -- once reported for an objective it is suppressed
        (mirroring ``_observe_suppressed``/``_answer_suppressed`` in
        candidates.py) so a mixed ledger's other pending objectives (an
        OBSERVE count, a plain answer, ...) keep being offered afterward.
        """
        candidates = []
        for objective in self._observation_objectives(snapshot):
            if objective.kind != GoalKind.FIND_OBSERVE or objective.terminal_condition != "VALIDATED":
                continue
            candidate_id = f"VALIDATE:{objective.objective_id}:UNAVAILABLE"
            digest = relevant_hash(CandidateKind.ESCALATE, snapshot)
            if any(o.candidate_id == candidate_id and o.relevant_state_sha256 == digest
                  and o.code == "CLOSER_INSPECTION_UNAVAILABLE" for o in snapshot.outcomes):
                continue
            description = (f"Closer inspection is not available in this session for objective "
                          f"{objective.objective_id}; only surveyed viewpoints and existing evidence "
                          f"can be reported.")
            candidates.append(RoomCandidate(candidate_id, CandidateKind.VALIDATE_DETECTION,
                                            snapshot.state_version, digest, description, description))
        return tuple(candidates)

    def _observation_candidates(self, snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
        if not self._observation_enabled:
            return ()
        return build_observation_candidates(self._observation_objectives(snapshot), self.supervisor.site,
                                            self._coverage(snapshot), snapshot.outcomes, snapshot.state_version)

    def _legacy_count_answer_blocked(self, snapshot: ReadSnapshot) -> bool:
        """Fix round 1 (Q2) / residual F2: once this session can observe, a
        pending COUNT objective is never answered through the plain legacy
        ANSWER candidate (``can_answer`` only checks that *some* READ_COUNTS
        survey report exists -- a report unrelated to this session's
        observations that states full-room coverage is unknown). Its only
        terminal routes are the grounded ``ANSWER:COUNTS`` candidate and the
        explicit ``ANSWER:<objective>:INCOMPLETE`` candidate."""
        # Task B item 2b: a search objective likewise answers only from this
        # session's own observations (ANSWER:SEARCH), never from historical
        # READ_OBSERVATIONS evidence that happens to name the class.
        return self._observation_enabled and (bool(self._count_objectives(snapshot))
                                              or bool(self._search_objectives(snapshot)))

    def _search_objectives(self, snapshot: ReadSnapshot):
        return tuple(sorted((o for o in self._observation_objectives(snapshot) if is_search_objective(o)),
                            key=lambda o: o.ordinal))

    def _count_answer_candidates(self, snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
        if not self._observation_enabled:
            return ()
        coverage = self._coverage(snapshot)
        candidates = (count_answer_candidate(snapshot, coverage),
                      search_answer_candidate(snapshot, self.supervisor.site, coverage))
        return tuple(candidate for candidate in candidates if candidate is not None)

    def _detection_bands(self, snapshot: ReadSnapshot) -> dict[str, str]:
        """``<observation_id>/<detection_id>`` -> the detector's confidence band,
        from the session's persisted observation evidence."""
        bands = {}
        for fact in snapshot.sources:
            if fact.kind != CandidateKind.READ_OBSERVATIONS or len(fact.evidence_ids) < 3:
                continue
            observation_id, _, detection_id = fact.evidence_ids[:3]
            bands[f"{observation_id}/{detection_id}"] = dict(fact.values).get("confidence_band")
        return bands

    def _search_sections(self, snapshot: ReadSnapshot) -> tuple[list[str], bool]:
        """Task B item 2b: one answer section per search objective, and
        whether every search was conclusive (found, or every declared region
        in scope actually observed)."""
        site = self.supervisor.site
        coverage = self._coverage(snapshot)
        bands = self._detection_bands(snapshot)
        observed = {o.count_evidence.pose_id for o in snapshot.outcomes
                    if o.code == "OBSERVE_SUCCEEDED" and o.count_evidence is not None}
        sections, conclusive = [], True
        for objective in self._search_objectives(snapshot):
            classes = ", ".join(objective.target_classes)
            header = f"Objective {objective.objective_id} (search for {classes}):"
            sightings = search_sightings(objective, site, snapshot.outcomes)
            if not sightings:
                in_scope = search_pose_ids(objective, site)
                seen = [pose_id for pose_id in in_scope if pose_id in observed]
                missing = [pose_id for pose_id in in_scope if pose_id not in observed]
                conclusive = conclusive and not missing
                lines = [header, f"{classes}: not found in observed regions "
                                 f"({', '.join(seen) or 'none observed'})."]
                if missing:
                    lines.append(f"Regions that could not be observed: {', '.join(missing)}.")
                lines.append(render_coverage_note(coverage, scope="ROOM"))
                sections.append("\n".join(lines))
                continue
            lines = [header]
            for outcome, item in sightings:
                pose_id = outcome.count_evidence.pose_id
                band = bands.get(item.source_detection_id if hasattr(item, "source_detection_id")
                                 else item.detection_id)
                confidence = ("high confidence" if band == "high"
                              else f"uncertain ({band or 'unknown'} confidence)")
                viewpoint = "-".join(pose_id.split("-")[:3])
                if hasattr(item, "x_cm"):
                    horizontal, depth, inside = semantic_region_of(site, item.x_cm, item.y_cm)
                    where = f"{horizontal}-{depth} part of the room" + ("" if inside else ", beyond the flyable area")
                    placement = (f"placed on the {item.support_plane_id} support plane in the {where} "
                                 f"(about x={round(item.x_cm)} cm, y={round(item.y_cm)} cm; "
                                 f"{item.pose_provenance.lower()} camera pose)")
                else:
                    placement = f"seen but not placed ({item.reason})"
                lines.append(f"{item.class_name}: seen from the {viewpoint} region "
                             f"(declared pose {pose_id}); {placement}; {confidence}. "
                             f"[observation={outcome.count_evidence.observation_id}; event={outcome.event_id}]")
            sections.append("\n".join(lines))
        return sections, conclusive

    # -- Task B item 2c: localization health Jev can see, and REANCHOR ----------

    def localization_health(self) -> dict | None:
        """The Jev-facing localization summary for this turn, or None without
        a supervisor. State (ANCHORED/COASTING/...), the age of the last
        accepted tag fix, the current position uncertainty and the margin
        left before the calibrated RECOVERY_REQUIRED threshold -- plain
        numbers only, never coordinates."""
        if self.supervisor is None:
            return None
        tracker = self.supervisor.tracker
        health, profile = tracker.health, tracker.profile
        margins = []
        if profile is not None:
            for field in ("camera_age_s", "fix_age_s", "estimator_failures", "look_away_s", "translation_cm",
                          "yaw_deg", "xy_uncertainty_cm", "heading_uncertainty_deg"):
                limit = getattr(profile, "max_" + field)
                if limit:
                    margins.append(max(0.0, 1.0 - getattr(health, field) / limit))
        return {
            "state": health.state.value,
            "last_tag_fix_age_s": round(max(0.0, health.fix_age_s), 1),
            "position_uncertainty_cm": round(max(0.0, health.xy_uncertainty_cm), 1),
            "margin_before_mandatory_recovery_pct": round(100 * min(margins)) if margins else None,
            "recent_positioning_events": list(getattr(self.supervisor, "positioning_log", ()))[-5:],
        }

    def _reanchor_candidates(self, snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
        """A Jev-selectable bounded re-anchor, offered while COASTING (or while
        ANCHORED on a fix older than half the calibrated limit), only when a
        supervisor recovery adapter exists and the aircraft is airborne. It
        never replaces the safety floor: RECOVERY_REQUIRED is still recovered
        by the supervisor before Jev is asked, and LOST still lands."""
        if (self.supervisor is None or self.flight_executor is None
                or getattr(self.supervisor, "recovery", None) is None
                or self.flight_executor.state != ExecutorState.AIRBORNE):
            return ()
        tracker = self.supervisor.tracker
        health, profile = tracker.health, tracker.profile
        aged = profile is not None and health.fix_age_s >= profile.max_fix_age_s / 2
        if not (health.state == LocalizationState.COASTING
                or (health.state == LocalizationState.ANCHORED and aged)):
            return ()
        digest = hashlib.sha256(json.dumps({"candidate_id": "REANCHOR", "pose_revision": health.pose_revision,
                                            "state": health.state.value}, sort_keys=True).encode()).hexdigest()
        if any(o.candidate_id == "REANCHOR" and o.relevant_state_sha256 == digest and o.refusal_code is not None
               for o in snapshot.outcomes):
            return ()
        summary = self.localization_health()
        description = (f"Re-anchor now: hover and re-acquire the mapped tags (bounded, supervisor-admitted). "
                       f"Localization is {summary['state']}; last tag fix {summary['last_tag_fix_age_s']} s ago; "
                       f"{summary['margin_before_mandatory_recovery_pct']}% margin left before a mandatory "
                       f"recovery.")
        return (RoomCandidate("REANCHOR", CandidateKind.REANCHOR, snapshot.state_version, digest, description,
                              description, (), (), PhysicalCost(requires_airborne=True)),)

    def _execute_reanchor(self, session_id, expected_version):
        """Run the voluntary re-anchor; a dict payload, or a TurnOutcome when
        a mandatory transition preempted it (reported like any preemption)."""
        try:
            code, result = self.supervisor.voluntary_reanchor(self.flight_executor, snapshot_version=expected_version)
        except VersionConflict:
            return {"code": "STALE_DECISION", "refusal": "STALE_DECISION"}
        if code == "PREEMPTED":
            terminal = result if isinstance(result, TerminalOutcome) else None
            return self._preempted(session_id, self.supervisor.last_preemption, terminal_outcome=terminal)
        if code == "REANCHORED":
            return {"code": "REANCHORED"}
        return {"code": code, "refusal": code}

    def _legal_candidates(self, snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
        observations = self._observation_candidates(snapshot)
        observation_legal = any(c.kind == CandidateKind.OBSERVE for c in observations)
        base = build_candidates(snapshot, observation_legal=observation_legal)
        if observation_legal:
            # The live call logs showed READ options diluted the physical
            # mission decision even after their facts were already current.
            # Re-offer them as soon as there is no admissible OBSERVE.
            base = tuple(c for c in base if c.kind not in READ_KINDS)
        if self._legacy_count_answer_blocked(snapshot):
            base = tuple(c for c in base if c.kind != CandidateKind.ANSWER)
        return (*base, *observations, *self._count_answer_candidates(snapshot),
               *self._closer_unavailable_candidates(snapshot), *self._reanchor_candidates(snapshot))

    def _local_terminal(self, terminal, candidate_id):
        if not isinstance(terminal, TerminalOutcome):
            return None
        confirmed_landing = (terminal.state == "LANDED" and type(terminal.completed_at) in (int, float)
                             and math.isfinite(terminal.completed_at) and terminal.completed_at >= 0)
        state = TerminalState.SAFE_LANDED if confirmed_landing else TerminalState.ABORTED
        reason = (terminal.reason if isinstance(terminal.reason, str)
                  and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", terminal.reason) else "TERMINAL_EXECUTION_STOPPED")
        # Database exception messages can contain paths or credentials. Publish
        # a stable category and label this result as local, with no claimed IDs.
        error = "AUDIT_PERSISTENCE_FAILED" if self.supervisor.last_persistence_error else None
        return TurnOutcome(candidate_id, False, (), state, "LOCAL_SUPERVISOR", reason, error)

    def _preempted(self, session_id, decision, terminal_outcome=None):
        candidate_id = decision.transition if decision else "LAND_CONTINGENCY"
        terminal = (terminal_outcome if isinstance(terminal_outcome, TerminalOutcome)
                    else self.supervisor.last_terminal_outcome)
        local = self._local_terminal(terminal, candidate_id)
        if local is not None and self.supervisor.last_persistence_error:
            return local
        try:
            snapshot = self.queries.project(session_id)
            events = self.store.events(session_id)
        except Exception:
            if local is not None:
                return local
            raise
        if snapshot.terminal_state is None and local is not None:
            return local
        version = decision.request.snapshot_version if decision else self.supervisor.last_preempted_version
        return TurnOutcome(candidate_id, False,
                           tuple(e.event_id for e in events if e.state_version > version),
                           snapshot.terminal_state)

    def wait_for_perception(self, operation, *, session_id, timeout_s=None):
        """Bound a data-returning detector operation; its caller owns ingestion."""
        timeout_s = self.provider_wait_s if timeout_s is None else timeout_s
        if self.supervisor is not None:
            return self.supervisor.run_wait(operation, snapshot_version=self.store.session_version(session_id),
                                           timeout_s=timeout_s, executor=self.flight_executor)
        return SupervisedWait().run(operation, time.monotonic() + timeout_s, lambda: None)

    def _append(self, session_id, version, correlation, entries, causation=None):
        return self.store.append_events(session_id=session_id, expected_version=version, events=tuple(
            {"event_type": kind, "payload": data, "correlation_id": correlation,
             "causation_id": causation, "idempotency_key": f"{correlation}:{index}:{kind}"}
            for index, (kind, data) in enumerate(entries)
        ))

    def _outcome(self, snapshot, candidate_id, kind, digest, code, *, facts=(), terminal=None, refusal=None,
                answer=None, observed_at=None, count_evidence=None, count_evidences=None, detail=None):
        # Every refusal path, including stale decisions before dispatch, shares
        # the persisted progress rule. Audit-only version changes are not new
        # relevant evidence and cannot create an unbounded invalid-choice loop.
        if refusal is not None and any(o.candidate_id == candidate_id and o.relevant_state_sha256 == digest
                                       and o.refusal_code == refusal for o in snapshot.outcomes):
            code = "NO_PROGRESS"
            terminal = TerminalState.PROVIDER_FAILURE if refusal == "PROVIDER_ERROR" else TerminalState.ESCALATED
        result = {"candidate_id": candidate_id, "kind": kind, "snapshot_version": snapshot.state_version,
                  "relevant_state_sha256": digest, "code": code, "refusal_code": refusal,
                  "facts": [asdict(f) for f in facts], "terminal_state": terminal}
        if answer is not None:
            result["answer"] = answer
        if observed_at is not None:
            result["observed_at"] = observed_at
        if count_evidence is not None:
            result["count_evidence"] = count_evidence
        if count_evidences is not None:
            result["count_evidences"] = count_evidences
        if detail is not None:
            result["detail"] = detail
        return result

    def _has_flown(self, session_id: str) -> bool:
        """Fix round 1 (Q1) / fix round 3 (F5): whether this session's
        aircraft is currently airborne (or mid-return), or has ever actually
        dispatched a physical checkpoint. ``CHECKPOINT_COMPLETED`` is written
        only by ``SafetySupervisor._persist_completion``, i.e. only after a
        real command was sent to the executor -- never by a fixture's
        manually appended legacy ``DRONE_STATE_RECORDED`` telemetry read, and
        never by an OBSERVE turn that failed before dispatch (an unresolved
        checkpoint or a refused admission). ``CHECKPOINT_COMPLETED`` alone
        missed a session that took off (``supervisor.start``, which persists
        no room event) and then answered before ever dispatching a
        checkpoint: the measured executor state is checked first so that gap
        can never leave an airborne aircraft unreturned."""
        if self.flight_executor is not None and self.flight_executor.state in (
                ExecutorState.AIRBORNE, ExecutorState.RETURNING):
            return True
        return any(e.event_type == "CHECKPOINT_COMPLETED" for e in self.store.events(session_id))

    def _request_safe_return(self, session_id, snapshot_version):
        """Fix round 1 (Q1): force a voluntary return-and-land through the
        supervisor's own termination-plan machinery, reusing exactly the
        mechanism a mandatory battery/localization return already uses
        (``SafetySupervisor.assess``'s ``RETURN_NOW``/``LAND_CONTINGENCY``
        transitions dispatched through ``dispatch``). A safety-mandatory
        transition already computed by a fresh ``prepare()`` takes precedence
        over the voluntary request; otherwise a nominal ``CONTINUE`` outcome
        is forced to ``RETURN_NOW`` so the full route home plus landing runs.
        """
        try:
            base = self.supervisor.prepare(snapshot_version=snapshot_version, provider_wait_s=0)
        except Exception:
            return self.supervisor.refresh_failure(self.flight_executor)
        forced = base if base.transition != "CONTINUE" else replace(
            base, transition="RETURN_NOW", reason="MISSION_TERMINAL", invalidates_snapshot=True)
        try:
            return self.supervisor.dispatch(forced, self.flight_executor)
        except Exception:
            return self.supervisor.refresh_failure(self.flight_executor)

    def _finalize_terminal(self, session_id, correlation, snapshot, prior, candidate_id, kind, digest, *,
                           code, requested_terminal, answer=None, facts=(), extra=None):
        """Fix round 1 (Q1, Q3, Q11) / fix round 3 (F1): persist a
        Jev-selected (or system-forced) terminal decision. When this session
        has actually flown, a supervisor-owned safe return/landing is
        requested first -- mission success is reported only after a
        confirmed touchdown; an unconfirmed or failed landing downgrades the
        reported terminal state to ABORTED, exactly like every other
        physical failure this controller reports.

        A single ``_request_safe_return`` call can come back with a
        non-terminal result -- an SDK-uncertain command mid-route, or a
        RECOVER transition whose own recovery episode resolves without
        landing (``SafetySupervisor._recover``'s non-terminal
        ``CheckpointOutcome``) -- meaning the aircraft is still provably
        airborne or returning. Persisting a terminal ROOM_TOOL_OUTCOME in
        that case would end the session (``ReadQueries.project`` then
        reports it terminal on every later ``step()``) while nothing ever
        flies it home. So this retries a fresh ``_request_safe_return`` --
        which recomputes the checkpoint from the aircraft's current measured
        state each time -- up to ``_MAX_RETURN_ATTEMPTS`` times, and only
        ever persists a terminal outcome once that attempt actually reaches
        a ``TerminalOutcome``. If the bound is exhausted without ever
        reaching one, this escalates through
        ``SafetySupervisor.refresh_failure`` -- a forced emergency
        landing/abort that always returns a ``TerminalOutcome`` -- so a
        terminal answer is still never persisted for a genuinely open-ended
        return.

        The final commit always re-reads the live session version (``_record``)
        rather than the turn's original CAS version, because the voluntary
        return itself durably advances that version.
        """
        try:
            final_terminal = requested_terminal
            if self.supervisor is not None and self.flight_executor is not None and self._has_flown(session_id):
                result = None
                for _ in range(_MAX_RETURN_ATTEMPTS):
                    result = self._request_safe_return(session_id, self.store.session_version(session_id))
                    if isinstance(result, TerminalOutcome):
                        break
                if not isinstance(result, TerminalOutcome):
                    result = self.supervisor.refresh_failure(self.flight_executor)
                landed = (isinstance(result, TerminalOutcome) and result.state == "LANDED"
                         and type(result.completed_at) in (int, float) and math.isfinite(result.completed_at)
                         and result.completed_at >= 0)
                final_terminal = requested_terminal if landed else TerminalState.ABORTED
            payload = self._outcome(snapshot, candidate_id, kind, digest, code, facts=facts,
                                    terminal=final_terminal, answer=answer)
            payload.update(extra or {})
            return self._record(snapshot, correlation, payload, prior=prior)
        except Exception:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "TOOL_FAILURE", refusal="TOOL_FAILURE")
            return self._record(snapshot, correlation, payload, prior=prior)

    def _record(self, snapshot, correlation, payload, *, attempt=None, prior=()):
        entries = (("ROOM_CHOICE_ATTEMPT", attempt),) if attempt is not None else ()
        entries += (("ROOM_TOOL_OUTCOME", payload),)
        # A refusal is an audit event, not dispatch. Retrying its CAS can safely
        # retain the original attempted snapshot when a writer races the append.
        while True:
            version = self.store.session_version(snapshot.session_id)
            try:
                events = self._append(snapshot.session_id, version, correlation, entries,
                                      prior[-1].event_id if prior else None)
                return TurnOutcome(payload["candidate_id"], payload["refusal_code"] is None,
                                   tuple(e.event_id for e in (*prior, *events)), payload["terminal_state"])
            except VersionConflict:
                continue

    def step(self, session_id: str) -> TurnOutcome:
        if self.supervisor is not None:
            if self.supervisor.session_id != session_id:
                raise ValueError("supervisor belongs to another session")
            if self.supervisor.last_persistence_error:
                local = self._local_terminal(self.supervisor.last_terminal_outcome, "TERMINAL")
                if local is not None:
                    return local
        ingest_failed = False
        if self.ingest_pending is not None:
            try:
                self.ingest_pending(session_id)
            except Exception:
                ingest_failed = True
        snapshot = self.queries.project(session_id)
        if snapshot.terminal_state:
            return TurnOutcome("TERMINAL", False, (), snapshot.terminal_state)
        if self.supervisor is not None:
            try:
                decision = self.supervisor.prepare(snapshot_version=snapshot.state_version,
                                                   provider_wait_s=self.provider_wait_s)
            except Exception:
                terminal = self.supervisor.refresh_failure(self.flight_executor)
                return self._preempted(session_id, None, terminal)
            if decision.transition != "CONTINUE":
                terminal = self.supervisor.dispatch(decision, self.flight_executor)
                return self._preempted(session_id, decision, terminal)
        correlation = str(uuid.uuid4())
        if ingest_failed:
            payload = self._outcome(snapshot, "INGEST_PENDING", "INGEST_PENDING",
                                    relevant_hash(CandidateKind.READ_OBSERVATIONS, snapshot),
                                    "SOURCE_FAILURE", refusal="SOURCE_FAILURE")
            payload["error_category"] = "INGEST_ERROR"
            return self._record(snapshot, correlation, payload)
        try:
            snapshot, source_events = self._audit_source_failures(snapshot, correlation)
        except VersionConflict:
            payload = self._outcome(snapshot, "JEV", "JEV", relevant_hash(CandidateKind.ESCALATE, snapshot),
                                    "STALE_DECISION", refusal="STALE_DECISION")
            return self._record(snapshot, correlation, payload)
        outcome = self._decide(snapshot, correlation)
        return replace(outcome, event_ids=tuple(e.event_id for e in source_events) + outcome.event_ids)

    def _audit_source_failures(self, snapshot, correlation):
        entries = []
        for failure in snapshot.source_failures:
            digest = relevant_hash(failure.kind, snapshot)
            if any(o.candidate_id == failure.kind and o.relevant_state_sha256 == digest
                   and o.refusal_code == "SOURCE_FAILURE" for o in snapshot.outcomes):
                continue
            payload = self._outcome(snapshot, failure.kind.value, failure.kind, digest,
                                    "SOURCE_FAILURE", refusal="SOURCE_FAILURE")
            payload["error_category"] = failure.error_category
            entries.append(("ROOM_TOOL_OUTCOME", payload))
        if not entries:
            return snapshot, ()
        events = self._append(snapshot.session_id, snapshot.state_version, correlation + ":sources", entries)
        outcomes = tuple(ToolOutcome(e.payload["candidate_id"], e.payload["kind"],
                                     e.payload["relevant_state_sha256"], "SOURCE_FAILURE",
                                     "SOURCE_FAILURE", event_id=e.event_id) for e in events)
        return replace(snapshot, state_version=events[-1].state_version,
                       outcomes=snapshot.outcomes + outcomes,
                       tool_outcome_revision=snapshot.tool_outcome_revision + len(events)), events

    def _decide(self, snapshot, correlation):
        session_id = snapshot.session_id
        if snapshot.compile_rejected or snapshot.ledger is None:
            return self._finalize_terminal(session_id, correlation, snapshot, (), "CLARIFY", "CLARIFY",
                                           relevant_hash(CandidateKind.CLARIFY, snapshot),
                                           code="CLARIFICATION_REQUIRED", requested_terminal=TerminalState.BLOCKED)

        # Custom builders may narrow the legal set, but cannot add a physical,
        # stale, unsupported or previously refused action at this boundary.
        # OBSERVE (and the closer-inspection-unavailable answer) are only ever
        # legal when this session's observation collaborators are configured;
        # see _observation_enabled.
        legal = {c.candidate_id: c for c in self._legal_candidates(snapshot)}
        candidates = {c.candidate_id: c for c in self.candidate_builder(snapshot) if legal.get(c.candidate_id) == c}
        if not candidates:
            return self._finalize_terminal(session_id, correlation, snapshot, (), "ESCALATE", "ESCALATE",
                                           relevant_hash(CandidateKind.ESCALATE, snapshot),
                                           code="NO_LEGAL_CANDIDATES", requested_terminal=TerminalState.BLOCKED)
        criteria = {key: c.description for key, c in candidates.items()}
        state = asdict(snapshot)
        if self.supervisor is not None:
            # Task B item 2c: this turn's localization health, so Jev can
            # choose an early bounded re-anchor (never coordinates).
            state["localization"] = self.localization_health()
        coverage = self._coverage(snapshot) if self.supervisor is not None else project_coverage((), ())
        instruction = build_decision_instruction(snapshot, coverage, state.get("localization"),
                                                 tuple(candidates.values()))
        started = time.monotonic_ns()
        policy = self.acceptance

        def ask(asked_state):
            def choose(cancel):
                try:
                    return self.judge.choice(deepcopy(asked_state),
                        instruction,
                        criteria=dict(criteria), name="room_action"), None
                except Exception as exc:
                    return None, type(exc).__name__
            waited = self.wait_for_perception(choose, session_id=session_id)
            if waited.status == "PREEMPTED":
                return None
            if waited.status == "COMPLETED":
                answer, error = waited.value
            else:
                answer, error = None, waited.status
            # Task V2: the bar is the one for the kind Jev actually chose.
            try:
                label = answer.get("value") if isinstance(answer, dict) else None
            except Exception:
                label = None
            chosen = candidates.get(label) if type(label) is str else None
            act_at = policy.act_at_for(chosen.kind if chosen is not None else None)
            assessment = assess_choice(answer, criteria, escalation_label="ESCALATE",
                                       act_at=act_at, min_margin=policy.min_margin)
            return observe_kind_gate(assessment, candidates, act_at, policy.min_margin), error, act_at

        def audit(assessment):
            audited = asdict(assessment)
            audited["probabilities"] = [{"label": label, "probability": probability}
                                        for label, probability in assessment.probabilities]
            return audited

        asked = ask(state)
        if asked is None:
            return self._preempted(session_id, self.supervisor.last_preemption)
        assessment, provider_error, act_at = asked
        reask = None
        malformed = provider_error is not None or assessment.outcome in _MALFORMED_OUTCOMES
        if policy.reask_once and (malformed or assessment.outcome in ("LOW_CONFIDENCE", "THIN_MARGIN")):
            # Task V2: one re-ask with the refusal as feedback, never a loop.
            # Task 4b item 4: a malformed/illegal reply or a provider error is
            # re-asked once too, with feedback on the reply's shape.
            refusal = "PROVIDER_ERROR" if provider_error else assessment.outcome
            note = ("Your previous reply could not be used (" + refusal + "). Reply with one legal action "
                    "label as value, a probability for every legal label (the chosen one the highest) and a "
                    "confidence; this is the last ask this turn.") if malformed else (
                    "Your previous choice was refused: below the acceptance threshold for its "
                    "kind. Choose once more from the same legal actions. Repeat it only if you "
                    "are confident it is the right next action; this is the last ask this turn.")
            feedback = {"refused_choice": assessment.selected_label, "refusal": refusal,
                        "confidence": assessment.confidence, "required_confidence": act_at,
                        "margin": assessment.margin, "required_margin": policy.min_margin,
                        "acceptance_thresholds": policy.as_config(), "note": note}
            reask = {"first_assessment": audit(assessment), "first_act_at": act_at, "feedback": feedback}
            asked = ask({**state, "previous_refusal": feedback})
            if asked is None:
                return self._preempted(session_id, self.supervisor.last_preemption)
            assessment, provider_error, act_at = asked
        audited = audit(assessment)
        attempt = {"snapshot_version": snapshot.state_version, "state": state,
                   "candidates": [asdict(c) for c in candidates.values()], "assessment": audited,
                   "provider_error": provider_error, "act_at": act_at, "min_margin": policy.min_margin,
                   "acceptance_policy": policy.as_config(),
                   "latency_ms": (time.monotonic_ns() - started) // 1_000_000}
        if reask is not None:
            attempt["reask"] = reask
        selected = candidates.get(assessment.selected_label)
        # Illegal provider labels are one failed decision operation. Retain the
        # actual proposed label in the assessment, but do not let arbitrary new
        # strings evade the persisted no-progress signature.
        candidate_id = selected.candidate_id if selected else "JEV"
        kind = selected.kind if selected else "JEV"
        digest = selected.relevant_state_sha256 if selected else relevant_hash(CandidateKind.ESCALATE, snapshot)
        if self.store.session_version(session_id) != snapshot.state_version:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "STALE_DECISION", refusal="STALE_DECISION")
            return self._record(snapshot, correlation, payload, attempt=attempt)
        if assessment.outcome != "ACCEPTED" or provider_error:
            code = "PROVIDER_ERROR" if provider_error else assessment.outcome
            payload = self._outcome(snapshot, candidate_id, kind, digest, code, refusal=code)
            return self._record(snapshot, correlation, payload, attempt=attempt)
        assert selected is not None
        try:
            prior = self._append(session_id, snapshot.state_version, correlation, (("ROOM_CHOICE_ATTEMPT", attempt),))
        except VersionConflict:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "STALE_DECISION", refusal="STALE_DECISION")
            return self._record(snapshot, correlation, payload, attempt=attempt)

        expected_version = prior[-1].state_version
        # Evidence tables and the legacy adapter can change independently of a
        # room event; check both evidence and the session CAS just before dispatch.
        try:
            current_evidence = (self.queries.evidence_is_current(snapshot, selected.kind)
                                if selected.kind in (*READ_KINDS, CandidateKind.ANSWER) else True)
        except Exception:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "TOOL_FAILURE", refusal="TOOL_FAILURE")
            return self._record(snapshot, correlation, payload, prior=prior)
        if not current_evidence or self.store.session_version(session_id) != expected_version:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "STALE_DECISION", refusal="STALE_DECISION")
            return self._record(snapshot, correlation, payload, prior=prior)
        try:
            if selected.kind in READ_KINDS:
                facts = self.executor(selected, snapshot) if self.executor else self.queries.read(selected.kind, snapshot)
                if not isinstance(facts, tuple) or any(not isinstance(f, ReadFact) or f.kind != selected.kind or f not in snapshot.sources for f in facts):
                    raise ValueError("read facts must match the projected persisted sources")
                payload = self._outcome(snapshot, candidate_id, kind, digest,
                                        "READ_SUCCEEDED" if facts else "NO_EVIDENCE", facts=facts)
            elif selected.kind == CandidateKind.OBSERVE:
                # Effectful: resolves a checkpoint, admits/dispatches it through
                # the safety supervisor and, only once that checkpoint actually
                # completes, waits for and ingests its correlated perception
                # result. A mandatory recovery/return transition is applied and
                # reported here directly -- bypassing Jev, never persisted as an
                # ordinary ROOM_TOOL_OUTCOME refusal -- so the caller's outcome
                # already includes this turn's ROOM_CHOICE_ATTEMPT event.
                outcome = self._execute_observation(session_id, selected, snapshot, expected_version, correlation)
                if isinstance(outcome, TurnOutcome):
                    return replace(outcome, event_ids=tuple(e.event_id for e in prior) + outcome.event_ids)
                payload = self._outcome(snapshot, candidate_id, kind, digest, outcome["code"],
                                        refusal=outcome.get("refusal"), observed_at=outcome.get("observed_at"),
                                        count_evidence=outcome.get("count_evidence"),
                                        count_evidences=outcome.get("count_evidences"),
                                        detail=outcome.get("detail"))
                return self._record(snapshot, correlation, payload, prior=prior)
            elif selected.kind == CandidateKind.REANCHOR:
                # Task B item 2c: the supervisor's own stationary recovery,
                # chosen early by Jev and admitted like any other action. An
                # accepted fix commits its candidate invalidations; the typed
                # outcome is what Jev sees next turn.
                outcome = self._execute_reanchor(session_id, expected_version)
                if isinstance(outcome, TurnOutcome):
                    return replace(outcome, event_ids=tuple(e.event_id for e in prior) + outcome.event_ids)
                payload = self._outcome(snapshot, candidate_id, kind, digest, outcome["code"],
                                        refusal=outcome.get("refusal"))
                return self._record(snapshot, correlation, payload, prior=prior)
            elif selected.kind == CandidateKind.ANSWER:
                # Fix round 1 (Q1, Q3): a terminal answer -- plain or the
                # explicit ANSWER:<objective>:INCOMPLETE candidate -- requests
                # a supervisor-owned safe return before it is ever reported as
                # this session's final word (see _finalize_terminal).
                #
                # Residual F2: both count routes render the session COUNT fact
                # (``_count_projections``) -- grounded counts from this
                # session's own observations, each with its scope-worded
                # coverage caveat -- and persist it on the terminal outcome.
                # The legacy READ_COUNTS report is never rendered as a count.
                if candidate_id == "ANSWER:SEARCH":
                    # Task B item 2b: where the searched object was seen (or
                    # "not found in observed regions" with the coverage
                    # note), plus any count objectives' grounded counts; the
                    # safe return runs before this is recorded.
                    sections, conclusive = self._search_sections(snapshot)
                    projections = self._count_projections(snapshot)
                    sections += [render_objective_count(p) for p in projections]
                    facts = tuple(f for f in snapshot.facts if f.kind != CandidateKind.READ_COUNTS)
                    others = [o for o in self._observation_objectives(snapshot)
                              if o.kind != GoalKind.COUNT and not is_search_objective(o)]
                    if others and facts:
                        sections.append(render_answer(facts))
                    return self._finalize_terminal(
                        session_id, correlation, snapshot, prior, candidate_id, kind, digest,
                        code="ANSWERED_SEARCH" if conclusive else "ANSWERED_INCOMPLETE",
                        requested_terminal=TerminalState.COMPLETED if conclusive else TerminalState.INCOMPLETE_RETURNED,
                        answer="\n\n".join(sections), facts=facts,
                        extra={"count_projection": [p.to_payload() for p in projections]})
                if candidate_id == "ANSWER:COUNTS" or candidate_id.endswith(":INCOMPLETE"):
                    projections = self._count_projections(snapshot)
                    sections = [render_objective_count(p) for p in projections]
                    facts = tuple(f for f in snapshot.facts if f.kind != CandidateKind.READ_COUNTS)
                    extra = {"count_projection": [p.to_payload() for p in projections]}
                    if candidate_id == "ANSWER:COUNTS":
                        if any(o.kind != GoalKind.COUNT for o in self._observation_objectives(snapshot)) and facts:
                            sections.append(render_answer(facts))
                        return self._finalize_terminal(session_id, correlation, snapshot, prior, candidate_id,
                                                       kind, digest, code="ANSWERED_COUNT",
                                                       requested_terminal=TerminalState.COMPLETED,
                                                       answer="\n\n".join(sections), facts=facts, extra=extra)
                    objective_id = candidate_id.removeprefix("ANSWER:").removesuffix(":INCOMPLETE")
                    incomplete_answer = "\n\n".join((
                        f"Objective {objective_id} cannot be completed with the declared legal views "
                        f"available in this session.", *sections))
                    return self._finalize_terminal(session_id, correlation, snapshot, prior, candidate_id, kind,
                                                   digest, code="ANSWERED_INCOMPLETE",
                                                   requested_terminal=TerminalState.INCOMPLETE_RETURNED,
                                                   answer=incomplete_answer, facts=facts, extra=extra)
                answer_text = render_answer(snapshot.facts)
                return self._finalize_terminal(session_id, correlation, snapshot, prior, candidate_id, kind, digest,
                                               code="ANSWERED", requested_terminal=TerminalState.COMPLETED,
                                               answer=answer_text, facts=snapshot.facts)
            elif selected.kind == CandidateKind.CLARIFY:
                return self._finalize_terminal(session_id, correlation, snapshot, prior, candidate_id, kind, digest,
                                               code="CLARIFICATION_REQUIRED", requested_terminal=TerminalState.BLOCKED,
                                               answer="Please clarify the request or provide the missing recorded "
                                                      "evidence. This session supports historical reads only.")
            elif selected.kind == CandidateKind.VALIDATE_DETECTION:
                # Plan 03 Task 3 (closer-inspection viewpoints) is deferred; see
                # _closer_unavailable_candidates. This never executes a checkpoint.
                # Fix round 1 (Q11): this limitation is never terminal by
                # itself -- it is suppressed afterward and other pending
                # objectives keep being offered; the session only ends once
                # nothing else can progress (the NO_LEGAL_CANDIDATES path
                # above, which does request a safe return).
                payload = self._outcome(snapshot, candidate_id, kind, digest, "CLOSER_INSPECTION_UNAVAILABLE",
                                        answer="Closer inspection is not available in this session; only "
                                               "surveyed viewpoints and existing evidence can be reported.")
            else:
                return self._finalize_terminal(session_id, correlation, snapshot, prior, candidate_id, kind, digest,
                                               code="ESCALATED", requested_terminal=TerminalState.ESCALATED)
        except Exception:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "TOOL_FAILURE", refusal="TOOL_FAILURE")
        try:
            events = self._append(session_id, expected_version, correlation, (("ROOM_TOOL_OUTCOME", payload),), prior[-1].event_id)
            return TurnOutcome(candidate_id, payload["refusal_code"] is None,
                               tuple(e.event_id for e in (*prior, *events)), payload["terminal_state"])
        except VersionConflict:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "STALE_DECISION", refusal="STALE_DECISION")
            return self._record(snapshot, correlation, payload, prior=prior)

    def _checkpoint_targets_declared_pose(self, resolved, plan) -> bool:
        """Fix round 2 (Q5) / fix round 3 (F6): ``_coverage`` credits every
        declared cell/class a pose declares visibility of purely off the
        ``pose_id`` embedded in an OBSERVE candidate's ID -- it never checks
        that the checkpoint which actually ran was the one that pose
        declares. Verify, before this checkpoint is ever admitted or
        dispatched, that the plan ``resolve_checkpoint`` built truly targets
        that exact declared pose's resolved endpoint and heading, not merely
        some other (e.g. current, unmoved) position happening to be reported
        under this pose's ID. The heading comparison is modular, within
        tolerance -- never exact equality -- so a planner that emits
        headings normalized to [0, 360) is not refused merely because
        ``resolve_semantic_pose`` normalizes its own yaw to (-180, 180]."""
        target = Position(resolved.x_cm, resolved.y_cm, resolved.z_cm)
        return plan.target == target and _heading_within_tolerance(plan.terminal_heading_deg, resolved.yaw_deg)

    def _measured_pose_matches_declared_pose(self, resolved, result, health) -> bool:
        """Fix round 3 (F3): a checkpoint plan that *declares* the correct
        target/heading (``_checkpoint_targets_declared_pose``) can still fail
        to actually reach it -- nothing before this checked that the
        dispatched commands landed the aircraft there. The round-2 positive
        test fixture (``make_resolve_checkpoint``) hovers in place with a
        single CAPTURE and only relabels the plan's declared target/heading;
        this closes exactly that gap by additionally requiring the measured
        post-checkpoint position (``CheckpointOutcome.pose``) and the
        tracker's current heading to be within tolerance of the resolved
        pose's own declared endpoint/heading before coverage is ever
        credited.

        Fix round 4 (Q2): ``health`` is the tracker snapshot the caller
        captured once, right after dispatch -- never re-read here -- so this
        check and ``_ground_observation`` (which the caller also feeds this
        same snapshot) always agree on which pose sample the checkpoint
        actually reached."""
        pose = result.pose
        if pose is None:
            return False
        # Task B2: the checkpoint planner deliberately leaves an offset within
        # its dead band uncorrected (e.g. the CanningSt pad at y 190 and the
        # rear poses at y 183: no 7 cm move is ever commanded), so a correct
        # arrival is within the dead band of the declared pose, plus the
        # measurement tolerance -- never within the tolerance alone, which
        # refused every such observation as TOOL_FAILURE.
        # Live 2026-09-24: a real Tello overshoots a 20 cm move by ~13 cm and
        # its altimeter wanders ~20 cm, so the dead-band tolerance refused
        # every real observation. Grounding places detections at the
        # *measured* pose, so an arrival this close still observes its region.
        limits = getattr(self.supervisor, "limits", None)
        tolerance_cm = max(_ARRIVAL_XY_TOLERANCE_CM,
                           _POSE_POSITION_TOLERANCE_CM + float(getattr(limits, "dead_band_cm", 0) or 0))
        distance_cm = math.hypot(pose.x_cm - resolved.x_cm, pose.y_cm - resolved.y_cm)
        if distance_cm > tolerance_cm or abs(pose.z_cm - resolved.z_cm) > _ARRIVAL_Z_TOLERANCE_CM:
            return False
        return _heading_within_tolerance(health.heading_deg, resolved.yaw_deg, _ARRIVAL_HEADING_TOLERANCE_DEG)

    def _capture_completed(self, plan, result) -> bool:
        """Fix round 2 (Q5): a dispatched ``CheckpointOutcome`` can report
        ``COMPLETED``, ``completion_known`` and even non-empty
        ``observation_ids`` without this plan's own CAPTURE command ever
        having run -- ``SafetySupervisor._dispatch``'s mid-plan re-dispatch
        (supervisor.py's remaining-commands loop, ``if refreshed.transition
        != current.transition: return self.dispatch(...)``) discards any
        commands already accumulated and can return a fresh outcome (e.g.
        ``_recover``'s own vacuous ``CheckpointOutcome("COMPLETED", (), (),
        ..., True)``) that never touched this checkpoint's capture. Require
        instead that the outcome's own per-command record
        (``command_states``) shows a completed CAPTURE that is actually one
        of this plan's declared commands -- never inferred merely from the
        outcome's top-level status/observation_ids, which a recovery or
        partial re-dispatch outcome can satisfy vacuously."""
        planned = tuple(command for command in plan.commands if command.kind == "CAPTURE")
        completed = tuple(state.command for state in result.command_states
                          if state.status == "COMPLETED" and state.command.kind == "CAPTURE"
                          and state.command in planned)
        return bool(planned) and len(completed) == len(planned) == len(result.observation_ids)

    def _checkpoint_completed_since(self, session_id, version) -> bool:
        """Whether any command was executed and recorded after ``version``
        (this turn's ROOM_CHOICE_ATTEMPT). ``CHECKPOINT_COMPLETED`` is written
        by ``SafetySupervisor._record_completion`` after every executed
        command, so its absence proves the conflict happened before motion.
        If the ledger cannot be read, motion cannot be ruled out."""
        try:
            return any(e.event_type == "CHECKPOINT_COMPLETED" and e.state_version > version
                       for e in self.store.events(session_id))
        except Exception:
            return True

    def _record_perception_preempted(self, session_id, candidate, snapshot, correlation, requests):
        """Fix round 3 (F4): a perception wait preempted by a mandatory
        safety transition after the checkpoint's own CAPTURE already
        completed must never silently drop those captures. Persist a durable,
        non-terminal OBSERVE outcome referencing the issued perception
        request (so a later delivery, or a fresh OBSERVE retry, is never
        mistaken for a session that flew this checkpoint and captured
        nothing) before the mandatory preemption itself is reported.
        Coverage is never credited for this outcome -- only an
        OBSERVE_SUCCEEDED outcome ever resolves a cell (see ``_coverage``).
        Best-effort: a CAS race against this fresh append is not itself a
        failure worth reporting differently -- the mandatory preemption path
        still reports this turn either way."""
        digest = candidate.relevant_state_sha256
        payload = self._outcome(snapshot, candidate.candidate_id, candidate.kind, digest,
                                "PERCEPTION_PREEMPTED", refusal="PERCEPTION_PREEMPTED")
        request = requests[0]
        payload["perception_request"] = {"observation_id": request.observation_id, "frame_id": request.frame_id,
                                         "frame_path": request.frame_path, "captured_at": request.captured_at}
        payload["perception_requests"] = [
            {"observation_id": item.observation_id, "frame_id": item.frame_id,
             "frame_path": item.frame_path, "captured_at": item.captured_at}
            for item in requests]
        try:
            self._append(session_id, self.store.session_version(session_id),
                        correlation + ":perception-preempted", (("ROOM_TOOL_OUTCOME", payload),))
        except VersionConflict:
            pass

    def _execute_observation(self, session_id, candidate, snapshot, expected_version, correlation):
        """Resolve, admit and dispatch exactly one checkpoint for ``candidate``,
        then wait for and ingest every correlated perception result.

        Returns a plain dict payload (``{"code": ..., "refusal": ...}``) for the
        normal ROOM_TOOL_OUTCOME append path, or a fully-formed ``TurnOutcome``
        when a mandatory safety transition bypassed Jev and must be reported
        directly (see the OBSERVE branch in ``_decide``).
        """
        try:
            plan = self.resolve_checkpoint(candidate, snapshot)
        except Exception:
            plan = None
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        resolved = resolve_pose_id(self.supervisor.site, pose_id)
        if plan is None or resolved is None or not self._checkpoint_targets_declared_pose(resolved, plan):
            return _failure("TOOL_FAILURE", "no flight plan to this pose from the current position")
        # The full plan proves the declared destination and sweep shape. In
        # live sessions each look is then planned from a fresh tracker pose
        # and admitted as its own checkpoint. Forecasting all three looks as
        # one leg exceeds the calibrated drift envelope even though each
        # individually supervised leg is safe.
        resolvers = self.resolve_sweep_checkpoints or (self.resolve_checkpoint,)
        completed_legs = []
        for index, resolver in enumerate(resolvers):
            try:
                leg = resolver(candidate, snapshot) if self.resolve_sweep_checkpoints else plan
                if not self._checkpoint_targets_declared_pose(resolved, leg):
                    return _failure("TOOL_FAILURE", f"look {index + 1} could not be planned from the "
                                    "current position")
                admission = self.supervisor.prepare(
                    snapshot_version=(expected_version if index == 0 else self.store.session_version(session_id)),
                    checkpoint=leg, provider_wait_s=self.provider_wait_s if index == len(resolvers) - 1 else 0,
                    replan=lambda resolver=resolver: resolver(candidate, snapshot))
            except Exception:
                terminal = self.supervisor.refresh_failure(self.flight_executor)
                return self._preempted(session_id, None, terminal)
            try:
                leg_result = self.supervisor.dispatch(admission, self.flight_executor)
            except VersionConflict:
                if self._checkpoint_completed_since(session_id, expected_version):
                    return _failure("CHECKPOINT_FAILED", f"look {index + 1} was interrupted mid-flight")
                return {"code": "STALE_DECISION", "refusal": "STALE_DECISION"}
            except Exception:
                return _failure("CHECKPOINT_FAILED", f"look {index + 1} failed to execute")
            if admission.transition != "CONTINUE" or isinstance(leg_result, TerminalOutcome):
                terminal_outcome = leg_result if isinstance(leg_result, TerminalOutcome) else None
                return self._preempted(session_id, admission, terminal_outcome=terminal_outcome)
            if (leg_result.status != "COMPLETED" or not leg_result.completion_known
                    or not self._capture_completed(leg, leg_result)):
                reason = ("positioning was lost and the tag search had not re-anchored yet"
                          if leg_result.status == "RECOVERY_PENDING"
                          else f"the flight step ended {leg_result.status}")
                return _failure("TOOL_FAILURE", f"look {index + 1} of {len(resolvers)} did not capture: {reason}")
            completed_legs.append(leg_result)
        result = replace(completed_legs[-1],
                         command_states=tuple(state for leg in completed_legs for state in leg.command_states),
                         observation_ids=tuple(frame for leg in completed_legs for frame in leg.observation_ids))
        if len(result.observation_ids) != sum(command.kind == "CAPTURE" for command in plan.commands):
            return _failure("TOOL_FAILURE", "not every look captured a frame")
        # Fix round 4 (Q2): capture the tracker's health exactly once, here,
        # right after dispatch -- the same sample the measured-pose check
        # below uses -- and thread it through to grounding. Previously
        # ``_ground_observation`` re-read ``self.supervisor.tracker.health``
        # after the perception wait (which can last up to
        # ``provider_wait_s``), so a heading update or re-anchor during that
        # wait could place every persisted estimate at a different heading
        # than the one this checkpoint was actually verified against, and
        # could even change the reported provenance for a position that
        # never moved.
        health = self.supervisor.tracker.health
        if not self._measured_pose_matches_declared_pose(resolved, result, health):
            # Fix round 3 (F3): the plan declared the right target/heading,
            # but the aircraft's own measured post-dispatch pose does not
            # actually match it -- never credit coverage off a checkpoint
            # that merely claimed the right pose.
            return _failure("TOOL_FAILURE", _arrival_miss(resolved, result.pose, health))
        try:
            captured = self.perception_capture(candidate, result, snapshot)
            captures = (captured if isinstance(captured, tuple)
                        else (CapturedRequest(captured, health.heading_deg),))
            if not captures or len(captures) != len(result.observation_ids):
                return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}
        except Exception:
            return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}

        def perceive(cancel):
            results, errors = [], []
            # Each request is independent; run the three provider calls in
            # parallel so the supervisor's one bounded wait is the actual
            # worst-case airborne detector interval. Results are restored to
            # capture order before correlation and durable ingestion.
            pool = ThreadPoolExecutor(max_workers=len(captures), thread_name_prefix="room-perception")
            try:
                futures = tuple(pool.submit(self.perception_provider, item.request) for item in captures)
                for future in futures:
                    if cancel.is_set():
                        break
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        print(f"Perception failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                        errors.append(type(exc).__name__)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            return tuple(results), tuple(errors)

        waited = self.wait_for_perception(perceive, session_id=session_id,
                                          timeout_s=self.provider_wait_s)
        if waited.status != "COMPLETED":
            print(f"Perception wait ended {waited.status} (limit {self.provider_wait_s} s)",
                  file=sys.stderr, flush=True)
        if waited.status == "PREEMPTED":
            self._record_perception_preempted(session_id, candidate, snapshot, correlation,
                                              tuple(item.request for item in captures))
            return self._preempted(session_id, self.supervisor.last_preemption)
        if waited.status == "COMPLETED":
            perception_results, provider_errors = waited.value
        else:
            perception_results, provider_errors = (), (waited.status,)
        if provider_errors or len(perception_results) != len(captures):
            return _failure("PROVIDER_ERROR", "the detector did not answer in time ("
                            + ", ".join(sorted(set(provider_errors))) + "); the flight itself was fine")
        # Fix round 1 (S1/Q4): the result must actually correlate to the
        # request this turn issued, and ingestion's own authorization must be
        # checked -- a stale snapshot_version, a session mismatch or a
        # replayed/duplicate delivery must never be reported the same as a
        # freshly authorized observation.
        if any(perception_result.request != captured_frame.request
               or captured_frame.request.session_id != session_id
               for captured_frame, perception_result in zip(captures, perception_results)):
            return {"code": "PERCEPTION_MISMATCH", "refusal": "PERCEPTION_MISMATCH"}
        try:
            current_version = self.store.session_version(session_id)
            ingest_outcomes = tuple(ingest_result(self.store, perception_result,
                                                  current_snapshot_version=current_version)
                                    for perception_result in perception_results)
        except Exception:
            return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}
        if any(perception_result.status != "SUCCEEDED" for perception_result in perception_results):
            return {"code": "PROVIDER_ERROR", "refusal": "PROVIDER_ERROR"}
        if not all(outcome.authorizes_current_state for outcome in ingest_outcomes):
            return {"code": "STALE_RESULT", "refusal": "STALE_RESULT"}
        # Residual F2: ground this frame's detections at the pose the
        # checkpoint actually measured, now -- the pose, camera model and
        # support declarations are persisted with the outcome, so later
        # counts are a pure projection of append-only session state.
        try:
            count_evidences = tuple(self._ground_observation(
                resolved, result, captured_frame.request, perception_result, health,
                heading_deg=captured_frame.heading_deg)
                for captured_frame, perception_result in zip(captures, perception_results))
        except Exception:
            return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}
        # Fix round 1 (Q12): carry the perception's own measured capture time
        # forward for coverage bookkeeping, never a fabricated timestamp and
        # never the durable event ID (see _coverage).
        return {"code": "OBSERVE_SUCCEEDED", "observed_at": perception_results[-1].completed_at,
                "count_evidence": count_evidences[0].to_payload(),
                "count_evidences": [evidence.to_payload() for evidence in count_evidences]}

    def _ground_observation(self, resolved, result, request, perception_result, health, *, heading_deg=None):
        """Residual F2: turn one authorized frame into persisted count
        evidence. The camera position is the checkpoint's *measured*
        post-dispatch position (already verified against the declared pose,
        see ``_measured_pose_matches_declared_pose``) and the recorded heading
        at this frame's CAPTURE, converted once into the compass convention
        ``estimate_detection`` uses. A tag-anchored pose is MEASURED; any
        other pose source is dead-reckoned (COMMANDED, wider covariance); a
        pose needing recovery or lost is stale, so nothing is placed from it.

        Fix round 4 (Q2): ``health`` is the tracker snapshot the caller
        captured once, right after dispatch -- never a fresh read taken here
        after the perception wait, which can run for up to
        ``provider_wait_s`` and could otherwise report a different
        heading/source/state than the one this checkpoint was verified
        against."""
        pose = PoseEvidence(
            result.pose.x_cm, result.pose.y_cm, result.pose.z_cm,
            semantic_heading_to_compass_deg(health.heading_deg if heading_deg is None else heading_deg), 0.0,
            "MEASURED" if health.source == "mapped_joint_tags" else "COMMANDED",
            request.captured_at,
            health.state in (LocalizationState.RECOVERY_REQUIRED, LocalizationState.LOST),
        )
        return ground_frame(perception_result.detections, observation_id=request.observation_id,
                            frame_id=request.frame_id, pose_id=resolved.pose_id, pose=pose,
                            geometry=self.count_geometry)

    def run_until_terminal(self, session_id: str) -> TerminalState:
        while True:
            outcome = self.step(session_id)
            if outcome.terminal_state is not None:
                return outcome.terminal_state
