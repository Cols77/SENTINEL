"""Aggregate one stationary hover burst of pose reports into a single fix.

Pose is only trusted at rest: stream latency and gaps then cost time, never
accuracy. Pure functions, no I/O.
"""
import math
import statistics
from dataclasses import dataclass
from typing import Sequence

from sentinel.contracts.pose import DronePoseReport
from sentinel.pose.scoring import circular_median_deg


@dataclass(frozen=True)
class LocalisationFix:
    x_units: float
    y_units: float
    heading_deg: float
    n: int
    spread_p95_cm: float


@dataclass(frozen=True)
class LocalisationUnreliable:
    reason: str
    n: int


def localise(
    reports: Sequence[DronePoseReport],
    *,
    min_samples: int = 8,
    max_spread_cm: float = 5.0,
    cm_per_unit: float = 10.0,
) -> LocalisationFix | LocalisationUnreliable:
    n = len(reports)
    if n < min_samples:
        return LocalisationUnreliable(f"{n} samples, need {min_samples}", n)
    x = statistics.median(r.point[0] for r in reports)
    y = statistics.median(r.point[1] for r in reports)
    dist = sorted(math.hypot(r.point[0] - x, r.point[1] - y) for r in reports)
    spread_cm = dist[int(0.95 * (n - 1))] * cm_per_unit
    if spread_cm > max_spread_cm:
        return LocalisationUnreliable(
            f"spread p95 {spread_cm:.1f} cm exceeds {max_spread_cm:.1f} cm", n)
    heading = circular_median_deg([r.heading_deg for r in reports])
    return LocalisationFix(x, y, heading, n, spread_cm)
