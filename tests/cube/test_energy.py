from __future__ import annotations

import hashlib

import pytest

from sentinel.cube.energy import (
    EnergyEstimate,
    EnergyProfile,
    EnergySample,
    ReserveDecision,
    ReserveGuard,
)
from sentinel.dry_run import DryRunClient


def _profile(**changes: object) -> EnergyProfile:
    values: dict[str, object] = {
        "profile_id": "energy-v1",
        "site_sha256": "a" * 64,
        "aircraft_id": "TELLO-01",
        "percent_per_second_upper": 0.1,
        "telemetry_allowance_pct": 1,
        "landing_floor_pct": 20,
        "sample_max_age_s": 5.0,
        "execution_scope": "SIMULATION_ONLY",
    }
    values.update(changes)
    return EnergyProfile(**values)  # type: ignore[arg-type]


def _sample(battery_pct: int, measured_at_s: float, *, motor_time_s: float | None = None) -> EnergySample:
    return EnergySample(
        battery_pct=battery_pct,
        measured_at_s=measured_at_s,
        motor_time_s=motor_time_s,
        motor_time_source="tello-flight-time" if motor_time_s is not None else None,
    )


def _plan(name: str, seconds: float) -> object:
    return type("Plan", (), {"checkpoint_id": name, "worst_case_s": seconds})()


def _guard(*, now: float = 10.0, **profile_changes: object) -> ReserveGuard:
    return ReserveGuard(_profile(**profile_changes), clock=lambda: now)


def test_reserve_cost_rounds_up_once_and_admits_equality() -> None:
    guard = _guard(percent_per_second_upper=0.1, telemetry_allowance_pct=1, landing_floor_pct=20)

    estimate = guard.assess(
        current=_sample(25, 10), next_plan=_plan("next", 19.1), recovery_plan=_plan("recover", 10),
        provider_wait_s=0.0, termination_plan=_plan("home-and-land", 10),
    )

    # ceil(0.1 * 39.1 + 1) is 5, so equality at the landing floor is safe.
    assert estimate.action_cost_pct == 5
    assert estimate.projected_landing_pct == 20
    assert estimate.decision is ReserveDecision.CONTINUE


def test_return_and_land_contingency_use_the_exact_termination_reserve() -> None:
    guard = _guard(percent_per_second_upper=0.1, telemetry_allowance_pct=1, landing_floor_pct=20)
    common = dict(next_plan=_plan("next", 30), recovery_plan=None, provider_wait_s=0.0,
                  termination_plan=_plan("home-and-land", 10))

    assert guard.assess(current=_sample(24, 10), **common).decision is ReserveDecision.RETURN_NOW
    assert guard.assess(current=_sample(21, 10), **common).decision is ReserveDecision.LAND_CONTINGENCY


def test_components_are_key_sorted_immutable_and_detached_from_source_plan() -> None:
    plan = type("MutablePlan", (), {"worst_case_s": 2.0})()
    guard = _guard(percent_per_second_upper=0.5, telemetry_allowance_pct=0, landing_floor_pct=1)

    estimate = guard.assess(current=_sample(100, 10), next_plan=plan, recovery_plan=None,
                            provider_wait_s=1.0, termination_plan=_plan("home", 1.0))
    before = hashlib.sha256(repr(estimate).encode()).hexdigest()
    plan.worst_case_s = 999.0

    assert estimate.components == tuple(sorted(estimate.components))
    assert dict(estimate.components)["next_checkpoint_s"] == 2.0
    assert hashlib.sha256(repr(estimate).encode()).hexdigest() == before


def test_integer_battery_increase_is_noise_and_never_lowers_the_calibrated_bound() -> None:
    guard = _guard(percent_per_second_upper=0.1)

    guard.observe(_sample(80, 0), _sample(75, 10))
    learned = guard.conservative_percent_per_second
    guard.observe(_sample(75, 10), _sample(76, 20))

    assert learned == 0.5
    assert guard.conservative_percent_per_second == learned


