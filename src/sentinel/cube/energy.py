"""Conservative, immutable energy-reserve admission for cube flights.

This module does not talk to the aircraft.  It consumes the upper bounds from
the same checkpoint plans dispatched by the mission runner, which makes the
reserve calculation auditable and prevents a second, looser route estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from enum import Enum
import math
from typing import Callable, Literal, Mapping, Protocol


class Clock(Protocol):
    def __call__(self) -> float: ...


class ReserveDecision(str, Enum):
    CONTINUE = "CONTINUE"
    RETURN_NOW = "RETURN_NOW"
    LAND_CONTINGENCY = "LAND_CONTINGENCY"


class ExecutionMode(str, Enum):
    """The only execution contexts that may consume an energy profile."""

    SIMULATION = "SIMULATION"
    CALIBRATION = "CALIBRATION"
    REAL = "REAL"


# Tello's ``time?`` telemetry is integral seconds.  A read can therefore lead
# or lag monotonic elapsed time by one reported second at a counter boundary.
# Comparing against a stream epoch makes that phase envelope cumulative across
# frequent subsecond polls and counter advances.
MOTOR_TIME_QUANTIZATION_ENVELOPE_S = Decimal("1")
#: A single 1 % battery reading step over a shorter window is not learned as a rate.
BURN_QUANTIZATION_WINDOW_S = Decimal("10")


def _exact_decimal(value: object, field: str) -> Decimal:
    """Accept finite external numbers as their documented decimal spelling.

    A binary float is intentionally parsed through ``str`` so the public
    decimal contract (for example ``0.07`` percent/s) is not perturbed by its
    in-memory IEEE-754 representation before the single conservative ceiling.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{field} must be a finite nonnegative number")
    try:
        numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite nonnegative number") from exc
    if not numeric.is_finite() or numeric < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return numeric


def _finite_nonnegative(value: object, field: str) -> float:
    # Public profile/sample fields are annotated as floats.  Do not silently
    # accept a higher-precision Decimal then downcast it: reserve arithmetic
    # accepts float/int inputs as their decimal spelling at the boundary.
    if type(value) not in (int, float):
        raise TypeError(f"{field} must be a finite nonnegative float or integer")
    numeric = _exact_decimal(value, field)
    result = float(numeric)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite nonnegative number")
    return result


def _percent(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError(f"{field} must be an integer from 0 to 100")
    return value


def _identity(value: object, field: str) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{field} must be a nonblank normalized string")
    return value


@dataclass(frozen=True, slots=True)
class EnergyProfile:
    profile_id: str
    site_sha256: str
    aircraft_id: str
    percent_per_second_upper: float
    telemetry_allowance_pct: int
    landing_floor_pct: int
    sample_max_age_s: float
    execution_scope: Literal["SIMULATION_ONLY", "CALIBRATION", "PROMOTED"]

    def __post_init__(self) -> None:
        for field in ("profile_id", "site_sha256", "aircraft_id"):
            object.__setattr__(self, field, _identity(getattr(self, field), field))
        object.__setattr__(self, "percent_per_second_upper", _finite_nonnegative(
            self.percent_per_second_upper, "percent_per_second_upper"))
        object.__setattr__(self, "telemetry_allowance_pct", _percent(
            self.telemetry_allowance_pct, "telemetry_allowance_pct"))
        object.__setattr__(self, "landing_floor_pct", _percent(
            self.landing_floor_pct, "landing_floor_pct"))
        object.__setattr__(self, "sample_max_age_s", _finite_nonnegative(
            self.sample_max_age_s, "sample_max_age_s"))
        if self.execution_scope not in {"SIMULATION_ONLY", "CALIBRATION", "PROMOTED"}:
            raise ValueError("execution_scope is not recognized")

    def validate_for_execution(self, *, mode: ExecutionMode, site_sha256: str, aircraft_id: str) -> None:
        """Require the profile's physical binding before an execution mode uses it."""

        if not isinstance(mode, ExecutionMode):
            raise ValueError("execution mode must be an ExecutionMode")
        if site_sha256 != self.site_sha256:
            raise ValueError("energy profile site does not match the active site")
        if aircraft_id != self.aircraft_id:
            raise ValueError("energy profile aircraft does not match the active aircraft")
        required_scope = {
            ExecutionMode.SIMULATION: "SIMULATION_ONLY",
            ExecutionMode.CALIBRATION: "CALIBRATION",
            ExecutionMode.REAL: "PROMOTED",
        }[mode]
        if self.execution_scope != required_scope:
            raise ValueError(f"{mode.value} execution requires a {required_scope} energy profile")

    def allows_execution(self, *, mode: ExecutionMode, site_sha256: str, aircraft_id: str) -> bool:
        try:
            self.validate_for_execution(mode=mode, site_sha256=site_sha256, aircraft_id=aircraft_id)
        except (TypeError, ValueError):
            return False
        return True


@dataclass(frozen=True, slots=True)
class EnergySample:
    """One explicitly timed battery reading with optional motor-time provenance."""

    battery_pct: int
    measured_at_s: float
    motor_time_s: float | None = None
    motor_time_source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "battery_pct", _percent(self.battery_pct, "battery_pct"))
        object.__setattr__(self, "measured_at_s", _finite_nonnegative(
            self.measured_at_s, "measured_at_s"))
        if self.motor_time_s is None:
            if self.motor_time_source is not None:
                raise ValueError("motor_time_source requires motor_time_s")
            return
        object.__setattr__(self, "motor_time_s", _finite_nonnegative(self.motor_time_s, "motor_time_s"))
        object.__setattr__(self, "motor_time_source", _identity(
            self.motor_time_source, "motor_time_source"))

    @property
    def battery_percent(self) -> int:
        return self.battery_pct

    @property
    def measured_monotonic_s(self) -> float:
        return self.measured_at_s


