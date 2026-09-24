"""Deterministic, historical reads from room evidence and legacy survey reports.

These adapters never construct a drone, camera, detector or provider. A fact's
identity covers its values and provenance; reads and answers retain that identity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import time
from typing import Literal, TYPE_CHECKING

from .goals import EntityRef, GoalLedger, GoalObjective
from .grounding import FrameCountEvidence
from .models import CandidateKind, GoalKind, GoalStatus, RoomSnapshot, TerminalState
from .store import RoomStore

if TYPE_CHECKING:
    from sentinel.survey.store import SurveyStore

READ_KINDS = (CandidateKind.READ_DRONE, CandidateKind.READ_OBSERVATIONS, CandidateKind.READ_COUNTS)


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ReadFact:
    fact_id: str
    kind: CandidateKind
    values: tuple[tuple[str, str | int | float | None], ...]
    observed_at: str
    scope: str
    uncertainty: str
    evidence_ids: tuple[str, ...]
    recorded_at: str | None = None
    scope_kind: Literal["UNKNOWN", "AIRCRAFT_STATE", "HISTORICAL_OBSERVATION", "LEGACY_SURVEY_RUN"] = "UNKNOWN"


@dataclass(frozen=True)
class ToolOutcome:
    candidate_id: str
    kind: str
    relevant_state_sha256: str
    code: str
    refusal_code: str | None = None
    facts: tuple[ReadFact, ...] = ()
    terminal_state: TerminalState | None = None
    event_id: str = ""
    #: The perception result's own measured capture time for an
    #: OBSERVE_SUCCEEDED outcome (fix round 1, Q12); None for every other
    #: outcome and for outcomes recorded before this field existed.
    observed_at: str | None = None
    #: The grounded count evidence (placed estimates, people split, unplaced
    #: detections) an OBSERVE_SUCCEEDED outcome persisted for its frame
    #: (Plan 03 residual task R, F2); None for every other outcome.
    count_evidence: FrameCountEvidence | None = None
    #: All grounded frames from one completed sweep. Legacy events use the
    #: singular field above and leave this empty.
    count_evidences: tuple[FrameCountEvidence, ...] = ()


@dataclass(frozen=True)
class SourceFailure:
    kind: CandidateKind
    error_category: str


@dataclass(frozen=True)
class ReadSnapshot(RoomSnapshot):
    request_text: str
    ledger: GoalLedger | None
    sources: tuple[ReadFact, ...]
    facts: tuple[ReadFact, ...]
    outcomes: tuple[ToolOutcome, ...]
    terminal_state: TerminalState | None
    compile_rejected: bool
    source_failures: tuple[SourceFailure, ...] = ()


def _fact(kind, values, observed_at, scope, uncertainty, evidence_ids, *, recorded_at=None) -> ReadFact:
    scope_kind = {CandidateKind.READ_DRONE: "AIRCRAFT_STATE",
                  CandidateKind.READ_OBSERVATIONS: "HISTORICAL_OBSERVATION",
                  CandidateKind.READ_COUNTS: "LEGACY_SURVEY_RUN"}[kind]
    body = dict(kind=kind, values=tuple(sorted(values.items())), observed_at=observed_at,
                scope=scope, uncertainty=uncertainty, evidence_ids=tuple(evidence_ids), recorded_at=recorded_at,
                scope_kind=scope_kind)
    return ReadFact(f"fact:{_sha(body)}", **body)


def _restore_fact(body: dict) -> ReadFact:
    return ReadFact(body["fact_id"], CandidateKind(body["kind"]),
                    tuple(tuple(pair) for pair in body["values"]), body["observed_at"],
                    body["scope"], body["uncertainty"], tuple(body["evidence_ids"]), body.get("recorded_at"),
                    body.get("scope_kind", "UNKNOWN"))


def _ledger(body: dict) -> GoalLedger:
    objectives = []
    for raw in body["objectives"]:
        item = dict(raw)
        item["kind"] = GoalKind(item["kind"])
        for field in ("subject_ref", "target_ref"):
            item[field] = EntityRef(**item[field]) if item.get(field) else None
        objectives.append(GoalObjective(**item))
    return GoalLedger(body["request_text"], tuple(objectives), GoalStatus(body["status"]))


def relevant_hash(kind: CandidateKind, snapshot: ReadSnapshot) -> str:
    """Audit versions and refusals are intentionally absent from relevant state."""
    sources = snapshot.sources
    failures = snapshot.source_failures
    if kind in READ_KINDS:
        sources = tuple(f for f in sources if f.kind == kind)
        failures = tuple(f for f in failures if f.kind == kind)
    return _sha({"kind": kind, "goal_revision": snapshot.goal_revision,
                 "ledger": asdict(snapshot.ledger) if snapshot.ledger else None,
                 "sources": [asdict(f) for f in sources],
                 "source_failures": [asdict(f) for f in failures],
                 "facts": [] if kind in READ_KINDS else [asdict(f) for f in snapshot.facts]})


class ReadQueries:
    """Read adapters for an explicit room session and optional legacy survey run.

    Only saved report class_counts are adapted: recomputing from object rows
    would lose single-view person counting and observability semantics.
    """

    def __init__(self, store: RoomStore, *, survey_store: SurveyStore | None = None,
                 survey_run_id: str | None = None, clock=time.monotonic, clock_domain_id: str | None = None):
        if (survey_store is None) != (survey_run_id is None):
            raise ValueError("survey_store and survey_run_id must be supplied together")
        self.store = store
        self.survey_store = survey_store
        self.survey_run_id = survey_run_id
        self.clock, self.clock_domain_id = clock, clock_domain_id

    relevant_hash = staticmethod(relevant_hash)

    def _capture(self, session_id: str):
        # One read transaction pins room events and ingestion tables even when
        # another RoomStore connection appends concurrently.
        failures = []
        with self.store._lock:
            self.store._require_open()
            connection = self.store.connection
            connection.execute("BEGIN")
            try:
                session = connection.execute("SELECT * FROM room_sessions WHERE session_id = ?",
                                             (session_id,)).fetchone()
                if session is None:
                    raise KeyError(session_id)
                events = self.store.events(session_id)
                try:
                    observations = connection.execute(
                        "SELECT o.observation_id, o.frame_id, o.action_id, o.captured_at, r.result_id, "
                        "d.detection_id, d.class_name, d.confidence_band, d.raw_score, d.score_kind "
                        "FROM room_observations o JOIN room_perception_results r "
                        "ON r.session_id = o.session_id AND r.observation_id = o.observation_id "
                        "JOIN room_detection_evidence d ON d.result_id = r.result_id "
                        "WHERE o.session_id = ? AND r.status = 'SUCCEEDED' AND r.identity_status = 'VERIFIED' "
                        "ORDER BY o.captured_at, o.observation_id, d.detection_id", (session_id,),
                    ).fetchall()
                except Exception as exc:
                    observations = ()
                    failures.append(SourceFailure(CandidateKind.READ_OBSERVATIONS, type(exc).__name__))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        facts = []
        drones = [e for e in events if e.event_type == "DRONE_STATE_RECORDED"]
        if drones:
            try:
                event = drones[-1]
                data = event.payload
                values = data.get("state", {})
                allowed = {"battery_percent", "flight_state", "x_cm", "y_cm", "z_cm", "heading_deg",
                           "pose_source", "pose_quality", "pose_age_s", "localization_state", "airborne",
                           "terminal_state", "action_progress", "completion_known"}
                values = {key: value for key, value in values.items() if key in allowed}
                if "clock_domain_id" in data:
                    now = self.clock()
                    current = (data["clock_domain_id"] == self.clock_domain_id and data["refresh_succeeded"]
                        and all(0 <= now - data[key] <= data["sample_max_age_s"]
                                for key in ("pose_measured_at", "energy_measured_at")))
                    values["state_freshness"] = "FRESH" if current else "STALE"
                if values and all(data.get(key) for key in ("observed_at", "scope", "uncertainty")):
                    facts.append(_fact(CandidateKind.READ_DRONE, values, data["observed_at"], data["scope"],
                                       data["uncertainty"], (event.event_id,)))
            except Exception as exc:
                failures.append(SourceFailure(CandidateKind.READ_DRONE, type(exc).__name__))
        # A sweep is one OBSERVE result. An early frame may already be in the
        # ingestion tables when a later frame fails; only frames named by a
        # successful durable outcome are readable as observation evidence.
        committed_frames = {
            (frame.get("observation_id"), frame.get("frame_id"))
            for event in events if event.event_type == "ROOM_TOOL_OUTCOME"
            and event.payload.get("kind") == CandidateKind.OBSERVE.value
            and event.payload.get("code") == "OBSERVE_SUCCEEDED"
            for frame in (event.payload.get("count_evidences")
                          or ([event.payload["count_evidence"]] if event.payload.get("count_evidence") else []))
            if isinstance(frame, dict)
        }
        for row in observations:
            if (row["action_id"].startswith("OBSERVE:")
                    and (row["observation_id"], row["frame_id"]) not in committed_frames):
                continue
            facts.append(_fact(CandidateKind.READ_OBSERVATIONS,
                               {key: row[key] for key in ("class_name", "confidence_band", "raw_score", "score_kind")},
                               row["captured_at"], f"observation:{row['observation_id']}/frame:{row['frame_id']}",
                               "historical detection; confidence band is not a probability; current presence unknown",
                               (row["observation_id"], row["frame_id"], row["detection_id"], row["result_id"])))
        return dict(session), events, tuple(facts), tuple(failures)

    def _counts(self) -> tuple[ReadFact, ...]:
        if self.survey_store is None:
            return ()
        connection = self.survey_store.connection
        # Legacy reports contain no frame association. Current run frames may
        # have been added after this report; neither their IDs nor capture times
        # can be attributed to its counts. The report's own creation time is
        # provenance, explicitly distinct from unknown observation time.
        report = connection.execute(
            "SELECT * FROM reports WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (self.survey_run_id,),
        ).fetchone()
        if report is None:
            return ()
        refs = (report["report_id"],)
        facts = []
        entries = json.loads(report["class_counts_json"])
        if not isinstance(entries, list):
            raise ValueError("legacy counts must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("class"), str):
                raise ValueError("legacy count entry is malformed")
            if entry.get("method") not in ("SINGLE_VIEW_LOWER_BOUND", "MERGED_MULTI_VIEW", "NOT_OBSERVABLE"):
                raise ValueError("legacy count method is unsupported")
            if any(type(entry.get(key)) is not int or entry[key] < 0 for key in ("confirmed", "uncertain")):
                raise ValueError("legacy count values are malformed")
            facts.append(_fact(CandidateKind.READ_COUNTS,
                               {key: entry[key] for key in ("class", "confirmed", "uncertain", "method")},
                               "UNKNOWN", f"survey_run:{self.survey_run_id}; report frame provenance unknown",
                               f"{entry['method']}; legacy report; observation time, frame provenance, full-room coverage and current presence unknown",
                               refs, recorded_at=report["created_at"]))
        return tuple(facts)

    def _sources(self, session_id: str):
        session, events, sources, failures = self._capture(session_id)
        try:
            sources += self._counts()
        except Exception as exc:
            failures += (SourceFailure(CandidateKind.READ_COUNTS, type(exc).__name__),)
        return session, events, sources, failures

    def project(self, session_id: str) -> ReadSnapshot:
        session, events, sources, failures = self._sources(session_id)
        ledger = None
        goal_revision = 0
        rejected = False
        outcomes = []
        # Fix round 1 (Q1, Q9): walk events in their durable sequence order and
        # let the chronologically LAST terminal signal win, whether it comes
        # from a physical landing/abort or from a Jev-decided ROOM_TOOL_OUTCOME
        # -- rather than always preferring a flight-derived terminal
        # regardless of order. Before this fix round, a flight terminal event
        # always latched ``supervisor._terminal`` and so always short-circuited
        # any later turn before another outcome could be recorded; the only
        # sequence that can now put an outcome after a flight terminal is the
        # controller's own request-safe-return-then-report flow
        # (``_finalize_terminal``), where the later, Jev-decided terminal is
        # exactly the one that must be reported. A terminal drone event
        # requires ``completion_known`` for SAFE_LANDED -- an SDK-uncertain
        # completion is never projected as a confirmed safe landing.
        chronological_terminal = None
        for event in events:
            data = event.payload
            if event.event_type == "GOAL_LEDGER_ACCEPTED":
                ledger, goal_revision, rejected = _ledger(data["ledger"]), data["goal_revision"], False
            elif event.event_type == "GOAL_COMPILE_REJECTED":
                rejected = True
            elif event.event_type == "ROOM_TOOL_OUTCOME":
                outcome = ToolOutcome(
                    data["candidate_id"], data["kind"], data["relevant_state_sha256"], data["code"],
                    data.get("refusal_code"), tuple(_restore_fact(f) for f in data.get("facts", [])),
                    TerminalState(data["terminal_state"]) if data.get("terminal_state") else None,
                    event.event_id, data.get("observed_at"),
                    FrameCountEvidence.from_payload(data["count_evidence"]) if data.get("count_evidence") else None,
                    tuple(FrameCountEvidence.from_payload(item) for item in data.get("count_evidences", ())),
                )
                outcomes.append(outcome)
                if outcome.terminal_state is not None:
                    chronological_terminal = outcome.terminal_state
            elif event.event_type == "DRONE_STATE_RECORDED" and isinstance(data.get("state"), dict):
                measured = data["state"]
                if measured.get("terminal_state"):
                    chronological_terminal = (
                        TerminalState.SAFE_LANDED
                        if measured["terminal_state"] == "LANDED" and measured.get("completion_known")
                        else TerminalState.ABORTED)
                elif (measured.get("action_progress") == "COMMAND_COMPLETION_UNKNOWN"
                      or measured.get("flight_state") in {"TERMINAL", "CLOSED"}):
                    chronological_terminal = TerminalState.ABORTED
        drone_events = [e for e in events if e.event_type == "DRONE_STATE_RECORDED"
                        and isinstance(e.payload.get("state"), dict)]
        snapshot = ReadSnapshot(
            session_id=session_id, state_version=session["state_version"], goal_revision=goal_revision,
            pose_revision=sum(any(k in e.payload.get("state", {}) for k in ("x_cm", "y_cm", "z_cm", "heading_deg")) for e in drone_events),
            energy_revision=sum("battery_percent" in e.payload.get("state", {}) for e in drone_events),
            home_revision=0, coverage_revision=0, static_evidence_revision=len(sources),
            target_revision=0, landing_zone_revision=0, tool_outcome_revision=len(outcomes),
            request_text=session["request_text"], ledger=ledger, sources=sources, facts=(),
            outcomes=tuple(outcomes), terminal_state=chronological_terminal,
            compile_rejected=rejected, source_failures=failures,
        )
        facts = {}
        for outcome in outcomes:
            if outcome.kind in READ_KINDS and outcome.code == "READ_SUCCEEDED" and outcome.relevant_state_sha256 == relevant_hash(CandidateKind(outcome.kind), snapshot):
                for fact in outcome.facts:
                    if fact in sources:
                        facts[fact.fact_id] = fact
        return replace(snapshot, facts=tuple(facts.values()))

    def read(self, kind: CandidateKind, snapshot: ReadSnapshot) -> tuple[ReadFact, ...]:
        if kind not in READ_KINDS:
            raise ValueError("read handler requires a read candidate")
        return tuple(f for f in snapshot.sources if f.kind == kind)

    def evidence_is_current(self, snapshot: ReadSnapshot, kind: CandidateKind) -> bool:
        _, _, sources, failures = self._sources(snapshot.session_id)
        current = replace(snapshot, sources=sources, source_failures=failures)
        return relevant_hash(kind, current) == relevant_hash(kind, snapshot)


def render_answer(facts: tuple[ReadFact, ...]) -> str:
    """Only typed persisted values enter factual answer templates."""
    if not facts:
        raise ValueError("an answer requires facts")
    lines = []
    for fact in facts:
        values = dict(fact.values)
        if fact.kind == CandidateKind.READ_COUNTS:
            name, method = values["class"], values["method"]
            if method == "NOT_OBSERVABLE":
                body = f"{name}: not observable"
            elif method == "SINGLE_VIEW_LOWER_BOUND":
                lower_bound = values["confirmed"] + values["uncertain"]
                body = f"{name}: at least {lower_bound} observed, single-view lower bound"
            else:
                body = f"{name}: {values['confirmed']} confirmed; {values['uncertain']} uncertain"
        else:
            body = "; ".join(f"{key}={value}" for key, value in fact.values)
        timing = "observation time unknown" if fact.observed_at == "UNKNOWN" else f"Observed at {fact.observed_at}"
        if fact.recorded_at is not None:
            timing += f"; report recorded at {fact.recorded_at}"
        lines.append(f"{body}. {timing}; scope: {fact.scope}; uncertainty: {fact.uncertainty}. "
                     f"[fact={fact.fact_id}; evidence={', '.join(fact.evidence_ids)}]")
    return "\n".join(lines)
