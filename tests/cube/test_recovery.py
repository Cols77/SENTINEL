from dataclasses import asdict, replace
import json
from pathlib import Path

import jsonschema
import pytest

from sentinel.cube.checkpoint import HomeDefinition
from sentinel.cube.localization import LocalizationState
from sentinel.cube.recovery import (
    PoseReport, RecoveryController, RecoveryEnvironment, RecoveryVolume,
)
from sentinel.models import Position
from sentinel.room.site import (
    ContingencyAssurance, ContingencyKind, ContingencyTrigger, RoomSite,
    RouteDefinition, TerminalContingency, Volume3D, adapt_recovery_environment,
    parse_room_site, room_site_document,
)
from tests.cube.test_localization import fix, tracker


POLYGON = ((0, 0), (200, 0), (200, 200), (0, 200))


def site():
    return RoomSite("room", "a" * 64, "b" * 64,
                    (Volume3D("free", POLYGON, (-20, 220)),), (),
                    (Volume3D("corridor", POLYGON, (0, 200)),),
                    (RouteDefinition("home-route", (Position(100, 100, 100),), ("corridor",)),),
                    HomeDefinition("pad", Position(100, 100, 0), 90, "home-route"),
                    tuple(TerminalContingency(trigger.value, trigger, ContingencyKind.LAND_IN_PLACE,
                        POLYGON, (0, 200), "COASTING", "survey", "checked-clear", 5000)
                        for trigger in ContingencyTrigger), "survey-revision-1")


def assurance(**changes):
    return replace(ContingencyAssurance("a1", "NAVIGATION_UNAVAILABLE", "session", "clock",
        "CURRENT", "2026-09-23T00:00:00Z", 10000, 99000, ("clearance-observation",)), **changes)


def environment():
    return RecoveryEnvironment("session", "clock", ((90, (1, 2)), (60, (1, 2, 3)), (120, (1,))),
        (RecoveryVolume("rotation", POLYGON, (0, 200)),), "NAVIGATION_UNAVAILABLE", 18000)


def controller(**kwargs):
    value, clock = tracker()
    recovery = RecoveryController(value, clock=clock, burst_source=lambda action: (),
                                  energy_admits=lambda action: True, **kwargs)
    return recovery, value, clock


def reports(clock, **changes):
    return tuple(replace(PoseReport(str(i), fix(clock)), **changes) for i in range(3))


def terminal_context(value):
    from sentinel.room.site import SessionClockGuard
    guard = SessionClockGuard(session_id="session")
    return (assurance(clock_domain_id=guard.clock_domain_id),), {
        "session_id": guard.session_id, "clock_domain_id": guard.clock_domain_id,
        "clock_guard": guard, "drift_profile": value.profile,
    }


def test_stationary_burst_precedes_finite_headings_ranked_by_joint_tag_visibility():
    recovery, value, _ = controller()
    env = environment()
    first = recovery.next_action(value.health, env, ())
    assert first.kind == "STATIONARY_BURST"
    assert not recovery.assess_burst(first, ()).accepted
    second = recovery.next_action(value.health, env, ())
    assert second.kind == "YAW_BURST" and second.heading_deg == 60
    recovery.assess_burst(second, ())
    third = recovery.next_action(value.health, env, (60,))
    assert third.kind == "YAW_BURST" and third.heading_deg == 90
    recovery.assess_burst(third, ())
    assert recovery.next_action(value.health, env, (60, 90)).kind == "CONTINGENCY"


@pytest.mark.parametrize("mode", ["lost", "clearance", "energy", "deadline", "attempts", "expired", "clock_reset"])
def test_recovery_exhaustion_or_failed_integrity_never_translates_or_explores(mode):
    recovery, value, clock = controller()
    env = environment()
    first = recovery.next_action(value.health, env, ())
    recovery.assess_burst(first, ())
    if mode == "lost":
        value.accept_fix(fix(clock, position=Position(900, 100, 100)))
    elif mode == "clearance":
        env = replace(env, rotation_clearance_volumes=(RecoveryVolume("small", ((95, 95), (105, 95), (105, 105), (95, 105)), (0, 200)),))
    elif mode == "energy":
        recovery.energy_admits = lambda action: False
    elif mode == "deadline":
        clock.now = 18
    elif mode == "attempts":
        recovery.max_attempts = 1
    elif mode == "expired":
        env = replace(env, navigation_contingency_expires_monotonic_ms=10000)
    else:
        clock.now = 1
    result = recovery.next_action(value.health, env, ())
    assert result.kind in {"CONTINGENCY", "EMERGENCY"}
    if mode not in {"expired", "clock_reset", "deadline"}:
        assert result.contingency_id == "NAVIGATION_UNAVAILABLE"
    else:
        assert result.kind == "EMERGENCY"


