# src/sentinel/survey/options.py
"""Generate the legal sweeps. Deterministic, envelope-valid, and the only
source of a sweep the agent may ask for."""
from __future__ import annotations

import math

from .envelope import SweepEnvelope, SweepOption
from .observability import FOCAL_PX, IMAGE_WIDTH, resolvable_classes

MAX_WAYPOINTS = 8          # the WP5 mission runner's own cap
SECONDS_PER_STOP = 22      # move, settle, capture -- measured on 09-21, revise then


def _rail(envelope: SweepEnvelope, option_id: str, description: str,
          count: int, z_cm: int, y_cm: int, range_cm: int) -> SweepOption:
    span = envelope.max_x_cm - envelope.min_x_cm
    step = span / (count - 1) if count > 1 else 0
    stops = tuple((int(round(envelope.min_x_cm + step * index)), y_cm)
                  for index in range(count))
    half_angle = math.atan(IMAGE_WIDTH / 2.0 / FOCAL_PX)
    viewed = int(span + 2 * range_cm * math.tan(half_angle))
    return SweepOption(option_id, description, stops, z_cm, (0,), viewed,
                       count * SECONDS_PER_STOP, resolvable_classes(range_cm), range_cm)


def generate_options(envelope: SweepEnvelope) -> tuple[SweepOption, ...]:
    mid_z = (envelope.min_z_cm + envelope.max_z_cm) // 2
    near_y = envelope.min_y_cm
    far_y = envelope.max_y_cm
    near_range = envelope.standoff_cm + (far_y - near_y)
    far_range = envelope.standoff_cm
    centre_x = (envelope.min_x_cm + envelope.max_x_cm) // 2
    close_range = max(100, envelope.standoff_cm // 2)

    options = [
        _rail(envelope, "RAIL_MID_HEIGHT", "four stops across the middle of the cube",
              4, mid_z, near_y, near_range),
        _rail(envelope, "RAIL_HIGH_WIDE", "six stops at the top of the cube, widest view",
              6, envelope.max_z_cm, far_y, far_range),
        _rail(envelope, "RAIL_LOW_NEAR", "four stops low and close",
              4, envelope.min_z_cm, near_y, near_range),
        SweepOption("CLOSE_LOOK_CENTRE", "two close stops in the middle, for a second look",
                    ((centre_x - 40, far_y), (centre_x + 40, far_y)), mid_z, (0,),
                    int(2 * close_range * math.tan(math.atan(IMAGE_WIDTH / 2.0 / FOCAL_PX))),
                    2 * SECONDS_PER_STOP, resolvable_classes(close_range), close_range),
    ]
    for option in options:
        assert len(option.stops) <= MAX_WAYPOINTS, option.option_id
        for x_cm, y_cm in option.stops:
            assert envelope.contains(x_cm, y_cm, option.z_cm), option.option_id
    return tuple(options)
