"""One version-checked, audited read-only room action per turn."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import math
import time
from typing import Callable, TYPE_CHECKING
import uuid

from sentinel.jev_choice import assess_choice
from sentinel.judgment import ChoiceJudge
from sentinel.cube.supervised_wait import SupervisedWait

from .candidates import build_candidates
from .models import CandidateKind, RoomCandidate, TerminalState
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


class RoomController:
    def __init__(self, store: RoomStore, judge: ChoiceJudge, *,
                 survey_store: SurveyStore | None = None, survey_run_id: str | None = None,
                 queries: ReadQueries | None = None, act_at: float = .8, min_margin: float = .15,
                 candidate_builder: Callable[[ReadSnapshot], tuple[RoomCandidate, ...]] = build_candidates,
                 executor: Callable[[RoomCandidate, ReadSnapshot], tuple[ReadFact, ...]] | None = None,
                 ingest_pending: Callable[[str], object] | None = None,
                 supervisor=None, flight_executor=None, provider_wait_s: float = 10.0):
        for value in (act_at, min_margin):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("choice thresholds must be finite probabilities")
        self.store, self.judge = store, judge
        query_clock = ({"clock": supervisor.clock, "clock_domain_id": supervisor.clock_domain_id}
                       if supervisor is not None else {})
        self.queries = queries if queries is not None else ReadQueries(
            store, survey_store=survey_store, survey_run_id=survey_run_id, **query_clock)
        self.act_at, self.min_margin = act_at, min_margin
        self.candidate_builder, self.executor = candidate_builder, executor
        self.ingest_pending = ingest_pending
        if (supervisor is None) != (flight_executor is None):
            raise ValueError("supervisor and flight executor must be supplied together")
        if type(provider_wait_s) not in (int, float) or not math.isfinite(provider_wait_s) or provider_wait_s <= 0:
            raise ValueError("provider wait must be finite and positive")
        self.supervisor, self.flight_executor, self.provider_wait_s = supervisor, flight_executor, provider_wait_s

    def _preempted(self, session_id, decision):
        snapshot = self.queries.project(session_id)
        events = self.store.events(session_id)
        version = decision.request.snapshot_version if decision else self.supervisor.last_preempted_version
        return TurnOutcome(decision.transition if decision else "LAND_CONTINGENCY", False,
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
            if self.supervisor.session_id != session_id:
                raise ValueError("supervisor belongs to another session")
            try:
                decision = self.supervisor.prepare(snapshot_version=snapshot.state_version,
                                                   provider_wait_s=self.provider_wait_s)
            except Exception:
                self.supervisor.refresh_failure(self.flight_executor)
                return self._preempted(session_id, None)
            if decision.transition != "CONTINUE":
                self.supervisor.dispatch(decision, self.flight_executor)
                return self._preempted(session_id, decision)
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
        legal = {c.candidate_id: c for c in build_candidates(snapshot)}
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
            elif selected.kind == CandidateKind.ANSWER:
                payload = self._outcome(snapshot, candidate_id, kind, digest, "ANSWERED", facts=snapshot.facts,
                                        answer=render_answer(snapshot.facts), terminal=TerminalState.COMPLETED)
            elif selected.kind == CandidateKind.CLARIFY:
                payload = self._outcome(snapshot, candidate_id, kind, digest, "CLARIFICATION_REQUIRED", terminal=TerminalState.BLOCKED,
                                        answer="Please clarify the request or provide the missing recorded evidence. This session supports historical reads only.")
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

    def run_until_terminal(self, session_id: str) -> TerminalState:
        while True:
            outcome = self.step(session_id)
            if outcome.terminal_state is not None:
                return outcome.terminal_state