@pytest.mark.parametrize("bad", ["stale", "future", "single_tag", "unmapped", "duplicate", "spread", "heading", "outside", "height", "few"])
def test_burst_rejects_untrustworthy_joint_tag_evidence(bad):
    recovery, value, clock = controller()
    action = recovery.next_action(value.health, environment(), ())
    sample = list(reports(clock))
    bad_fix = {
        "stale": {"measured_at": 8}, "future": {"measured_at": 11},
        "single_tag": {"tag_ids": (1,)}, "unmapped": {"tag_ids": (80, 90)},
        "spread": {"position": Position(112, 100, 100)}, "heading": {"heading_deg": 150},
        "outside": {"position": Position(250, 100, 100)}, "height": {"position": Position(100, 100, 150)},
    }
    if bad in bad_fix:
        sample[0] = replace(sample[0], fix=replace(sample[0].fix, **bad_fix[bad]))
    elif bad == "duplicate":
        sample = [sample[0]] * 3
    else:
        sample.pop()
    outcome = recovery.assess_burst(action, tuple(sample))
    assert not outcome.accepted and not outcome.events
    assert value.health.position == Position(100, 100, 100)


def test_reanchor_resets_history_atomically_and_emits_all_invalidations():
    from tests.cube.test_localization import command
    recovery, value, clock = controller()
    value.note_command(command(direction="right", distance_cm=20), 10)
    action = recovery.next_action(value.health, environment(), ())
    sample = tuple(replace(report, fix=replace(report.fix, position=Position(118, 100, 100)))
                   for report in reports(clock))
    outcome = recovery.assess_burst(action, sample)
    assert outcome.accepted
    assert value.health.position == Position(118, 100, 100)
    assert value.health.heading_deg == 90
    assert value.health.translation_cm == value.health.yaw_deg == value.health.look_away_s == 0
    assert value.health.estimator_failures == 0
    assert value.health.state == LocalizationState.ANCHORED
    assert value.health.requires_route_validation and value.health.requires_energy_validation
    assert tuple(event["event_type"] for event in outcome.events) == (
        "POSE_REANCHORED", "ROUTES_INVALIDATED", "CANDIDATES_INVALIDATED", "TARGET_LOCKS_INVALIDATED")


def test_noncanonical_burst_requires_physical_restoration_and_fresh_canonical_evidence():
    from tests.cube.test_localization import command
    recovery, value, clock = controller()
    value.note_command(command("ROTATE", degrees=30, direction="cw"), 10)
    action = recovery.next_action(value.health, environment(), ())
    result = recovery.assess_burst(action, tuple(replace(report, fix=replace(report.fix, heading_deg=60))
                                              for report in reports(clock)))
    assert not result.accepted and result.reason == "restore_canonical_heading"
    assert value.health.heading_deg == 60
    restore = recovery.next_action(value.health, environment(), ())
    assert restore.kind == "RESTORE_CANONICAL" and restore.heading_deg == 90
    assert recovery.assess_burst(restore, reports(clock)).accepted


def test_injected_source_and_clock_are_used_and_actions_cannot_be_replayed():
    recovery, value, clock = controller()
    recovery.burst_source = lambda action: reports(clock)
    action = recovery.next_action(value.health, environment(), ())
    assert recovery.acquire(action).accepted
    assert not recovery.assess_burst(action, reports(clock)).accepted


def test_site_roundtrip_and_schema_are_static_and_have_exact_base_contract():
    doc = room_site_document(site())
    assert parse_room_site(doc) == site()
    assert "monotonic" not in json.dumps(doc)
    schema = json.loads((Path(__file__).resolve().parents[2] /
        "docs/superpowers/specs/contracts/room-site-base-v1.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(doc, schema)
    for path in ("terminal_contingencies", "free_space_volumes", "home"):
        broken = dict(doc)
        del broken[path]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(broken, schema)
    doc["terminal_contingencies"][0]["expires_monotonic_ms"] = 30000
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)
    with pytest.raises(ValueError):
        parse_room_site(doc)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "route_kind", "geometry", "policy", "age", "pose", "home_route", "corridor", "hash"])
def test_invalid_or_incomplete_site_cannot_admit_real_mode(fault):
    doc = room_site_document(site())
    if fault == "missing":
        doc["terminal_contingencies"].pop()
    elif fault == "duplicate":
        doc["terminal_contingencies"].append(dict(doc["terminal_contingencies"][0]))
    elif fault == "home_route":
        doc["home"]["route_id"] = "missing"
    elif fault == "corridor":
        doc["routes"][0]["corridor_volume_ids"] = ["missing"]
    elif fault == "hash":
        doc["tag_map_sha256"] = "not-a-hash"
    else:
        field, invalid = {"route_kind": ("kind", "RETURN_VIA_ROUTE"), "geometry": ("landing_polygon_xy_cm", []),
                          "policy": ("assurance_policy_id", ""), "age": ("max_assurance_age_ms", 0),
                          "pose": ("required_pose_state", "unknown")}[fault]
        doc["terminal_contingencies"][0][field] = invalid
    with pytest.raises(ValueError):
        parse_room_site(doc, real_mode=True)


def adapt(records, **changes):
    return adapt_recovery_environment(site(), records, session_id="session", clock_domain_id="clock",
        navigation_contingency_id="NAVIGATION_UNAVAILABLE", now_monotonic_ms=changes.pop("now_monotonic_ms", 11000),
        mapped_heading_tag_ids=changes.pop("mapped_heading_tag_ids", environment().mapped_heading_tag_ids), **changes)


