# src/sentinel/survey/envelope.py
"""The flyable box, as plain data.

Deliberately free of any ``sentinel.cube`` import so every module below it
is testable before WP5 lands. ``survey.cube_bridge`` (Task 11) is the one
place that converts a WP5 ``Site`` into this.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SweepEnvelope:
    min_x_cm: int
    max_x_cm: int
    min_y_cm: int
    max_y_cm: int
    min_z_cm: int
    max_z_cm: int
    standoff_cm: int      # distance from the rail to what it looks at

    def contains(self, x_cm: float, y_cm: float, z_cm: float) -> bool:
        return (self.min_x_cm <= x_cm <= self.max_x_cm
                and self.min_y_cm <= y_cm <= self.max_y_cm
                and self.min_z_cm <= z_cm <= self.max_z_cm)


@dataclass(frozen=True)
class SweepOption:
    option_id: str
    description: str
    stops: tuple[tuple[int, int], ...]
    z_cm: int
    looks_deg: tuple[int, ...]
    viewed_width_cm: int
    estimated_seconds: int
    resolvable_classes: tuple[str, ...]
    range_cm: int
