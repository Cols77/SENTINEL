from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from sentinel.cube.energy import (
    EnergyEstimate,
    EnergyProfile,
    EnergySample,
    ExecutionMode,
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
    common = dict(next_plan=_plan("next", 30), recovery_plan=None, provider_wait_s=0.0,
                  termination_plan=_plan("home-and-land", 10))

    assert _guard(percent_per_second_upper=0.1, telemetry_allowance_pct=1, landing_floor_pct=20).assess(
        current=_sample(24, 10), **common,
    ).decision is ReserveDecision.RETURN_NOW
    assert _guard(percent_per_second_upper=0.1, telemetry_allowance_pct=1, landing_floor_pct=20).assess(
        current=_sample(21, 10), **common,
    ).decision is ReserveDecision.LAND_CONTINGENCY


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


def test_motor_time_is_only_a_consistency_check_not_the_burn_denominator() -> None:
    guard = _guard(now=11, percent_per_second_upper=0.01, telemetry_allowance_pct=0, landing_floor_pct=20)

    guard.observe(_sample(80, 0, motor_time_s=10), _sample(79, 10, motor_time_s=20))
    estimate = guard.assess(current=_sample(79, 11, motor_time_s=21), next_plan=None,
                            recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 10))

    assert guard.conservative_percent_per_second == 0.1
    assert estimate.decision is ReserveDecision.CONTINUE


def test_exact_decimal_reserve_math_does_not_overcharge_a_binary_float_boundary() -> None:
    guard = _guard(percent_per_second_upper=0.07, telemetry_allowance_pct=0, landing_floor_pct=20)

    estimate = guard.assess(current=_sample(27, 10), next_plan=None, recovery_plan=None,
                            provider_wait_s=0.0, termination_plan=_plan("home", 100.0))

    assert estimate.action_cost_pct == 7
    assert estimate.projected_landing_pct == 20
    assert estimate.decision is ReserveDecision.CONTINUE