def test_adapter_clamps_assurance_lifetime_and_only_copies_monotonic_deadline():
    adapted = adapt((assurance(),))
    assert adapted.navigation_contingency_expires_monotonic_ms == 15000
    assert "assured_at" not in asdict(adapted)
    assert all(type(volume) is RecoveryVolume for volume in adapted.rotation_clearance_volumes)


@pytest.mark.parametrize("records,now", [
    ((), 11000), ((assurance(), assurance(assurance_id="a2")), 11000),
    ((assurance(session_id="other"),), 11000), ((assurance(clock_domain_id="other"),), 11000),
    ((assurance(),), 100), ((assurance(),), 15000),
    ((assurance(state="REVOKED"),), 11000),
    ((assurance(), assurance(state="REVOKED")), 11000),
    ((assurance(), assurance(state="REVOKED"), assurance()), 11000),
])
def test_no_missing_ambiguous_revoked_expired_or_cross_clock_clearance(records, now):
    with pytest.raises(ValueError):
        adapt(records, now_monotonic_ms=now)


@pytest.mark.parametrize("wall_time", ["1900-01-01T00:00:00Z", "2200-01-01T00:00:00Z"])
def test_wall_time_is_audit_only_and_never_revives_assurance(wall_time):
    assert adapt((assurance(assured_at=wall_time),)).navigation_contingency_expires_monotonic_ms == 15000
    with pytest.raises(ValueError):
        adapt((assurance(assured_at=wall_time),), now_monotonic_ms=15000)


def test_recovery_never_uses_pre_action_frames_after_yaw():
    recovery, value, clock = controller()
    first = recovery.next_action(value.health, environment(), ())
    recovery.assess_burst(first, ())
    old = tuple(replace(report, fix=replace(report.fix, heading_deg=60)) for report in reports(clock))
    clock.now = 10.5
    second = recovery.next_action(value.health, environment(), ())
    assert recovery.assess_burst(second, old).reason == "untrusted_report"
    assert recovery.assess_burst(second, old).reason == "unknown_or_consumed_action"


def test_stale_pose_argument_cannot_enable_yaw_after_tracker_loses_integrity():
    recovery, value, clock = controller()
    stale = value.health
    first = recovery.next_action(stale, environment(), ())
    recovery.assess_burst(first, ())
    value.accept_fix(fix(clock, heading_deg=180))
    assert recovery.next_action(stale, environment(), ()).kind == "CONTINGENCY"


@pytest.mark.parametrize("dimension", ["time", "yaw", "uncertainty", "clearance_deadline"])
def test_recovery_admits_entire_bounded_action_before_limits(dimension):
    recovery, value, clock = controller()
    first = recovery.next_action(value.health, environment(), ())
    recovery.assess_burst(first, ())
    env = environment()
    if dimension == "time":
        clock.now = 17.5
    elif dimension == "yaw":
        value.profile = replace(value.profile, max_yaw_deg=20)
        env = replace(env, mapped_heading_tag_ids=((60, (1, 2, 3)),))
    elif dimension == "uncertainty":
        value.profile = replace(value.profile, xy_drift_cm_s=25)
    else:
        env = replace(env, navigation_contingency_expires_monotonic_ms=10500)
    assert recovery.next_action(value.health, env, ()).kind in {"CONTINGENCY", "EMERGENCY"}


def test_near_canonical_is_not_silently_treated_as_restored():
    recovery, value, clock = controller()
    first = recovery.next_action(value.health, environment(), ())
    sample = tuple(replace(report, fix=replace(report.fix, heading_deg=95)) for report in reports(clock))
    assert recovery.assess_burst(first, sample).reason == "restore_canonical_heading"


def test_stateful_room_adapter_poisoned_by_reset_never_revives_assurance():
    from sentinel.room.site import RecoveryEnvironmentAdapter
    from tests.cube.test_localization import Clock
    clock = Clock(11)
    adapter = RecoveryEnvironmentAdapter(session_id="session", clock=clock)
    records = (assurance(clock_domain_id=adapter.clock_domain_id),)
    def build():
        return adapter.adapt(site(), records, navigation_contingency_id="NAVIGATION_UNAVAILABLE",
                             mapped_heading_tag_ids=environment().mapped_heading_tag_ids)
    assert build().navigation_contingency_expires_monotonic_ms == 15000
    clock.now = 14
    build()
    clock.now = 12  # still later than issuance; reset must nevertheless be permanent
    with pytest.raises(ValueError, match="clock"):
        build()
    clock.now = 14
    with pytest.raises(ValueError, match="clock"):
        build()
    restarted = RecoveryEnvironmentAdapter(session_id="session", clock=clock)
    assert restarted.clock_domain_id != adapter.clock_domain_id
    with pytest.raises(ValueError):
        restarted.adapt(site(), records, navigation_contingency_id="NAVIGATION_UNAVAILABLE",
                        mapped_heading_tag_ids=environment().mapped_heading_tag_ids)