def test_observed_burn_can_raise_the_calibrated_bound() -> None:
    guard = _guard(percent_per_second_upper=0.1)

    guard.observe(_sample(80, 0), _sample(75, 5))

    assert guard.conservative_percent_per_second == 1.0


def test_motor_time_is_cross_checked_against_monotonic_measurement_time() -> None:
    guard = _guard()

    guard.observe(_sample(80, 0, motor_time_s=10), _sample(79, 5, motor_time_s=16))
    estimate = guard.assess(current=_sample(79, 10, motor_time_s=17), next_plan=None,
                            recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_stale_or_regressing_clock_telemetry_fails_closed() -> None:
    stale = _guard(now=16)
    regression_times = iter((10.0, 9.0))
    regressing = ReserveGuard(_profile(), clock=lambda: next(regression_times))
    kwargs = dict(next_plan=None, recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    assert stale.assess(current=_sample(100, 10), **kwargs).decision is ReserveDecision.LAND_CONTINGENCY
    assert regressing.assess(current=_sample(100, 10), **kwargs).decision is ReserveDecision.CONTINUE
    assert regressing.assess(current=_sample(100, 10), **kwargs).decision is ReserveDecision.LAND_CONTINGENCY


def test_return_latch_is_irrevocable() -> None:
    guard = _guard(percent_per_second_upper=0.1, telemetry_allowance_pct=0, landing_floor_pct=20)
    kwargs = dict(next_plan=_plan("next", 30), recovery_plan=None, provider_wait_s=0,
                  termination_plan=_plan("home", 10))
    first = guard.assess(current=_sample(23, 10), **kwargs)
    guard.latch_return(first)

    later = guard.assess(current=_sample(100, 10), **kwargs)

    assert first.decision is ReserveDecision.RETURN_NOW
    assert later.decision is ReserveDecision.RETURN_NOW


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: _sample(True, 0), "battery_pct"),
        (lambda: _sample(101, 0), "battery_pct"),
        (lambda: EnergySample(battery_pct=1, measured_at_s=float("nan")), "measured_at_s"),
        (lambda: _profile(percent_per_second_upper=float("inf")), "percent_per_second_upper"),
        (lambda: _profile(telemetry_allowance_pct=True), "telemetry_allowance_pct"),
    ],
)
def test_profile_and_sample_reject_invalid_constructions(factory, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


def test_provider_wait_must_be_a_finite_nonnegative_number() -> None:
    guard = _guard()
    kwargs = dict(current=_sample(100, 10), next_plan=None, recovery_plan=None, termination_plan=_plan("home", 1))

    for wait in (True, -0.1, float("nan"), float("inf")):
        with pytest.raises((TypeError, ValueError), match="provider_wait_s"):
            guard.assess(provider_wait_s=wait, **kwargs)


def test_real_execution_requires_a_promoted_matching_profile() -> None:
    profile = _profile(execution_scope="PROMOTED")

    profile.validate_for_execution(mode="real", site_sha256="a" * 64, aircraft_id="TELLO-01")
    with pytest.raises(ValueError, match="site"):
        profile.validate_for_execution(mode="real", site_sha256="b" * 64, aircraft_id="TELLO-01")
    with pytest.raises(ValueError, match="PROMOTED"):
        _profile().validate_for_execution(mode="real", site_sha256="a" * 64, aircraft_id="TELLO-01")


def test_dry_run_can_replay_scripted_battery_and_motor_time_sequences() -> None:
    client = DryRunClient(battery_sequence=(25, 24, 21), flight_time_sequence=(10, 15, 20))
    client.connect()

    readings = (client.telemetry(), client.telemetry(), client.telemetry())

    assert [(reading.battery_percent, reading.flight_time_s) for reading in readings] == [
        (25, 10), (24, 15), (21, 20),
    ]
