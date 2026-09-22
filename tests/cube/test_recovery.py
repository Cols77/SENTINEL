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
                    (Volume3D("free", POLYGON, (0, 200)),), (),
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
        mapped_heading_tag_ids=environment().mapped_heading_tag_ids, **changes)


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
    result = admit_terminal_contingency(site(), (assurance(),), contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE if fault == "wrong_trigger" else ContingencyTrigger.NAVIGATION_UNAVAILABLE,
        session_id="session", clock_domain_id="clock", now_monotonic_ms=15000 if fault == "expired" else 11000,
        pose=pose, energy_available=fault != "energy")
    assert result is None


def test_room_contingency_admission_resolves_the_matching_current_landing_policy():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    result = admit_terminal_contingency(site(), (assurance(),), contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, session_id="session", clock_domain_id="clock",
        now_monotonic_ms=11000, pose=value.health, energy_available=True)
    assert result == site().terminal_contingencies[0]


def test_return_energy_contingency_is_separate_and_admissible_with_its_own_assurance():
    from sentinel.room.site import admit_terminal_contingency
    _, value, _ = controller()
    result = admit_terminal_contingency(site(), (assurance(contingency_id="RETURN_ENERGY_UNAVAILABLE"),),
        contingency_id="RETURN_ENERGY_UNAVAILABLE", trigger=ContingencyTrigger.RETURN_ENERGY_UNAVAILABLE,
        session_id="session", clock_domain_id="clock", now_monotonic_ms=11000,
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
    assert admit_terminal_contingency(elevated, (assurance(),), contingency_id="NAVIGATION_UNAVAILABLE",
        trigger=ContingencyTrigger.NAVIGATION_UNAVAILABLE, session_id="session", clock_domain_id="clock",
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