def _components(value: object) -> tuple[tuple[str, float], ...]:
    items = value.items() if isinstance(value, Mapping) else value
    try:
        frozen = tuple((key, numeric) for key, numeric in items)  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise TypeError("components must be key/value pairs") from exc
    normalized: list[tuple[str, float]] = []
    for key, numeric in frozen:
        if type(key) is not str or not key:
            raise ValueError("component keys must be nonblank strings")
        normalized.append((key, _finite_nonnegative(numeric, f"component {key}")))
    normalized.sort(key=lambda item: item[0])
    if len({key for key, _ in normalized}) != len(normalized):
        raise ValueError("components must have unique keys")
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class EnergyEstimate:
    current_pct: int
    action_cost_pct: int
    projected_landing_pct: int
    landing_floor_pct: int
    decision: ReserveDecision
    components: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_pct", _percent(self.current_pct, "current_pct"))
        if type(self.action_cost_pct) is not int or self.action_cost_pct < 0:
            raise ValueError("action_cost_pct must be a nonnegative integer")
        if type(self.projected_landing_pct) is not int:
            raise ValueError("projected_landing_pct must be an integer")
        object.__setattr__(self, "landing_floor_pct", _percent(
            self.landing_floor_pct, "landing_floor_pct"))
        if not isinstance(self.decision, ReserveDecision):
            raise TypeError("decision must be a ReserveDecision")
        object.__setattr__(self, "components", _components(self.components))