@pytest.mark.parametrize("fault", ["lost", "energy", "pose", "outside", "expired", "wrong_trigger"])
def test_room_contingency_admission_requires_current_pose_energy_clearance_and_geometry(fault):
    from sentinel.room.site import admit_terminal_contingency
    _, value, clock = controller()
    pose = value.health
    if fault == "lost":
        pose = replace(pose, state=LocalizationState.LOST)
    if fault == "pose":
        pose = replace(pose, state=LocalizationState.RECOVERY_REQUIRED)
    if fault == "outside":
        pose = replace(pose, position=Position(500, 100, 100))
    records, context = terminal_context(value)
    result = admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE if fault == "wrong_trigger" else ContingencyTrigger.NAVIGATION_UNAVAILABLE,
        **context, now_monotonic_ms=15000 if fault == "expired" else 11000,
        pose=pose, energy_available=fault != "energy")
    assert result is None


def test_room_contingency_admission_resolves_the_matching_current_landing_policy():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    result = admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=value.health, energy_available=True)
    assert result == site().terminal_contingencies[0]


def test_return_energy_contingency_is_separate_and_admissible_with_its_own_assurance():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    records = (replace(records[0], contingency_id="RETURN_ENERGY_UNAVAILABLE"),)
    result = admit_terminal_contingency(site(), records,
        contingency_id="RETURN_ENERGY_UNAVAILABLE", trigger=ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE,
        **context, now_monotonic_ms=11000,
        pose=value.health, energy_available=True)
    assert result == site().terminal_contingencies[1]


def test_report_captured_after_action_bound_is_not_a_successful_reanchor():
    recovery, value, clock = controller()
    action = recovery.next_action(value.health, environment(), ())
    clock.now += action.upper_bound_s + 0.1
    assert not recovery.assess_burst(action, reports(clock)).accepted


def test_terminal_landing_requires_entire_descent_inside_surveyed_volume():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    elevated = replace(site(), free_space_volumes=(Volume3D("free", POLYGON, (50, 200)),),
                       home=HomeDefinition("pad", Position(100, 100, 50), 90, "home-route"))
    records, context = terminal_context(value)
    assert admit_terminal_contingency(elevated, records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=value.health, energy_available=True) is None


def test_rotation_swept_clearance_includes_calibrated_height_error():
    recovery, value, _ = controller()
    env = replace(environment(), rotation_clearance_volumes=(RecoveryVolume("thin", POLYGON, (99, 101)),))
    first = recovery.next_action(value.health, env, ())
    recovery.assess_burst(first, ())
    assert recovery.next_action(value.health, env, ()).kind == "CONTINGENCY"


def test_supervisor_lost_pose_cannot_be_upgraded_by_an_older_anchored_tracker():
    recovery, value, _ = controller()
    assert recovery.next_action(replace(value.health, state=LocalizationState.LOST), environment(), ()).kind == "CONTINGENCY"


@pytest.mark.parametrize("fault", ["empty_clearance", "hover_drift", "height_clearance", "energy"])
def test_stationary_recovery_admission_requires_the_complete_safe_hover(fault):
    recovery, value, _ = controller()
    env = environment()
    if fault == "empty_clearance":
        env = replace(env, rotation_clearance_volumes=())
    elif fault == "hover_drift":
        value.profile = replace(value.profile, xy_drift_cm_s=25)
    elif fault == "height_clearance":
        env = replace(env, rotation_clearance_volumes=(RecoveryVolume("thin", POLYGON, (99, 101)),))
    else:
        recovery.energy_admits = lambda action: False
    assert recovery.next_action(value.health, env, ()).kind == "CONTINGENCY"


@pytest.mark.parametrize("change", [
    {"position": Position(140, 100, 100)}, {"heading_deg": 140}, {"position": Position(100, 100, 130)},
])
def test_coherent_mapped_contradictory_burst_latches_lost_without_exploratory_yaw(change):
    recovery, value, clock = controller()
    action = recovery.next_action(value.health, environment(), ())
    sample = tuple(replace(report, fix=replace(report.fix, **change)) for report in reports(clock))
    outcome = recovery.assess_burst(action, sample)
    assert not outcome.accepted and not outcome.events
    assert value.health.state == LocalizationState.LOST
    assert recovery.next_action(value.health, environment(), ()).kind == "CONTINGENCY"


def test_one_contradictory_outlier_is_noisy_evidence_not_a_coherent_contradiction():
    recovery, value, clock = controller()
    action = recovery.next_action(value.health, environment(), ())
    sample = list(reports(clock))
    sample[0] = replace(sample[0], fix=replace(sample[0].fix, position=Position(140, 100, 100)))
    assert not recovery.assess_burst(action, tuple(sample)).accepted
    assert value.health.state != LocalizationState.LOST


@pytest.mark.parametrize("refresh_pending", [False, True])
def test_newer_accepted_fix_supersedes_recovery_action_before_persistence(refresh_pending):
    recovery, value, clock = controller()
    committed = []
    recovery.commit_reanchor = committed.append
    action = recovery.next_action(value.health, environment(), ())
    clock.now = 10.2
    old_reports = reports(clock)
    clock.now = 10.5
    newest = value.accept_fix(fix(clock, position=Position(110, 100, 100)))
    if refresh_pending:
        recovery.next_action(value.health, environment(), ())
    outcome = recovery.assess_burst(action, old_reports)
    assert not outcome.accepted and not outcome.events
    assert value.health == newest
    assert not committed


