"""Plan 06 Task 4 fix round 4 (Q2): the live count-and-return session composition.

Everything here is OFFLINE. The flight stack (executor, telemetry refresh,
emergency landing, frame source), the judgment model and the detector are
fakes; nothing constructs a network, provider or drone client. The fakes are
also imported by ``tests/room/test_cli.py`` so the CLI's own end-to-end tests
drive exactly the same composition.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import time

import pytest

from sentinel.cube.checkpoint import CommandLimits
from sentinel.cube.energy import EnergyProfile, EnergySample
from sentinel.cube.localization import DriftProfile, PoseFix
from sentinel.cube.step_executor import (CheckpointOutcome, CommandState, ExecutorState, HomeState,
                                         TerminalOutcome)
from sentinel.models import Position, Telemetry
from sentinel.room import capabilities, live_session
from sentinel.room.models import CapabilityGrant, CapabilityProfile, canonical_grant_sha256
from sentinel.room.site import ContingencyAssurance, parse_room_site, room_site_document
from sentinel.room.store import RoomStore
from sentinel.room.supervisor import calibration_hashes
from tests.cube.test_localization import profile as drift_profile_fixture

AIRCRAFT_ID = "aircraft-1"
SAFE_LABELS = ("CLARIFICATION_REQUIRED", "UNSUPPORTED", "ESCALATE")


def fixture_site():
    """A flight-valid synthetic site: the supervisor tests' site (its home
    route ends over the pad, as SafetySupervisor._termination requires) with
    the count-return tests' semantic frame and coverage cells. Round-tripped
    through the site document so it is exactly what --site loads."""
    from tests.room.test_count_return import room_site
    return parse_room_site(room_site_document(room_site(coverage=True)))


def write_site(path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(room_site_document(fixture_site())), encoding="utf-8")
    return path


def drift_profile() -> DriftProfile:
    # Generous synthetic envelope: never evidence of a real flight, only a
    # validated-shaped profile the real-mode tracker accepts.
    return drift_profile_fixture(max_camera_age_s=2000, max_fix_age_s=2000, max_translation_cm=2000,
                                 max_xy_uncertainty_cm=2000, max_heading_uncertainty_deg=2000)


def energy_profile(site) -> EnergyProfile:
    return EnergyProfile("energy-fixture", site.cube_site_sha256, AIRCRAFT_ID, .01, 1, 20, 2, "PROMOTED")


def command_limits() -> CommandLimits:
    return CommandLimits(trim_rounds=0, localise=False)


def flight_grant(site, *, capability="count_return", drift=None, energy=None, limits=None):
    """The flight-calibration grant the SafetySupervisor itself verifies:
    bound to calibration_hashes(energy, drift, limits, site)."""
    hashes = calibration_hashes(energy or energy_profile(site), drift or drift_profile(),
                                limits or command_limits(), site)
    grant = CapabilityGrant(capability, "PROMOTED", site.cube_site_sha256, AIRCRAFT_ID, hashes,
                            ("flight-evidence",), "2099-01-01T00:00:00+00:00", "")
    return replace(grant, grant_sha256=canonical_grant_sha256(grant))


class FakeExecutor:
    """A StepExecutor-shaped stand-in: records every command, never touches I/O."""

    def __init__(self, clock, hover):
        self.clock = clock
        self.hover = hover
        self.state = ExecutorState.NEW
        self.home = None
        self.calls: list[str] = []

    def start(self, home_definition):
        self.calls.append("TAKEOFF")
        self.state = ExecutorState.AIRBORNE
        self.home = HomeState(self.hover, home_definition.heading_deg, home_definition.route_id, self.clock())
        return self.home

    def execute(self, plan):
        self.calls.extend(c.kind for c in plan.commands)
        states = tuple(CommandState(c, "COMPLETED", 0.0, 1.0) for c in plan.commands)
        return CheckpointOutcome("COMPLETED", states, ("observation",), Telemetry(76, 100), plan.target, True)

    def return_home(self, plan):
        self.state = ExecutorState.RETURNING
        return self.execute(plan)

    def apply_reanchor(self, pose, *, persist):
        persist()

    def land(self, *, upper_bound_s=None, deadline=None):
        self.calls.append("LAND")
        self.state = ExecutorState.TERMINAL
        return TerminalOutcome("LANDED", "REQUESTED", Position(self.hover.x_cm, self.hover.y_cm, 0), 75,
                               self.clock())


class FakeFrames:
    def read_frame(self, timeout=None):  # pragma: no cover - OBSERVE is never offered in REAL mode
        raise AssertionError("no frame is read in this build's REAL-mode session")


class FlightStackFactory:
    """Build the fake flight stack exactly as a real adapter would: bound to the
    session's own tracker/clock/site, seeding the tracker with one measured fix."""

    def __init__(self, *, start_error: Exception | None = None, airborne_before_error: bool = False):
        self.calls = []
        self.executor = None
        self.emergencies = []
        self.start_error = start_error
        self.airborne_before_error = airborne_before_error

    def __call__(self, *, tracker, clock, site):
        self.calls.append({"tracker": tracker, "clock": clock, "site": site})
        # The measured hover pose over the pad: the home route's last checkpoint.
        route = next(r for r in site.routes if r.route_id == site.home.route_id)
        hover = route.checkpoints[-1]
        tracker.accept_fix(PoseFix(Position(hover.x_cm, hover.y_cm, hover.z_cm), site.home.heading_deg,
                                   clock(), 1, 1, (1, 2)))
        executor = FakeExecutor(clock, hover)
        if self.start_error is not None:
            def failing_start(home_definition, error=self.start_error, airborne=self.airborne_before_error):
                executor.calls.append("TAKEOFF_ATTEMPT")
                if airborne:
                    executor.state = ExecutorState.AIRBORNE
                raise error
            executor.start = failing_start
        self.executor = executor

        def refresh():
            return tracker.health, EnergySample(80, clock())

        def emergency(active_executor):
            self.emergencies.append(True)
            active_executor.state = ExecutorState.TERMINAL
            return TerminalOutcome("EMERGENCY_ABORTED", "NAVIGATION_UNAVAILABLE", None, None, clock())

        return live_session.FlightStack(executor=executor, refresh=refresh, emergency=emergency,
                                        frame_source=FakeFrames())


