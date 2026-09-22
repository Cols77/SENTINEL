"""Conservative, immutable energy-reserve admission for cube flights.

This module does not talk to the aircraft.  It consumes the upper bounds from
the same checkpoint plans dispatched by the mission runner, which makes the
reserve calculation auditable and prevents a second, looser route estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Callable, Literal, Mapping, Protocol


class Clock(Protocol):
    def __call__(self) -> float: ...


class ReserveDecision(str, Enum):
    CONTINUE = "CONTINUE"
    RETURN_NOW = "RETURN_NOW"
    LAND_CONTINGENCY = "LAND_CONTINGENCY"


def _finite_nonnegative(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite nonnegative number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return numeric


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

    def validate_for_execution(self, *, mode: str, site_sha256: str, aircraft_id: str) -> None:
        """Require the profile's physical binding before an execution mode uses it."""

        if site_sha256 != self.site_sha256:
            raise ValueError("energy profile site does not match the active site")
        if aircraft_id != self.aircraft_id:
            raise ValueError("energy profile aircraft does not match the active aircraft")
        if mode == "real" and self.execution_scope != "PROMOTED":
            raise ValueError("real execution requires a PROMOTED energy profile")

    def allows_execution(self, *, mode: str, site_sha256: str, aircraft_id: str) -> bool:
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

    def __init__(self, profile: EnergyProfile, clock: Clock) -> None:
        if not isinstance(profile, EnergyProfile):
            raise TypeError("profile must be an EnergyProfile")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.profile = profile
        self.clock = clock
        self._observed_upper = profile.percent_per_second_upper
        self._return_latched = False
        self._clock_failed = False
        self._last_clock_s: float | None = None
        self._last_sample_s: float | None = None
        self._telemetry_invalid = False

    @property
    def conservative_percent_per_second(self) -> float:
        return self._observed_upper

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

    def observe(self, previous: EnergySample, current: EnergySample) -> None:
        """Raise the learned rate only after internally consistent telemetry."""

        if not isinstance(previous, EnergySample) or not isinstance(current, EnergySample):
            self._telemetry_invalid = True
            return
        elapsed = current.measured_at_s - previous.measured_at_s
        if elapsed <= 0:
            self._telemetry_invalid = True
            return
        rate_elapsed = elapsed
        if (previous.motor_time_s is None) != (current.motor_time_s is None):
            self._telemetry_invalid = True
            return
        if previous.motor_time_s is not None:
            if previous.motor_time_source != current.motor_time_source:
                self._telemetry_invalid = True
                return
            motor_elapsed = current.motor_time_s - previous.motor_time_s  # type: ignore[operator]
            if motor_elapsed < 0 or motor_elapsed > elapsed:
                self._telemetry_invalid = True
                return
            if motor_elapsed > 0:
                rate_elapsed = motor_elapsed
        burn = previous.battery_pct - current.battery_pct
        if burn > 0:
            self._observed_upper = max(self._observed_upper, burn / rate_elapsed)
        self._last_sample_s = max(self._last_sample_s or 0.0, current.measured_at_s)

    @staticmethod
    def _plan_seconds(plan: object, field: str) -> float:
        if plan is None:
            return 0.0
        try:
            return _finite_nonnegative(getattr(plan, "worst_case_s"), field)
        except AttributeError as exc:
            raise TypeError(f"{field} must declare worst_case_s") from exc

    def _cost(self, seconds: float) -> int:
        return math.ceil(self._observed_upper * seconds + self.profile.telemetry_allowance_pct)

    def _estimate(self, *, current: EnergySample, action_cost: int, decision: ReserveDecision,
                  next_seconds: float, recovery_seconds: float, provider_wait_s: float,
                  termination_seconds: float) -> EnergyEstimate:
        components = {
            "next_checkpoint_s": next_seconds,
            "percent_per_second_upper": self._observed_upper,
            "provider_wait_s": provider_wait_s,
            "recovery_s": recovery_seconds,
            "telemetry_allowance_pct": float(self.profile.telemetry_allowance_pct),
            "termination_s": termination_seconds,
            "total_worst_case_s": next_seconds + recovery_seconds + provider_wait_s + termination_seconds,
        }
        return EnergyEstimate(
            current_pct=current.battery_pct, action_cost_pct=action_cost,
            projected_landing_pct=current.battery_pct - action_cost,
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
        provider_wait = _finite_nonnegative(provider_wait_s, "provider_wait_s")
        if termination_plan is None:
            raise ValueError("termination_plan is required")
        next_seconds = self._plan_seconds(next_plan, "next_plan.worst_case_s")
        recovery_seconds = self._plan_seconds(recovery_plan, "recovery_plan.worst_case_s")
        termination_seconds = self._plan_seconds(termination_plan, "termination_plan.worst_case_s")
        action_seconds = next_seconds + recovery_seconds + provider_wait + termination_seconds
        action_cost = self._cost(action_seconds)
        termination_cost = self._cost(termination_seconds)
        valid_sample = self._sample_is_current(current, self._now())
        can_terminate = current.battery_pct - termination_cost >= self.profile.landing_floor_pct
        if not valid_sample:
            decision = ReserveDecision.LAND_CONTINGENCY
        elif self._return_latched:
            decision = ReserveDecision.RETURN_NOW if can_terminate else ReserveDecision.LAND_CONTINGENCY
        elif current.battery_pct - action_cost >= self.profile.landing_floor_pct:
            decision = ReserveDecision.CONTINUE
        else:
            decision = ReserveDecision.RETURN_NOW if can_terminate else ReserveDecision.LAND_CONTINGENCY
        return self._estimate(
            current=current, action_cost=action_cost, decision=decision,
            next_seconds=next_seconds, recovery_seconds=recovery_seconds,
            provider_wait_s=provider_wait, termination_seconds=termination_seconds,
        )

    def latch_return(self, estimate: EnergyEstimate) -> None:
        if not isinstance(estimate, EnergyEstimate):
            raise TypeError("estimate must be an EnergyEstimate")
        self._return_latched = True