@pytest.mark.parametrize("fault", ["missing", "unvalidated", "missing_evidence"])
def test_terminal_admission_without_validated_aircraft_dimensions_fails_closed(fault):
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    context["drift_profile"] = {"missing": None, "unvalidated": replace(value.profile, validated=False),
                                "missing_evidence": replace(value.profile, evidence_refs=())}[fault]
    assert admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=value.health, energy_available=True) is None


@pytest.mark.parametrize("x,allowed", [(5, False), (15, False), (16, True)])
def test_terminal_admission_rejects_center_inside_but_rotor_footprint_outside(x, allowed):
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    near_wall = replace(value.health, position=Position(x, 100, 100))
    records, context = terminal_context(value)
    assert (admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=near_wall, energy_available=True) is not None) is allowed


def test_terminal_clock_regression_cannot_revive_expired_assurance():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    admitted = [admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=now, pose=value.health, energy_available=True) is not None
        for now in (11000, 16000, 12000, 14000)]
    assert admitted == [True, False, False, False]


def test_terminal_admission_without_session_owned_clock_guard_fails_closed():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    del context["clock_guard"]
    assert admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=value.health, energy_available=True) is None


def test_terminal_clock_poisoning_is_scoped_to_the_session_and_clock_domain():
    from sentinel.room.site import admit_terminal_contingency, SessionClockGuard
    _, value, _ = controller()
    records, context = terminal_context(value)
    context["clock_guard"].observe(16000, session_id="session", clock_domain_id=context["clock_domain_id"])
    context["clock_guard"].observe(12000, session_id="session", clock_domain_id=context["clock_domain_id"])
    for session in ("session", "other-session"):
        fresh_guard = SessionClockGuard(session_id=session)
        fresh = (assurance(session_id=session, clock_domain_id=fresh_guard.clock_domain_id),)
        kwargs = {**context, "session_id": session, "clock_domain_id": fresh_guard.clock_domain_id, "clock_guard": fresh_guard}
        assert admit_terminal_contingency(site(), fresh, contingency_id="NAVIGATION_UNAVAILABLE",
            trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **kwargs,
            now_monotonic_ms=11000, pose=value.health, energy_available=True) is not None
        assert admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
            trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **kwargs,
            now_monotonic_ms=11000, pose=value.health, energy_available=True) is None


def test_terminal_and_recovery_adapters_share_the_same_poisoned_clock_guard():
    from sentinel.room.site import admit_terminal_contingency, RecoveryEnvironmentAdapter
    _, value, clock = controller()
    adapter = RecoveryEnvironmentAdapter(session_id="session", clock=clock)
    records = (assurance(clock_domain_id=adapter.clock_domain_id),)
    adapter.adapt(site(), records, navigation_contingency_id="NAVIGATION_UNAVAILABLE",
                  mapped_heading_tag_ids=environment().mapped_heading_tag_ids)
    assert admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, session_id="session", clock_domain_id=adapter.clock_domain_id,
        clock_guard=adapter.clock_guard, drift_profile=value.profile,
        now_monotonic_ms=16000, pose=value.health, energy_available=True) is None
    clock.now = 12
    with pytest.raises(ValueError, match="clock"):
        adapter.adapt(site(), records, navigation_contingency_id="NAVIGATION_UNAVAILABLE",
                      mapped_heading_tag_ids=environment().mapped_heading_tag_ids)


def test_full_aircraft_descent_footprint_must_fit_free_space_even_with_wide_landing_polygon():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    narrow_free = replace(site(), free_space_volumes=(Volume3D("free", ((95, 0), (200, 0), (200, 200), (95, 200)), (-20, 220)),))
    assert admit_terminal_contingency(narrow_free, records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=value.health, energy_available=True) is None


@pytest.mark.parametrize("pending_kind", ["STATIONARY_BURST", "YAW_BURST"])
def test_next_action_cancels_superseded_work_and_rechecks_the_new_pose_clearance(pending_kind):
    recovery, value, clock = controller()
    env = replace(environment(), rotation_clearance_volumes=(RecoveryVolume(
        "narrow", ((75, 75), (125, 75), (125, 125), (75, 125)), (0, 200)),))
    pending = recovery.next_action(value.health, env, ())
    if pending_kind == "YAW_BURST":
        recovery.assess_burst(pending, ())
        pending = recovery.next_action(value.health, env, ())
    assert pending.kind == pending_kind and pending.pose_revision == 1
    clock.now = 10.5
    newest = value.accept_fix(fix(clock, position=Position(110, 100, 100)))
    assert newest.pose_revision == 2
    result = recovery.next_action(newest, env, ())
    assert result.kind == "CONTINGENCY"
    assert result.action_id != pending.action_id
    assert not recovery.assess_burst(pending, reports(clock)).accepted
    assert value.health == newest


