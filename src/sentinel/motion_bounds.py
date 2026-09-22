"""Conservative reply-time bounds shared by flight planning and transport."""

from __future__ import annotations

import math


#: Added to calibrated motion duration before a missing reply is considered lost.
MOTION_REPLY_SLACK_S = 6.0
#: Deliberately slow calibrated rotation speed for reply-time admission.
ROTATION_DEG_PER_S = 30.0


def move_reply_upper_bound_s(*, distance_cm: int | float, speed_cm_s: int | float,
                             base_timeout_s: int | float) -> int:
    """Bound one translation using the real backend's half-speed calibration."""

    if distance_cm < 0 or speed_cm_s <= 0 or base_timeout_s <= 0:
        raise ValueError("motion-bound inputs must be positive (distance may be zero)")
    return math.ceil(max(base_timeout_s, distance_cm / (0.5 * speed_cm_s) + MOTION_REPLY_SLACK_S))


def rotate_reply_upper_bound_s(*, degrees: int | float, base_timeout_s: int | float) -> int:
    """Bound one yaw command using the calibrated slow rotation rate."""

    if degrees < 0 or base_timeout_s <= 0:
        raise ValueError("rotation-bound inputs must be positive (degrees may be zero)")
    return math.ceil(max(base_timeout_s, degrees / ROTATION_DEG_PER_S + MOTION_REPLY_SLACK_S))