class ReserveGuard:
    """Monotonic, fail-closed admission guard for a declared flight action."""

    def __init__(self, profile: EnergyProfile, clock: Clock,
                 *, disable_predictive_guard: bool = False) -> None:
        if not isinstance(profile, EnergyProfile):
            raise TypeError("profile must be an EnergyProfile")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.profile = profile
        self.clock = clock
        self.disable_predictive_guard = disable_predictive_guard
        self._observed_upper = _exact_decimal(profile.percent_per_second_upper, "percent_per_second_upper")
        self._return_latched = False
        self._clock_failed = False
        self._last_clock_s: float | None = None
        self._last_sample_s: float | None = None
        self._telemetry_invalid = False
        self._motor_epoch: tuple[Decimal, Decimal, str] | None = None
        self._last_ingested_sample: EnergySample | None = None
        self._minimum_battery_pct: int | None = None
        self._conservative_sample: EnergySample | None = None
        self._stream_anchor: EnergySample | None = None

    @property
    def conservative_sample(self) -> EnergySample | None:
        """Lowest validated observation, retaining its original provenance."""
        return self._conservative_sample

    @property
    def conservative_percent_per_second(self) -> float:
        return float(self._observed_upper)

    @property
    def return_latched(self) -> bool:
        return self._return_latched

    def _now(self) -> float | None:
        try:
            now = _finite_nonnegative(self.clock(), "clock")
        except (TypeError, ValueError, OverflowError):
            self._clock_failed = True
            return None
        if self._last_clock_s is not None and now < self._last_clock_s:
            self._clock_failed = True
        self._last_clock_s = now
        return now

    def _accept_sample(self, sample: EnergySample) -> None:
        """Record a sample only after its stream transition was validated."""

        if self._minimum_battery_pct is None or sample.battery_pct <= self._minimum_battery_pct:
            self._conservative_sample = sample
        self._minimum_battery_pct = min(self._minimum_battery_pct, sample.battery_pct) \
            if self._minimum_battery_pct is not None else sample.battery_pct
        self._last_ingested_sample = sample
        if self._stream_anchor is None:
            self._stream_anchor = sample
        if sample.motor_time_s is not None and self._motor_epoch is None:
            self._motor_epoch = (
                _exact_decimal(sample.measured_at_s, "measured_at_s"),
                _exact_decimal(sample.motor_time_s, "motor_time_s"),
                sample.motor_time_source,
            )

    def _valid_transition(self, previous: EnergySample, current: EnergySample) -> bool:
        elapsed = _exact_decimal(current.measured_at_s, "measured_at_s") - _exact_decimal(
            previous.measured_at_s, "measured_at_s",
        )
        if elapsed <= 0:
            return False
        if (previous.motor_time_s is None) != (current.motor_time_s is None):
            return False
        if previous.motor_time_s is not None:
            if previous.motor_time_source != current.motor_time_source:
                return False
            measured_at = _exact_decimal(current.measured_at_s, "measured_at_s")
            motor_time = _exact_decimal(current.motor_time_s, "motor_time_s")
            previous_motor_time = _exact_decimal(previous.motor_time_s, "motor_time_s")
            if motor_time < previous_motor_time or self._motor_epoch is None:
                return False
            epoch_at, epoch_motor_time, epoch_source = self._motor_epoch
            if epoch_source != current.motor_time_source or motor_time < epoch_motor_time:
                return False
            epoch_elapsed = measured_at - epoch_at
            epoch_motor_elapsed = motor_time - epoch_motor_time
            if (epoch_elapsed < 0
                    or abs(epoch_motor_elapsed - epoch_elapsed)
                    > MOTOR_TIME_QUANTIZATION_ENVELOPE_S):
                return False
        burn = previous.battery_pct - current.battery_pct
        # Task B2: a single 1 % reading step between two samples a few
        # seconds apart is the reading's resolution, not a burn rate (2 s
        # apart it read as 0.5 %/s and priced every action at tens of
        # percent). Such a pair teaches nothing by itself; the rate over the
        # stream since its first sample, once that spans the window, does.
        if burn > 1 or (burn == 1 and elapsed >= BURN_QUANTIZATION_WINDOW_S):
            self._observed_upper = max(self._observed_upper, Decimal(burn) / elapsed)
        anchor = self._stream_anchor
        if anchor is not None:
            anchor_elapsed = _exact_decimal(current.measured_at_s, "measured_at_s") - _exact_decimal(
                anchor.measured_at_s, "measured_at_s")
            anchor_burn = anchor.battery_pct - current.battery_pct
            if anchor_burn > 0 and anchor_elapsed >= BURN_QUANTIZATION_WINDOW_S:
                self._observed_upper = max(self._observed_upper, Decimal(anchor_burn) / anchor_elapsed)
        return True

    def ingest_sample(self, sample: EnergySample) -> bool:
        """Ingest one chronological sample exactly once, failing closed on drift.

        Exact replays of the latest immutable payload are idempotent.  A
        changed payload at that timestamp, or any older timestamp, is an
        integrity fault.  Valid newer samples are checked against the stream
        predecessor before they can influence reserve pricing.
        """

        if not isinstance(sample, EnergySample):
            self._telemetry_invalid = True
            return False
        previous = self._last_ingested_sample
        if previous is None:
            self._accept_sample(sample)
            return True
        measured_at = _exact_decimal(sample.measured_at_s, "measured_at_s")
        previous_at = _exact_decimal(previous.measured_at_s, "measured_at_s")
        if measured_at == previous_at:
            if sample == previous:
                return True
            self._telemetry_invalid = True
            return False
        if measured_at < previous_at or not self._valid_transition(previous, sample):
            self._telemetry_invalid = True
            return False
        self._accept_sample(sample)
        return True

    def observe(self, previous: EnergySample, current: EnergySample) -> None:
        """Raise the learned rate only after internally consistent telemetry."""

        if not self.ingest_sample(previous):
            return
        self.ingest_sample(current)

    @staticmethod
    def _plan_seconds(plan: object, field: str) -> Decimal:
        if plan is None:
            return Decimal(0)
        try:
            return _exact_decimal(getattr(plan, "worst_case_s"), field)
        except AttributeError as exc:
            raise TypeError(f"{field} must declare worst_case_s") from exc

    def _cost(self, seconds: Decimal) -> int:
        if self.disable_predictive_guard:
            return 0
        value = self._observed_upper * seconds + Decimal(self.profile.telemetry_allowance_pct)
        return int(value.to_integral_value(rounding=ROUND_CEILING))

    def _estimate(self, *, current_pct: int, action_cost: int, decision: ReserveDecision,
                  next_seconds: Decimal, recovery_seconds: Decimal, provider_wait_s: Decimal,
                  termination_seconds: Decimal) -> EnergyEstimate:
        components = {
            "next_checkpoint_s": float(next_seconds),
            "percent_per_second_upper": float(self._observed_upper),
            "predictive_guard_disabled": float(self.disable_predictive_guard),
            "provider_wait_s": float(provider_wait_s),
            "recovery_s": float(recovery_seconds),
            "telemetry_allowance_pct": float(self.profile.telemetry_allowance_pct),
            "termination_s": float(termination_seconds),
            "total_worst_case_s": float(next_seconds + recovery_seconds + provider_wait_s + termination_seconds),
        }
        return EnergyEstimate(
            current_pct=current_pct, action_cost_pct=action_cost,
            projected_landing_pct=current_pct - action_cost,
            landing_floor_pct=self.profile.landing_floor_pct, decision=decision,
            components=components,
        )

    def _sample_is_current(self, current: EnergySample, now: float | None) -> bool:
        if now is None or self._clock_failed or self._telemetry_invalid:
            return False
        age = now - current.measured_at_s
        if age < 0 or age > self.profile.sample_max_age_s:
            return False
        if self._last_sample_s is not None and current.measured_at_s < self._last_sample_s:
            return False
        self._last_sample_s = current.measured_at_s
        return True

    def assess(self, *, current: EnergySample, next_plan: object | None, recovery_plan: object | None,
               provider_wait_s: float, termination_plan: object) -> EnergyEstimate:
        if not isinstance(current, EnergySample):
            raise TypeError("current must be an EnergySample")
        provider_wait = _exact_decimal(provider_wait_s, "provider_wait_s")
        if termination_plan is None:
            raise ValueError("termination_plan is required")
        next_seconds = self._plan_seconds(next_plan, "next_plan.worst_case_s")
        recovery_seconds = self._plan_seconds(recovery_plan, "recovery_plan.worst_case_s")
        termination_seconds = self._plan_seconds(termination_plan, "termination_plan.worst_case_s")
        bound_sample = self.ingest_sample(current)
        usable_battery = self._minimum_battery_pct if self._minimum_battery_pct is not None else current.battery_pct
        action_seconds = next_seconds + recovery_seconds + provider_wait + termination_seconds
        action_cost = self._cost(action_seconds)
        termination_cost = self._cost(termination_seconds)
        valid_sample = bound_sample and self._sample_is_current(current, self._now())
        can_terminate = usable_battery - termination_cost >= self.profile.landing_floor_pct
        if self.disable_predictive_guard:
            # Live demo: the Tello's battery reading is too unstable to gate
            # on. No floor, no projected cost, no telemetry-validity landing;
            # the aircraft's own firmware low-battery landing still applies.
            decision = ReserveDecision.RETURN_NOW if self._return_latched else ReserveDecision.CONTINUE
        elif not valid_sample:
            decision = ReserveDecision.LAND_CONTINGENCY
        elif self._return_latched:
            decision = ReserveDecision.RETURN_NOW if can_terminate else ReserveDecision.LAND_CONTINGENCY
        elif usable_battery - action_cost >= self.profile.landing_floor_pct:
            decision = ReserveDecision.CONTINUE
        else:
            decision = ReserveDecision.RETURN_NOW if can_terminate else ReserveDecision.LAND_CONTINGENCY
        return self._estimate(
            current_pct=usable_battery, action_cost=action_cost, decision=decision,
            next_seconds=next_seconds, recovery_seconds=recovery_seconds,
            provider_wait_s=provider_wait, termination_seconds=termination_seconds,
        )

    def latch_return(self, estimate: EnergyEstimate) -> None:
        if not isinstance(estimate, EnergyEstimate):
            raise TypeError("estimate must be an EnergyEstimate")
        self._return_latched = True