def test_next_action_rebinds_safe_recovery_after_a_new_fix_without_extending_attempt_budget():
    recovery, value, clock = controller()
    pending = recovery.next_action(value.health, environment(), ())
    clock.now = 10.5
    newest = value.accept_fix(fix(clock, position=Position(110, 100, 100)))
    current = recovery.next_action(newest, environment(), ())
    assert current.kind == "STATIONARY_BURST"
    assert current.action_id != pending.action_id
    assert current.pose_revision == newest.pose_revision and current.measured_at == newest.measured_at
    recovery.max_attempts = 2  # the superseded admission still consumed its bounded attempt
    recovery.assess_burst(current, ())
    assert recovery.next_action(value.health, environment(), ()).kind == "CONTINGENCY"


def test_adapter_and_terminal_share_normalized_milliseconds_at_fractional_clock_instants():
    from sentinel.room.site import admit_terminal_contingency, RecoveryEnvironmentAdapter
    _, value, clock = controller()
    adapter = RecoveryEnvironmentAdapter(session_id="session", clock=clock)
    records = (assurance(clock_domain_id=adapter.clock_domain_id),)
    for now, normalized in ((11.0005, 11000), (12.0, 12000)):
        clock.now = now
        adapter.adapt(site(), records, navigation_contingency_id="NAVIGATION_UNAVAILABLE",
                      mapped_heading_tag_ids=environment().mapped_heading_tag_ids)
        assert admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
            trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, session_id="session", clock_domain_id=adapter.clock_domain_id,
            clock_guard=adapter.clock_guard, drift_profile=value.profile,
            now_monotonic_ms=normalized, pose=value.health, energy_available=True) is not None


@pytest.mark.parametrize("before,after", [(11.0005, 11.0004), (11.0011, 11.0009)])
def test_actual_submillisecond_source_clock_regression_still_poisons_shared_guard(before, after):
    from sentinel.room.site import admit_terminal_contingency, RecoveryEnvironmentAdapter
    _, value, clock = controller()
    adapter = RecoveryEnvironmentAdapter(session_id="session", clock=clock)
    records = (assurance(clock_domain_id=adapter.clock_domain_id),)
    clock.now = before
    adapter.adapt(site(), records, navigation_contingency_id="NAVIGATION_UNAVAILABLE",
                  mapped_heading_tag_ids=environment().mapped_heading_tag_ids)
    clock.now = after
    with pytest.raises(ValueError, match="clock"):
        adapter.adapt(site(), records, navigation_contingency_id="NAVIGATION_UNAVAILABLE",
                      mapped_heading_tag_ids=environment().mapped_heading_tag_ids)
    assert admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, session_id="session", clock_domain_id=adapter.clock_domain_id,
        clock_guard=adapter.clock_guard, drift_profile=value.profile,
        now_monotonic_ms=12000, pose=value.health, energy_available=True) is None


def test_clock_guard_normalizes_fractional_and_integer_observations_to_one_tick_precision():
    from sentinel.room.site import SessionClockGuard
    guard = SessionClockGuard(session_id="session")
    bindings = {"session_id": guard.session_id, "clock_domain_id": guard.clock_domain_id}
    assert guard.observe(11000.5, **bindings)
    assert guard.observe(11000, **bindings)  # same floor-normalized clock tick
    assert guard.observe(12000, **bindings)
    assert not guard.observe(11999.9, **bindings)


@pytest.mark.parametrize("change", ["expired", "exact_deadline", "energy", "clearance", "projected_drift"])
def test_pending_action_is_readmitted_against_its_deadline_and_current_conditions(change):
    recovery, value, clock = controller()
    env = environment()
    pending = recovery.next_action(value.health, env, ())
    if change in {"expired", "exact_deadline"}:
        clock.now = 11.1 if change == "expired" else 11
    elif change == "energy":
        recovery.energy_admits = lambda action: False
    elif change == "clearance":
        env = replace(env, rotation_clearance_volumes=())
    else:
        value.profile = replace(value.profile, xy_drift_cm_s=25)
    current = recovery.next_action(value.health, env, ())
    assert current.action_id != pending.action_id
    if change not in {"expired", "exact_deadline"}:
        assert current.kind == "CONTINGENCY"
    else:
        assert current.kind == "STATIONARY_BURST"
        assert not recovery.assess_burst(pending, reports(clock)).accepted
        recovery.max_attempts = 2
        recovery.assess_burst(current, ())
        assert recovery.next_action(value.health, env, ()).kind == "CONTINGENCY"


def yaw_recovery(*, max_yaw=180):
    recovery, value, clock = controller()
    value.profile = replace(value.profile, max_yaw_deg=max_yaw)
    first = recovery.next_action(value.health, environment(), ())
    recovery.assess_burst(first, ())
    yaw = recovery.next_action(value.health, environment(), ())
    assert yaw.kind == "YAW_BURST" and yaw.heading_deg == 60
    sample = tuple(replace(report, fix=replace(report.fix, heading_deg=60)) for report in reports(clock))
    return recovery, value, clock, yaw, sample