class PolicyJudge:
    """A deterministic judgment model for both goal compilation and room turns.

    Goal compilation: COUNT for the goal family, otherwise the first
    controller-offered (non-safe) label. Room turns: prefer an ANSWER
    candidate, otherwise the first offered candidate."""

    def __init__(self, identity="jev-vendor-x", *, clarify=False):
        self.identity = identity
        self.clarify = clarify
        self.names: list[str] = []

    def choice(self, state, instruction, *, criteria, name):
        self.names.append(name)
        offered = [label for label in criteria if label not in SAFE_LABELS]
        if self.clarify and "CLARIFICATION_REQUIRED" in criteria:
            pick = "CLARIFICATION_REQUIRED"
        elif name == "goal_family":
            pick = "COUNT"
        elif name == "room_action":
            answers = [label for label in offered if label.startswith("ANSWER")]
            pick = answers[0] if answers else offered[0]
        else:
            pick = offered[0]
        return {"value": pick, "probabilities": {label: .99 if label == pick else .001 for label in criteria}}


def operator_assurances(run_id, site, *, clock_domain_id="cd-operator", now_ms=None):
    now_ms = int(time.monotonic() * 1000) if now_ms is None else now_ms
    return tuple(
        ContingencyAssurance(f"a-{entry.contingency_id}", entry.contingency_id, run_id, clock_domain_id,
                             "CURRENT", "2026-09-23T12:00:00+00:00", now_ms - 100, now_ms + 10 ** 9,
                             ("ev-1",))
        for entry in site.terminal_contingencies)


# ---------------------------------------------------------------------------
# loaders: calibration evidence files, never synthesized constants
# ---------------------------------------------------------------------------


def test_load_drift_profile_round_trips_a_json_document(tmp_path):
    profile = drift_profile()
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(asdict(profile)), encoding="utf-8")
    assert live_session.load_drift_profile(path) == profile


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("xy_drift_cm_s"),
    lambda d: d.update(extra_field=1),
    lambda d: d.update(schema_version="pose-drift-v0"),
])
def test_load_drift_profile_refuses_a_malformed_document(tmp_path, mutate):
    document = asdict(drift_profile())
    mutate(document)
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(live_session.LiveSessionError):
        live_session.load_drift_profile(path)


def test_load_energy_profile_round_trips_and_refuses_malformed(tmp_path):
    site = fixture_site()
    profile = energy_profile(site)
    path = tmp_path / "energy.json"
    path.write_text(json.dumps(asdict(profile)), encoding="utf-8")
    assert live_session.load_energy_profile(path) == profile
    path.write_text(json.dumps({**asdict(profile), "execution_scope": "BOGUS"}), encoding="utf-8")
    with pytest.raises(live_session.LiveSessionError):
        live_session.load_energy_profile(path)


def test_load_command_limits_defaults_and_overrides(tmp_path):
    assert live_session.load_command_limits(None) == CommandLimits()
    path = tmp_path / "limits.json"
    path.write_text(json.dumps({"trim_rounds": 0, "localise": False}), encoding="utf-8")
    assert live_session.load_command_limits(path) == command_limits()
    path.write_text(json.dumps({"not_a_limit": 1}), encoding="utf-8")
    with pytest.raises(live_session.LiveSessionError):
        live_session.load_command_limits(path)