def test_motor_time_is_cross_checked_against_monotonic_measurement_time() -> None:
    guard = _guard()

    guard.observe(_sample(80, 0, motor_time_s=10), _sample(79, 5, motor_time_s=17))
    estimate = guard.assess(current=_sample(79, 10, motor_time_s=17), next_plan=None,
                            recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


@pytest.mark.parametrize("current_motor_time_s", [10, 11])
def test_integer_motor_time_quantization_accepts_unchanged_or_boundary_crossing_subsecond_samples(
    current_motor_time_s: int,
) -> None:
    guard = _guard(now=10.25)

    guard.observe(_sample(80, 10, motor_time_s=10),
                  _sample(80, 10.25, motor_time_s=current_motor_time_s))

    estimate = guard.assess(current=_sample(80, 10.25, motor_time_s=current_motor_time_s),
                            next_plan=None, recovery_plan=None, provider_wait_s=0,
                            termination_plan=_plan("home", 1))
    assert estimate.decision is ReserveDecision.CONTINUE


def test_motor_time_prolonged_stall_exceeds_its_one_second_quantization_envelope() -> None:
    guard = _guard(now=12)

    guard.observe(_sample(80, 10, motor_time_s=10), _sample(80, 12, motor_time_s=10))

    estimate = guard.assess(current=_sample(80, 12, motor_time_s=10), next_plan=None,
                            recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))
    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_motor_time_regression_is_immediately_invalid_even_within_quantization_envelope() -> None:
    guard = _guard(now=10.25)

    guard.observe(_sample(80, 10, motor_time_s=11), _sample(80, 10.25, motor_time_s=10))

    estimate = guard.assess(current=_sample(80, 10.25, motor_time_s=10), next_plan=None,
                            recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))
    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_motor_time_regression_after_an_epoch_advance_fails_closed() -> None:
    guard = _guard(now=0.5)
    first = _sample(80, 0, motor_time_s=10)
    advanced = _sample(80, 0.25, motor_time_s=11)
    regressed = _sample(80, 0.5, motor_time_s=10)

    guard.observe(first, advanced)
    guard.observe(advanced, regressed)

    estimate = guard.assess(current=regressed, next_plan=None, recovery_plan=None,
                            provider_wait_s=0, termination_plan=_plan("home", 1))
    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_repeated_subsecond_motor_stalls_accumulate_past_the_quantization_envelope() -> None:
    guard = _guard(now=2)
    previous = _sample(80, 0, motor_time_s=10)
    for quarter_second in range(1, 9):
        current = _sample(80, quarter_second / 4, motor_time_s=10)
        guard.observe(previous, current)
        previous = current

    estimate = guard.assess(current=previous, next_plan=None, recovery_plan=None,
                            provider_wait_s=0, termination_plan=_plan("home", 1))
    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_floor_counter_advances_across_fractional_polls_stay_within_the_quantization_envelope() -> None:
    guard = _guard(now=10)
    samples = tuple(
        _sample(80, index / 4, motor_time_s=10 + index // 4)
        for index in range(41)
    )

    for previous, current in zip(samples, samples[1:]):
        guard.observe(previous, current)

    estimate = guard.assess(current=samples[-1], next_plan=None, recovery_plan=None,
                            provider_wait_s=0, termination_plan=_plan("home", 1))
    assert estimate.decision is ReserveDecision.CONTINUE


def test_repeated_fast_motor_ticks_accumulate_past_the_quantization_envelope() -> None:
    guard = _guard(now=2)
    previous = _sample(80, 0, motor_time_s=10)
    for quarter_second in range(1, 9):
        current = _sample(80, quarter_second / 4, motor_time_s=10 + quarter_second)
        guard.observe(previous, current)
        previous = current

    estimate = guard.assess(current=previous, next_plan=None, recovery_plan=None,
                            provider_wait_s=0, termination_plan=_plan("home", 1))
    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_nonzero_motor_lag_beyond_one_second_quantization_envelope_fails_closed() -> None:
    guard = _guard(now=2.25)
    previous = _sample(80, 0, motor_time_s=10)
    current = _sample(80, 2.25, motor_time_s=11)

    guard.observe(previous, current)

    estimate = guard.assess(current=current, next_plan=None, recovery_plan=None,
                            provider_wait_s=0, termination_plan=_plan("home", 1))
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
    guard = _guard(now=11, percent_per_second_upper=0.1, telemetry_allowance_pct=0, landing_floor_pct=20)
    kwargs = dict(next_plan=_plan("next", 30), recovery_plan=None, provider_wait_s=0,
                  termination_plan=_plan("home", 10))
    first = guard.assess(current=_sample(23, 10), **kwargs)
    guard.latch_return(first)

    later = guard.assess(current=_sample(100, 11), **kwargs)

    assert first.decision is ReserveDecision.RETURN_NOW
    assert later.decision is ReserveDecision.RETURN_NOW


def test_lower_battery_sample_cannot_be_restored_by_a_later_increase_in_the_same_guard() -> None:
    guard = _guard(now=11, percent_per_second_upper=1.0, telemetry_allowance_pct=0,
                   landing_floor_pct=20)
    kwargs = dict(next_plan=None, recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    low = guard.assess(current=_sample(20, 10), **kwargs)
    raised = guard.assess(current=_sample(100, 11), **kwargs)

    assert low.decision is ReserveDecision.LAND_CONTINGENCY
    assert raised.current_pct == 20
    assert raised.decision is ReserveDecision.LAND_CONTINGENCY


@pytest.mark.parametrize(("battery_pct", "motor_time_s"), [(79, 10), (80, 11)])
def test_conflicting_same_timestamp_battery_or_motor_payload_fails_closed(
    battery_pct: int, motor_time_s: int,
) -> None:
    guard = _guard(now=10)
    kwargs = dict(next_plan=None, recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    guard.assess(current=_sample(80, 10, motor_time_s=10), **kwargs)
    conflicted_estimate = guard.assess(current=_sample(battery_pct, 10, motor_time_s=motor_time_s), **kwargs)

    assert conflicted_estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_direct_assess_ingests_newer_battery_burn_before_pricing() -> None:
    guard = _guard(now=11, percent_per_second_upper=0.1, telemetry_allowance_pct=0,
                   landing_floor_pct=20)
    kwargs = dict(next_plan=None, recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    guard.assess(current=_sample(100, 10), **kwargs)
    estimate = guard.assess(current=_sample(80, 11), **kwargs)

    assert guard.conservative_percent_per_second == 20.0
    assert estimate.action_cost_pct == 20


def test_direct_assess_rejects_motor_regression_without_an_observe_call() -> None:
    guard = _guard(now=11)
    kwargs = dict(next_plan=None, recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    guard.assess(current=_sample(80, 10, motor_time_s=1), **kwargs)
    estimate = guard.assess(current=_sample(80, 11, motor_time_s=0), **kwargs)

    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


def test_assess_replays_an_observed_current_sample_without_learning_burn_twice() -> None:
    guard = _guard(now=11, percent_per_second_upper=0.1, telemetry_allowance_pct=0,
                   landing_floor_pct=20)
    previous = _sample(100, 10)
    current = _sample(80, 11)

    guard.observe(previous, current)
    estimate = guard.assess(current=current, next_plan=None, recovery_plan=None,
                            provider_wait_s=0, termination_plan=_plan("home", 1))

    assert guard.conservative_percent_per_second == 20.0
    assert estimate.action_cost_pct == 20


def test_direct_assess_rejects_an_older_sample_after_a_newer_ingested_sample() -> None:
    guard = _guard(now=11)
    kwargs = dict(next_plan=None, recovery_plan=None, provider_wait_s=0, termination_plan=_plan("home", 1))

    guard.assess(current=_sample(80, 11), **kwargs)
    estimate = guard.assess(current=_sample(80, 10), **kwargs)

    assert estimate.decision is ReserveDecision.LAND_CONTINGENCY


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: _sample(True, 0), "battery_pct"),
        (lambda: _sample(101, 0), "battery_pct"),
        (lambda: EnergySample(battery_pct=1, measured_at_s=float("nan")), "measured_at_s"),
        (lambda: EnergySample(battery_pct=1, measured_at_s=Decimal("NaN")), "measured_at_s"),
        (lambda: _profile(percent_per_second_upper=float("inf")), "percent_per_second_upper"),
        (lambda: _profile(percent_per_second_upper=Decimal("Infinity")), "percent_per_second_upper"),
        (lambda: _profile(percent_per_second_upper=Decimal("0.100000000000000005")), "percent_per_second_upper"),
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

    profile.validate_for_execution(mode=ExecutionMode.REAL, site_sha256="a" * 64, aircraft_id="TELLO-01")
    with pytest.raises(ValueError, match="site"):
        profile.validate_for_execution(mode=ExecutionMode.REAL, site_sha256="b" * 64, aircraft_id="TELLO-01")
    with pytest.raises(ValueError, match="PROMOTED"):
        _profile().validate_for_execution(mode=ExecutionMode.REAL, site_sha256="a" * 64, aircraft_id="TELLO-01")


@pytest.mark.parametrize("mode", ["real", "REAL", "rea", "simulation"])
def test_execution_mode_requires_an_explicit_known_enum(mode: str) -> None:
    with pytest.raises(ValueError, match="ExecutionMode"):
        _profile(execution_scope="PROMOTED").validate_for_execution(
            mode=mode, site_sha256="a" * 64, aircraft_id="TELLO-01",
        )


def test_execution_mode_matches_the_profile_scope() -> None:
    _profile(execution_scope="SIMULATION_ONLY").validate_for_execution(
        mode=ExecutionMode.SIMULATION, site_sha256="a" * 64, aircraft_id="TELLO-01",
    )
    _profile(execution_scope="CALIBRATION").validate_for_execution(
        mode=ExecutionMode.CALIBRATION, site_sha256="a" * 64, aircraft_id="TELLO-01",
    )
    with pytest.raises(ValueError, match="CALIBRATION"):
        _profile(execution_scope="SIMULATION_ONLY").validate_for_execution(
            mode=ExecutionMode.CALIBRATION, site_sha256="a" * 64, aircraft_id="TELLO-01",
        )


def test_dry_run_can_replay_scripted_battery_and_motor_time_sequences() -> None:
    client = DryRunClient(battery_sequence=(25, 24, 21), flight_time_sequence=(10, 15, 20))
    client.connect()

    readings = (client.telemetry(), client.telemetry(), client.telemetry())

    assert [(reading.battery_percent, reading.flight_time_s) for reading in readings] == [
        (25, 10), (24, 15), (21, 20),
    ]
