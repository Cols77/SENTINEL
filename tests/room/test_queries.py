"""Read projections use persisted evidence, never live providers."""
from dataclasses import asdict

import pytest

from sentinel.room.goals import EntityRef, GoalLedger, GoalObjective
from sentinel.room.ingest import ingest_result
from sentinel.room.models import CandidateKind, CapabilityProfile, GoalKind, GoalStatus
from sentinel.room.queries import ReadFact, ReadQueries, render_answer
from sentinel.room.store import RoomStore
from sentinel.survey.store import SurveyStore
from tests.room.test_ingest import sample


@pytest.fixture
def room(tmp_path):
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("s1", "What is recorded?", CapabilityProfile(()))
    yield store
    store.close()


def append(store, kind, payload):
    version = store.session_version("s1")
    return store.append_event(session_id="s1", expected_version=version, event_type=kind,
                              payload=payload, correlation_id="fixture", causation_id=None,
                              idempotency_key=f"fixture:{version}")


def goal(store, kind=GoalKind.ANSWER_STATE, *, terminal="REPORTED", scope="ROOM"):
    ref = EntityRef("target", "g1:observe", "OBSERVED_ENTITY", None)
    objective = GoalObjective("g1", 1, kind, () if kind == GoalKind.ANSWER_STATE else ("computer",),
                              None, None, scope, terminal, None,
                              ref if kind in (GoalKind.FOLLOW, GoalKind.LAND_NEAR) else None,
                              ("g1:observe",) if kind in (GoalKind.FOLLOW, GoalKind.LAND_NEAR) else ())
    return append(store, "GOAL_LEDGER_ACCEPTED", {
        "goal_revision": 1, "ledger": asdict(GoalLedger("What is recorded?", (objective,), GoalStatus.PENDING)),
    })


def drone(store, battery=73):
    return append(store, "DRONE_STATE_RECORDED", {
        "observed_at": "2026-09-22T00:00:00+00:00", "scope": "aircraft:fixture",
        "uncertainty": "last recorded telemetry; present state unknown",
        "state": {"battery_percent": battery, "flight_state": "LANDED"},
    })


def survey(tmp_path):
    store = SurveyStore.open(tmp_path / "survey.sqlite3")
    store.create_run("run1", "Count the room")
    sweep = store.add_sweep("run1", "legacy", 1)
    frame = store.add_frame(sweep, stop_index=0, look_deg=0, x_cm=0, y_cm=0, z_cm=0,
                            heading_deg=0, position_kind="COMMANDED", artifact_id="artifact",
                            sha256="a" * 64, captured_at="2026-09-21T12:00:00+00:00")
    counts = [
        {"class": "computer", "confirmed": 2, "uncertain": 1, "method": "MERGED_MULTI_VIEW"},
        {"class": "person", "confirmed": 0, "uncertain": 3, "method": "SINGLE_VIEW_LOWER_BOUND"},
        {"class": "pen", "confirmed": 0, "uncertain": 0, "method": "NOT_OBSERVABLE"},
    ]
    report = store.add_report("run1", "Untrusted free text: exactly 999 computers", counts)
    return store, frame, report


def test_drone_facts_have_exact_source_time_scope_and_uncertainty(room):
    evidence = drone(room)
    queries = ReadQueries(room)
    snapshot = queries.project("s1")
    facts = queries.read(CandidateKind.READ_DRONE, snapshot)
    fact, = facts
    assert isinstance(fact, ReadFact)
    assert dict(fact.values) == {"battery_percent": 73, "flight_state": "LANDED"}
    assert fact.evidence_ids == (evidence.event_id,)
    assert fact.observed_at == evidence.payload["observed_at"]
    assert fact.scope == "aircraft:fixture"
    assert fact.scope_kind == "AIRCRAFT_STATE"
    assert fact.uncertainty == evidence.payload["uncertainty"]
    text = render_answer(facts)
    for value in (fact.fact_id, evidence.event_id, fact.observed_at, fact.scope, fact.uncertainty, "73"):
        assert value in text


def test_missing_evidence_never_means_zero(room):
    queries = ReadQueries(room)
    for kind in (CandidateKind.READ_DRONE, CandidateKind.READ_OBSERVATIONS, CandidateKind.READ_COUNTS):
        assert queries.read(kind, queries.project("s1")) == ()
    with pytest.raises(ValueError, match="facts"):
        render_answer(())


def test_observations_are_historical_and_do_not_invent_numeric_confidence(room, tmp_path):
    result = sample(tmp_path)
    outcome = ingest_result(room, result, current_snapshot_version=0)
    queries = ReadQueries(room)
    fact, = queries.read(CandidateKind.READ_OBSERVATIONS, queries.project("s1"))
    assert dict(fact.values)["class_name"] == "computer"
    assert dict(fact.values)["raw_score"] is None
    assert dict(fact.values)["confidence_band"] == "high"
    assert fact.observed_at == result.request.captured_at
    assert fact.scope == "observation:o1/frame:f1"
    assert fact.scope_kind == "HISTORICAL_OBSERVATION"
    assert set(fact.evidence_ids) == {"o1", "f1", "d1", outcome.result_id}
    assert "historical" in fact.uncertainty
    assert "VLM_CONFIDENCE_BAND" in render_answer((fact,))


@pytest.mark.parametrize("recorded_at", [None, "2026-09-23T00:00:00.999000+00:00",
                                        "2026-09-23T00:00:01.199900+00:00"])