def test_verified_exploratory_yaw_updates_history_and_blocks_restore_beyond_cumulative_limit():
    recovery, value, _, yaw, sample = yaw_recovery(max_yaw=40)
    before = value.health
    assert recovery.assess_burst(yaw, sample).reason == "restore_canonical_heading"
    rotated = value.health
    assert rotated.heading_deg == pytest.approx(60) and rotated.yaw_deg == 30
    assert rotated.translation_cm == before.translation_cm
    assert rotated.xy_uncertainty_cm > before.xy_uncertainty_cm
    assert rotated.heading_uncertainty_deg > before.heading_uncertainty_deg
    assert not recovery.assess_burst(yaw, sample).accepted
    assert value.health == rotated
    assert recovery.next_action(value.health, environment(), ()).kind == "CONTINGENCY"


def test_exploratory_yaw_already_propagated_by_executor_is_not_counted_twice():
    from tests.cube.test_localization import command
    recovery, value, clock, yaw, sample = yaw_recovery()
    value.note_command(command("ROTATE", degrees=30, direction="cw"), clock.now)
    recovery.assess_burst(yaw, sample)
    assert value.health.heading_deg == pytest.approx(60) and value.health.yaw_deg == 30


def test_restore_yaw_is_accounted_once_even_when_reanchor_commit_retries():
    recovery, value, clock, yaw, sample = yaw_recovery()
    recovery.assess_burst(yaw, sample)
    restore = recovery.next_action(value.health, environment(), ())
    assert restore.kind == "RESTORE_CANONICAL"
    histories = []
    def commit(outcome):
        histories.append((value.health.heading_deg, value.health.yaw_deg))
        if len(histories) == 1:
            raise RuntimeError("retry commit")
    recovery.commit_reanchor = commit
    with pytest.raises(RuntimeError, match="retry commit"):
        recovery.assess_burst(restore, reports(clock))
    assert value.health.yaw_deg == 60
    assert recovery.assess_burst(restore, reports(clock)).accepted
    assert histories == [(90, 60), (90, 60)]
    assert value.health.yaw_deg == 0


@pytest.mark.parametrize("fault", ["hover_band", "ceiling", "touchdown"])
def test_terminal_descent_contains_full_calibrated_vertical_uncertainty(fault):
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    base = site()
    if fault == "hover_band":
        base = replace(base, terminal_contingencies=tuple(replace(item, z_band_cm=(99, 101))
                                                       for item in base.terminal_contingencies))
    else:
        band = (-20, 105) if fault == "ceiling" else (-5, 220)
        base = replace(base, free_space_volumes=(Volume3D("free", POLYGON, band),))
    assert admit_terminal_contingency(base, records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=value.health, energy_available=True) is None


@pytest.mark.parametrize("field,bad", [("xy_uncertainty_cm", None), ("xy_uncertainty_cm", "1"),
    ("xy_uncertainty_cm", -1), ("heading_uncertainty_deg", True),
    ("max_height_error_cm", None), ("max_height_error_cm", -1), ("max_height_error_cm", float("nan"))])
def test_terminal_missing_or_invalid_uncertainty_is_refused_without_exception(field, bad):
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    pose = value.health
    if field == "max_height_error_cm":
        damaged = replace(value.profile)
        object.__setattr__(damaged, field, bad)  # damaged persisted/provider object bypassed constructor
        context["drift_profile"] = damaged
    else:
        pose = replace(pose, **{field: bad})
    assert admit_terminal_contingency(site(), records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=pose, energy_available=True) is None


def test_terminal_descent_vertical_uncertainty_cannot_intersect_a_forbidden_volume():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    records, context = terminal_context(value)
    base = replace(site(), forbidden_volumes=(Volume3D("below-touchdown",
        ((90, 90), (110, 90), (110, 110), (90, 110)), (-15, -5)),))
    assert admit_terminal_contingency(base, records, contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, **context,
        now_monotonic_ms=11000, pose=value.health, energy_available=True) is None


@pytest.mark.parametrize("refs", [("",), ("  ",), ("line\nbreak",), ("two words",),
    ("evidence/../escape",), (None,), (123,)])
def test_assurance_requires_each_evidence_reference_to_be_a_safe_nonblank_identifier(refs):
    with pytest.raises(ValueError, match="evidence"):
        assurance(evidence_refs=refs)


def test_assurance_evidence_identifiers_are_normalized_at_construction():
    assert assurance(evidence_refs=(" evidence:frame-1 ",)).evidence_refs == ("evidence:frame-1",)


def test_adapter_revalidates_evidence_references_on_restored_assurance_objects():
    damaged = assurance()
    object.__setattr__(damaged, "evidence_refs", ("",))
    with pytest.raises(ValueError, match="evidence"):
        adapt((damaged,))


@pytest.mark.parametrize("changes", [{"heading_deg": "60"}, {"xy_uncertainty_cm": None},
    {"position": Position("100", 100, 100)}, {"position": None}, {"tag_ids": ([], 2)}])
def test_malformed_report_fix_is_refused_without_losing_integrity_or_raising(changes):
    recovery, value, clock = controller()
    action = recovery.next_action(value.health, environment(), ())
    sample = list(reports(clock))
    sample[0] = replace(sample[0], fix=replace(sample[0].fix, **changes))
    result = recovery.assess_burst(action, tuple(sample))
    assert not result.accepted and not result.events
    assert value.health.state != LocalizationState.LOST


