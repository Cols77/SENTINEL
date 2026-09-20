# src/sentinel/pose/containment.py
"""Advisory cube containment (SR-063 AC-2).

Classifies an estimated position against the operating cube footprint. The
result informs the operator and the existing abort policy; nothing here can
issue or modify a motion command.
"""
from dataclasses import dataclass
from enum import Enum


class Containment(str, Enum):
    INSIDE = "INSIDE"
    MARGIN = "MARGIN"
    OUTSIDE = "OUTSIDE"


@dataclass(frozen=True)
class CubeFootprint:
    min_x_cm: float
    max_x_cm: float
    min_y_cm: float
    max_y_cm: float


def check_containment(x_cm: float, y_cm: float, footprint: CubeFootprint, *,
                      margin_cm: float = 30.0) -> Containment:
    f = footprint
    if not (f.min_x_cm <= x_cm <= f.max_x_cm and f.min_y_cm <= y_cm <= f.max_y_cm):
        return Containment.OUTSIDE
    if (f.min_x_cm + margin_cm <= x_cm <= f.max_x_cm - margin_cm
            and f.min_y_cm + margin_cm <= y_cm <= f.max_y_cm - margin_cm):
        return Containment.INSIDE
    return Containment.MARGIN
