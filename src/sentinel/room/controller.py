"""One version-checked, audited read-only room action per turn."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import math
import re
import time
from typing import Callable, TYPE_CHECKING
import uuid

from sentinel.jev_choice import assess_choice
from sentinel.judgment import ChoiceJudge
from sentinel.cube.supervised_wait import SupervisedWait
from sentinel.cube.step_executor import TerminalOutcome

from .candidates import build_candidates, build_observation_candidates
from .coverage import CellObservation, CoverageProjection, project_coverage
from .ingest import ingest_result
from .models import CandidateKind, GoalKind, GoalStatus, RoomCandidate, TerminalState
from .queries import READ_KINDS, ReadFact, ReadQueries, ReadSnapshot, ToolOutcome, relevant_hash, render_answer
from .store import RoomStore, VersionConflict

if TYPE_CHECKING:
    from sentinel.survey.store import SurveyStore


@dataclass(frozen=True)
class TurnOutcome:
    candidate_id: str
    accepted: bool
    event_ids: tuple[str, ...]
    terminal_state: TerminalState | None
    terminal_source: str | None = None
    terminal_reason: str | None = None
    persistence_error: str | None = None


class RoomController:
    def __init__(self, store: RoomStore, judge: ChoiceJudge, *,
                 survey_store: SurveyStore | None = None, survey_run_id: str | None = None,
                 queries: ReadQueries | None = None, act_at: float = .8, min_margin: float = .15,
                 candidate_builder: Callable[[ReadSnapshot], tuple[RoomCandidate, ...]] | None = None,
                 executor: Callable[[RoomCandidate, ReadSnapshot], tuple[ReadFact, ...]] | None = None,
                 ingest_pending: Callable[[str], object] | None = None,
                 supervisor=None, flight_executor=None, provider_wait_s: float = 10.0,
                 resolve_checkpoint=None, perception_capture=None, perception_provider=None):
        for value in (act_at, min_margin):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("choice thresholds must be finite probabilities")
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
        self.perception_provider = perception_provider
        self.candidate_builder = candidate_builder if candidate_builder is not None else self._legal_candidates

    @property
    def _observation_enabled(self) -> bool:
        return (self.supervisor is not None and self.flight_executor is not None
                and self.resolve_checkpoint is not None and self.perception_capture is not None
                and self.perception_provider is not None)

    def _observation_objectives(self, snapshot):
        if snapshot.ledger is None or snapshot.ledger.status != GoalStatus.PENDING:
            return ()
        return snapshot.ledger.objectives

    def _coverage(self, snapshot: ReadSnapshot) -> CoverageProjection:
        """Rebuild the coverage projection purely from replayed OBSERVE outcomes.

        A pose declares visibility of a set of coverage cells
        (``RoomSite.pose_visibility``); once an OBSERVE checkpoint at that pose
        has completed and its perception result was ingested, every
        (cell, class) pair that pose declares is resolved for that flight --
        this session never infers *which* class a detection belongs to a
        specific cell (that per-detection geometric grounding is future work;
        see the task report), only that the region was actually examined.
        ``observed_at``/evidence use the durable ROOM_TOOL_OUTCOME event ID,
        never a fabricated timestamp.
        """
        site = self.supervisor.site
        cells_by_id = {cell.cell_id: cell for cell in site.coverage_cells}
        declared_views_by_cell: dict[str, list[str]] = {}
        for pose_id, cell_ids in site.pose_visibility:
            for cell_id in cell_ids:
                declared_views_by_cell.setdefault(cell_id, []).append(pose_id)
        observations = []
        for outcome in snapshot.outcomes:
            if outcome.kind != CandidateKind.OBSERVE.value or outcome.code != "OBSERVE_SUCCEEDED":
                continue
            pose_id = outcome.candidate_id.rsplit(":", 1)[-1]
            for cell_id in dict(site.pose_visibility).get(pose_id, ()):
                cell = cells_by_id.get(cell_id)
                if cell is None:
                    continue
                for class_name in cell.required_classes:
                    observations.append(CellObservation(cell_id, class_name, pose_id, "OBSERVED",
                                                         outcome.event_id, evidence_refs=(outcome.event_id,)))
        return project_coverage(site.coverage_cells, tuple(observations),
                                declared_views_by_cell={k: tuple(v) for k, v in declared_views_by_cell.items()})

    def _closer_unavailable_candidates(self, snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
        """Plan 03 Task 3 (closer-inspection viewpoints) is deferred: any goal
        that can only be satisfied by getting closer -- a FIND_OBSERVE
        objective whose terminal condition requires validation -- can never be
        offered an executable closer candidate. Rather than let Jev loop
        between CLARIFY/ESCALATE, this reports that limitation explicitly."""
        candidates = []
        for objective in self._observation_objectives(snapshot):
            if objective.kind != GoalKind.FIND_OBSERVE or objective.terminal_condition != "VALIDATED":
                continue
            candidate_id = f"VALIDATE:{objective.objective_id}:UNAVAILABLE"
            digest = relevant_hash(CandidateKind.ESCALATE, snapshot)
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

    def _legal_candidates(self, snapshot: ReadSnapshot) -> tuple[RoomCandidate, ...]:
        return (*build_candidates(snapshot), *self._observation_candidates(snapshot),
               *self._closer_unavailable_candidates(snapshot))

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

    def wait_for_perception(self, operation, *, session_id):
        """Bound a data-returning detector operation; its caller owns ingestion."""
        if self.supervisor is not None:
            return self.supervisor.run_wait(operation, snapshot_version=self.store.session_version(session_id),
                                           timeout_s=self.provider_wait_s, executor=self.flight_executor)
        return SupervisedWait().run(operation, time.monotonic() + self.provider_wait_s, lambda: None)

    def _append(self, session_id, version, correlation, entries, causation=None):
        return self.store.append_events(session_id=session_id, expected_version=version, events=tuple(
            {"event_type": kind, "payload": data, "correlation_id": correlation,
             "causation_id": causation, "idempotency_key": f"{correlation}:{index}:{kind}"}
            for index, (kind, data) in enumerate(entries)
        ))

    def _outcome(self, snapshot, candidate_id, kind, digest, code, *, facts=(), terminal=None, refusal=None, answer=None):
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
        return result

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
            payload = self._outcome(snapshot, "CLARIFY", "CLARIFY", relevant_hash(CandidateKind.CLARIFY, snapshot),
                                    "CLARIFICATION_REQUIRED", terminal=TerminalState.BLOCKED)
            return self._record(snapshot, correlation, payload)

        # Custom builders may narrow the legal set, but cannot add a physical,
        # stale, unsupported or previously refused action at this boundary.
        # OBSERVE (and the closer-inspection-unavailable answer) are only ever
        # legal when this session's observation collaborators are configured;
        # see _observation_enabled.
        legal = {c.candidate_id: c for c in self._legal_candidates(snapshot)}
        candidates = {c.candidate_id: c for c in self.candidate_builder(snapshot) if legal.get(c.candidate_id) == c}
        if not candidates:
            payload = self._outcome(snapshot, "ESCALATE", "ESCALATE", relevant_hash(CandidateKind.ESCALATE, snapshot),
                                    "NO_LEGAL_CANDIDATES", terminal=TerminalState.BLOCKED)
            return self._record(snapshot, correlation, payload)
        criteria = {key: c.description for key, c in candidates.items()}
        state = asdict(snapshot)
        provider_error = None
        started = time.monotonic_ns()
        def choose(cancel):
            try:
                return self.judge.choice(deepcopy(state),
                    "Choose one legal action. Reads are historical. Never compose factual values.",
                    criteria=dict(criteria), name="room_action"), None
            except Exception as exc:
                return None, type(exc).__name__
        waited = self.wait_for_perception(choose, session_id=session_id)
        if waited.status == "PREEMPTED":
            return self._preempted(session_id, self.supervisor.last_preemption)
        if waited.status == "COMPLETED":
            answer, provider_error = waited.value
        else:
            answer, provider_error = None, waited.status
        assessment = assess_choice(answer, criteria, escalation_label="ESCALATE",
                                   act_at=self.act_at, min_margin=self.min_margin)
        audited = asdict(assessment)
        audited["probabilities"] = [{"label": label, "probability": probability}
                                    for label, probability in assessment.probabilities]
        attempt = {"snapshot_version": snapshot.state_version, "state": state,
                   "candidates": [asdict(c) for c in candidates.values()], "assessment": audited,
                   "provider_error": provider_error, "act_at": self.act_at, "min_margin": self.min_margin,
                   "latency_ms": (time.monotonic_ns() - started) // 1_000_000}
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
                outcome = self._execute_observation(session_id, selected, snapshot, expected_version)
                if isinstance(outcome, TurnOutcome):
                    return replace(outcome, event_ids=tuple(e.event_id for e in prior) + outcome.event_ids)
                payload = self._outcome(snapshot, candidate_id, kind, digest, outcome["code"],
                                        refusal=outcome.get("refusal"))
                return self._record(snapshot, correlation, payload, prior=prior)
            elif selected.kind == CandidateKind.ANSWER:
                payload = self._outcome(snapshot, candidate_id, kind, digest, "ANSWERED", facts=snapshot.facts,
                                        answer=render_answer(snapshot.facts), terminal=TerminalState.COMPLETED)
            elif selected.kind == CandidateKind.CLARIFY:
                payload = self._outcome(snapshot, candidate_id, kind, digest, "CLARIFICATION_REQUIRED", terminal=TerminalState.BLOCKED,
                                        answer="Please clarify the request or provide the missing recorded evidence. This session supports historical reads only.")
            elif selected.kind == CandidateKind.VALIDATE_DETECTION:
                # Plan 03 Task 3 (closer-inspection viewpoints) is deferred; see
                # _closer_unavailable_candidates. This never executes a checkpoint.
                payload = self._outcome(snapshot, candidate_id, kind, digest, "CLOSER_INSPECTION_UNAVAILABLE",
                                        terminal=TerminalState.BLOCKED,
                                        answer="Closer inspection is not available in this session; only "
                                               "surveyed viewpoints and existing evidence can be reported.")
            else:
                payload = self._outcome(snapshot, candidate_id, kind, digest, "ESCALATED", terminal=TerminalState.ESCALATED)
        except Exception:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "TOOL_FAILURE", refusal="TOOL_FAILURE")
        try:
            events = self._append(session_id, expected_version, correlation, (("ROOM_TOOL_OUTCOME", payload),), prior[-1].event_id)
            return TurnOutcome(candidate_id, payload["refusal_code"] is None,
                               tuple(e.event_id for e in (*prior, *events)), payload["terminal_state"])
        except VersionConflict:
            payload = self._outcome(snapshot, candidate_id, kind, digest, "STALE_DECISION", refusal="STALE_DECISION")
            return self._record(snapshot, correlation, payload, prior=prior)

    def _execute_observation(self, session_id, candidate, snapshot, expected_version):
        """Resolve, admit and dispatch exactly one checkpoint for ``candidate``,
        then wait for and ingest its correlated perception result.

        Returns a plain dict payload (``{"code": ..., "refusal": ...}``) for the
        normal ROOM_TOOL_OUTCOME append path, or a fully-formed ``TurnOutcome``
        when a mandatory safety transition bypassed Jev and must be reported
        directly (see the OBSERVE branch in ``_decide``).
        """
        try:
            plan = self.resolve_checkpoint(candidate, snapshot)
        except Exception:
            plan = None
        if plan is None:
            return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}
        admission = self.supervisor.prepare(snapshot_version=expected_version, checkpoint=plan)
        result = self.supervisor.dispatch(admission, self.flight_executor)
        if admission.transition != "CONTINUE" or isinstance(result, TerminalOutcome):
            # Mandatory recovery/return/contingency: the checkpoint the candidate
            # asked for was never (fully) executed as chosen. No perception is
            # attempted; a fresh Jev choice must wait for the next turn.
            terminal_outcome = result if isinstance(result, TerminalOutcome) else None
            return self._preempted(session_id, admission, terminal_outcome=terminal_outcome)
        if result.status != "COMPLETED" or not result.completion_known:
            # SDK/command completion uncertainty: never treated as a success,
            # and never releases a second optional checkpoint this turn.
            return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}
        try:
            request = self.perception_capture(candidate, result, snapshot)
        except Exception:
            return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}

        def perceive(cancel):
            try:
                return self.perception_provider(request), None
            except Exception as exc:
                return None, type(exc).__name__

        waited = self.wait_for_perception(perceive, session_id=session_id)
        if waited.status == "PREEMPTED":
            return self._preempted(session_id, self.supervisor.last_preemption)
        if waited.status == "COMPLETED":
            perception_result, provider_error = waited.value
        else:
            perception_result, provider_error = None, waited.status
        if provider_error or perception_result is None:
            return {"code": "PROVIDER_ERROR", "refusal": "PROVIDER_ERROR"}
        try:
            current_version = self.store.session_version(session_id)
            ingest_result(self.store, perception_result, current_snapshot_version=current_version)
        except Exception:
            return {"code": "TOOL_FAILURE", "refusal": "TOOL_FAILURE"}
        if perception_result.status != "SUCCEEDED":
            return {"code": "PROVIDER_ERROR", "refusal": "PROVIDER_ERROR"}
        return {"code": "OBSERVE_SUCCEEDED"}

    def run_until_terminal(self, session_id: str) -> TerminalState:
        while True:
            outcome = self.step(session_id)
            if outcome.terminal_state is not None:
                return outcome.terminal_state