def test_legacy_reports_keep_lower_bound_uncertain_and_unobservable_semantics(room, tmp_path, monkeypatch, recorded_at):
    if recorded_at is not None:
        monkeypatch.setattr("sentinel.survey.store.utc_now", lambda: recorded_at)
    legacy, frame, report = survey(tmp_path)
    try:
        queries = ReadQueries(room, survey_store=legacy, survey_run_id="run1")
        facts = queries.read(CandidateKind.READ_COUNTS, queries.project("s1"))
        assert len(facts) == 3
        assert all(f.observed_at == "UNKNOWN" for f in facts)
        assert all(f.scope == "survey_run:run1; report frame provenance unknown" for f in facts)
        assert all(f.scope_kind == "LEGACY_SURVEY_RUN" for f in facts)
        assert all(f.evidence_ids == (report,) for f in facts)
        assert frame not in render_answer(facts)
        text = render_answer(facts)
        assert "2 confirmed" in text and "1 uncertain" in text
        assert "3" in text and "single-view lower bound" in text
        assert "pen: not observable" in text
        assert "exactly 999 computers" not in text
        assert "NOT_OBSERVABLE" in facts[2].uncertainty
    finally:
        legacy.connection.close()


def test_report_without_linked_frames_has_explicit_unknown_observation_time(room, tmp_path):
    legacy = SurveyStore.open(tmp_path / "survey.sqlite3")
    try:
        legacy.create_run("run1", "count")
        queries = ReadQueries(room, survey_store=legacy, survey_run_id="run1")
        assert queries.read(CandidateKind.READ_COUNTS, queries.project("s1")) == ()
        report = legacy.add_report("run1", "0 computers", [{"class": "computer", "confirmed": 0,
                                                            "uncertain": 0, "method": "MERGED_MULTI_VIEW"}])
        fact, = queries.read(CandidateKind.READ_COUNTS, queries.project("s1"))
        assert fact.observed_at == "UNKNOWN"
        assert fact.evidence_ids == (report,)
        assert "observation time unknown" in render_answer((fact,))
    finally:
        legacy.connection.close()


def test_later_run_frame_cannot_retroactively_change_report_provenance_or_hash(room, tmp_path):
    legacy, frame, report = survey(tmp_path)
    try:
        queries = ReadQueries(room, survey_store=legacy, survey_run_id="run1")
        before = queries.project("s1")
        sweep = legacy.add_sweep("run1", "later", 1)
        later_frame = legacy.add_frame(sweep, stop_index=0, look_deg=0, x_cm=0, y_cm=0, z_cm=0,
                                      heading_deg=0, position_kind="COMMANDED", artifact_id="later",
                                      sha256="b" * 64, captured_at="2099-01-01T00:00:00+00:00")
        after = queries.project("s1")
        assert before.sources == after.sources
        assert queries.relevant_hash(CandidateKind.READ_COUNTS, before) == queries.relevant_hash(CandidateKind.READ_COUNTS, after)
        created = legacy.connection.execute("SELECT created_at FROM reports WHERE report_id = ?", (report,)).fetchone()[0]
        assert all(f.recorded_at == created and f.observed_at == "UNKNOWN" for f in after.sources)
        assert all(f.evidence_ids == (report,) for f in after.sources)
        rendered = render_answer(after.sources)
        assert frame not in rendered and later_frame not in rendered
        assert created in rendered and "report recorded at" in rendered
        assert "2099-01-01" not in rendered
    finally:
        legacy.connection.close()


def test_snapshot_replay_and_audit_events_do_not_change_read_hash(room):
    goal(room)
    drone(room)
    queries = ReadQueries(room)
    before = queries.project("s1")
    append(room, "ROOM_CHOICE_ATTEMPT", {"message": "audit only"})
    after = queries.project("s1")
    assert after.state_version == before.state_version + 1
    assert queries.relevant_hash(CandidateKind.READ_DRONE, before) == queries.relevant_hash(CandidateKind.READ_DRONE, after)
    drone(room, 70)
    latest = queries.project("s1")
    assert queries.relevant_hash(CandidateKind.READ_DRONE, latest) != queries.relevant_hash(CandidateKind.READ_DRONE, before)
    assert latest.pose_revision == 0 and latest.energy_revision == 2
    path = room.connection.execute("PRAGMA database_list").fetchone()[2]
    with_reopened = RoomStore.open(path)
    try:
        assert ReadQueries(with_reopened).project("s1") == latest
    finally:
        with_reopened.close()


@pytest.mark.parametrize("confirmed,uncertain", [(2, 0), (1, 2)])
def test_legacy_single_view_lower_bound_includes_confirmed_and_uncertain(room, tmp_path, confirmed, uncertain):
    legacy, _, _ = survey(tmp_path)
    try:
        legacy.add_report("run1", "saved result", [{"class": "person", "confirmed": confirmed,
                                                   "uncertain": uncertain, "method": "SINGLE_VIEW_LOWER_BOUND"}])
        queries = ReadQueries(room, survey_store=legacy, survey_run_id="run1")
        fact, = queries.read(CandidateKind.READ_COUNTS, queries.project("s1"))
        text = render_answer((fact,))
        assert f"at least {confirmed + uncertain}" in text
        assert "single-view lower bound" in text
        assert dict(fact.values)["confirmed"] == confirmed
        assert dict(fact.values)["uncertain"] == uncertain
        assert fact.uncertainty in text
    finally:
        legacy.connection.close()