def test_session_capabilities_keeps_only_this_manifests_flight_capable_grants():
    site = fixture_site()
    keep = flight_grant(site)
    other_site = replace(keep, site_sha256="0" * 64)
    other_site = replace(other_site, grant_sha256=canonical_grant_sha256(other_site))
    simulation = replace(keep, state="SIMULATION_ONLY")
    simulation = replace(simulation, grant_sha256=canonical_grant_sha256(simulation))
    registry = capabilities.GrantRegistry(schema_version=capabilities.SCHEMA_VERSION,
                                          grants=(keep, other_site, simulation))

    class Manifest:
        requested_capability = "count_return"
        site_sha256 = site.cube_site_sha256
        aircraft_id = AIRCRAFT_ID

    profile = live_session.session_capabilities(registry, Manifest())
    assert profile.grants == (keep,)


def test_adopt_assurances_rekeys_verified_evidence_without_renewing_any_deadline(tmp_path):
    site = fixture_site()
    store = RoomStore.open(tmp_path / "room.sqlite3")
    try:
        store.create_session("run-0001", "request", CapabilityProfile(()))
        operator = operator_assurances("run-0001", site)
        adopted = live_session.adopt_assurances(store, session_id="run-0001", clock_domain_id="cd-session",
                                                assurances=operator)
        stored = store.contingency_assurances("run-0001", clock_domain_id="cd-session")
        assert stored == adopted
        for original, copy in zip(operator, adopted):
            assert copy.clock_domain_id == "cd-session"
            assert copy.assurance_id != original.assurance_id
            assert (copy.assured_monotonic_ms, copy.expires_monotonic_ms) == (
                original.assured_monotonic_ms, original.expires_monotonic_ms)
            assert f"operator-assurance:{original.assurance_id}" in copy.evidence_refs
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Task B item 2c: a stack with a recovery adapter gives the supervisor one
# ---------------------------------------------------------------------------


def _prepare_with(tmp_path, stack_factory):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    site = fixture_site()
    store = RoomStore.open(tmp_path / "room.sqlite3")
    manifest = SimpleNamespace(run_id="run-0001", aircraft_id=AIRCRAFT_ID, requested_capability="count_return",
                               mode="live", requested_detector_identity="detector", goal_parser_revision="rev-1")
    store.create_session(manifest.run_id, "request", CapabilityProfile((flight_grant(site),)))
    prepared = live_session._prepare(
        store=store, manifest=manifest, site=site, drift_profile=drift_profile(), energy_profile=energy_profile(site),
        limits=command_limits(), calibration_profiles=(SimpleNamespace(speeds_cm_s=(20,)),),
        flight_stack_factory=stack_factory, judge=PolicyJudge(), detector=object(),
        assurances=operator_assurances(manifest.run_id, site), request_text="count the computers",
        frames_dir=tmp_path / "frames", clock=time.monotonic,
        wall_clock=lambda: datetime(2026, 9, 23, tzinfo=timezone.utc), landing_upper_bound_s=10.0)
    return store, prepared


def test_a_stack_with_a_recovery_adapter_gives_the_supervisor_its_recovery_policy(tmp_path):
    inner = FlightStackFactory()

    def with_recovery(**kwargs):
        stack = inner(**kwargs)
        return replace(stack, recovery_burst=lambda action: (), mapped_heading_tag_ids=((90.0, (0, 2)),))

    store, (supervisor, stack, ledger, collaborators) = _prepare_with(tmp_path, with_recovery)
    try:
        assert supervisor.recovery is not None and supervisor.recovery.tracker is supervisor.tracker
        assert supervisor.recovery.burst_source is stack.recovery_burst
        assert supervisor.mapped_heading_tag_ids == ((90.0, (0, 2)),)
    finally:
        store.close()


def test_a_stack_without_a_recovery_adapter_keeps_recovery_unavailable(tmp_path):
    store, (supervisor, *_rest) = _prepare_with(tmp_path, FlightStackFactory())
    try:
        assert supervisor.recovery is None and supervisor.mapped_heading_tag_ids == ()
    finally:
        store.close()


def test_the_live_session_counts_with_the_production_count_geometry(tmp_path):
    from sentinel.camera import TELLO_MEASURED
    from sentinel.room.grounding import CountGeometry
    store, (supervisor, stack, ledger, collaborators) = _prepare_with(tmp_path, FlightStackFactory())
    try:
        geometry = collaborators["count_geometry"]
        assert isinstance(geometry, CountGeometry) and geometry.camera is TELLO_MEASURED
    finally:
        store.close()