def test_malformed_report_identity_is_a_controlled_rejection():
    recovery, value, clock = controller()
    action = recovery.next_action(value.health, environment(), ())
    malformed = reports(clock)[0]
    object.__setattr__(malformed, "report_id", ["invalid"])
    sample = (malformed,) + reports(clock)[1:]
    assert not recovery.assess_burst(action, sample).accepted


INVALID_HEADING_MAPS = [
    None, (), [], [(90, (1, 2))], "90", ([],), ((90,),),
    ([90, (1, 2)],), (([90], (1, 2)),), (("90", (1, 2)),),
    ((float("nan"), (1, 2)),), ((float("inf"), (1, 2)),),
    ((float("-inf"), (1, 2)),), ((True, (1, 2)),),
    ((90, ()),), ((90, [1, 2]),), ((90, "12"),), ((90, ([1], 2)),),
    ((90, (True, 2)),), ((90, (-1, 2)),), ((90, (1.0, 2)),),
    ((90, ("1", 2)),), ((90, (1, 1)),),
    ((90, (1, 2)), (90.0, (2, 3))), ((0, (1, 2)), (360, (2, 3))),
]


@pytest.mark.parametrize("mapping", INVALID_HEADING_MAPS)
@pytest.mark.parametrize("boundary", ["environment", "room_adapter"])
def test_heading_tag_map_rejects_malformed_input_at_both_boundaries(mapping, boundary):
    with pytest.raises(ValueError):
        if boundary == "environment":
            replace(environment(), mapped_heading_tag_ids=mapping)
        else:
            adapt((assurance(),), mapped_heading_tag_ids=mapping)


def test_heading_tag_map_has_one_immutable_canonical_representation():
    mapping = ((450, (2, 1)), (-30.5, (4, 0)), (720, (3,)))
    expected = ((0.0, (3,)), (90.0, (1, 2)), (329.5, (0, 4)))
    direct = replace(environment(), mapped_heading_tag_ids=mapping)
    adapted = adapt((assurance(),), mapped_heading_tag_ids=mapping)
    assert direct.mapped_heading_tag_ids == adapted.mapped_heading_tag_ids == expected
    assert all(type(heading) is float for heading, _ in direct.mapped_heading_tag_ids)
    with pytest.raises(AttributeError):
        direct.mapped_heading_tag_ids = mapping


@pytest.mark.parametrize("heading,expected", [(9007199254740993, 33.0), (-1e-300, 0.0)])
def test_heading_normalization_preserves_integer_angle_and_canonical_float_range(heading, expected):
    direct = replace(environment(), mapped_heading_tag_ids=((heading, (1, 2)),))
    assert direct.mapped_heading_tag_ids == ((expected, (1, 2)),)


@pytest.mark.parametrize("mapping", INVALID_HEADING_MAPS)
def test_recovery_rechecks_restored_heading_map_before_any_action(mapping):
    recovery, value, _ = controller()
    env = environment()
    stationary = recovery.next_action(value.health, env, ())
    recovery.assess_burst(stationary, ())
    # Model an old deserializer that bypasses frozen dataclass validation.
    object.__setattr__(env, "mapped_heading_tag_ids", mapping)
    result = recovery.next_action(value.health, env, ())
    assert result.kind == "CONTINGENCY"
    assert result.reason == "invalid_mapped_heading_tags"


@pytest.mark.parametrize("field", ["session_id", "clock_domain_id"])
def test_invalid_heading_map_cannot_bypass_session_or_clock_change(field):
    recovery, value, _ = controller()
    env = environment()
    recovery.next_action(value.health, env, ())
    changed = replace(env, **{field: "another"})
    object.__setattr__(changed, "mapped_heading_tag_ids", [])
    result = recovery.next_action(value.health, changed, ())
    assert result.kind == "EMERGENCY" and result.contingency_id is None
    assert result.reason == "session_or_clock_changed"


@pytest.mark.parametrize("report_id", ["", " ", "frame ", " frame", "frame\n", None, True, ["frame"]])
def test_pose_report_rejects_noncanonical_identity_on_construction(report_id):
    _, _, clock = controller()
    with pytest.raises(ValueError):
        PoseReport(report_id, fix(clock))


def test_pose_report_keeps_canonical_identity_immutable():
    _, _, clock = controller()
    report = PoseReport("frame-1", fix(clock))
    assert report.report_id == "frame-1"
    with pytest.raises(AttributeError):
        report.report_id = "other"


@pytest.mark.parametrize("identities", [("frame", "frame", "frame"), ("frame", " frame", "frame ")])
def test_same_report_and_whitespace_aliases_never_count_as_distinct(identities):
    persisted = []
    recovery, value, clock = controller(commit_reanchor=persisted.append)
    action = recovery.next_action(value.health, environment(), ())
    samples = reports(clock)
    for sample, report_id in zip(samples, identities):
        object.__setattr__(sample, "report_id", report_id)
    revision = value.health.pose_revision
    result = recovery.assess_burst(action, samples)
    assert not result.accepted and not result.events and not persisted
    assert value.health.pose_revision == revision
