"""Conservative reply-time bounds shared by flight planning and transport."""

from __future__ import annotations

import math
from dataclasses import dataclass


#: Added to calibrated motion duration before a missing reply is considered lost.
MOTION_REPLY_SLACK_S = 6.0
#: Deliberately slow calibrated rotation speed for reply-time admission.
ROTATION_DEG_PER_S = 30.0
#: DJITelloPy binds this value into ``send_control_command`` and
#: ``send_command_with_return`` when its methods are defined.  Changing an
#: instance's timeout therefore cannot make ordinary control or read commands
#: wait less than this value.
DJITELLOPY_SDK_DEFAULT_TIMEOUT_S = 7.0


@dataclass(frozen=True, slots=True)
class TransportLimits:
    """Frozen transport timing admitted by a planner without importing an SDK."""

    reply_timeout_s: float = 7.0
    control_attempts: int = 3
    control_retry_gap_s: float = 0.0
    read_timeout_s: float = 7.0
    control_timeout_s: float | None = None

    def __post_init__(self) -> None:
        if self.control_timeout_s is None:
            object.__setattr__(self, "control_timeout_s", self.reply_timeout_s)
        if (self.reply_timeout_s <= 0 or self.read_timeout_s <= 0
                or self.control_timeout_s <= 0):
            raise ValueError("transport timeouts must be positive")
        if self.control_attempts < 1 or self.control_retry_gap_s < 0:
            raise ValueError("transport retry configuration is invalid")


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


def set_speed_upper_bound_s(limits: TransportLimits) -> float:
    """Bound all configured SDK control attempts for one speed change."""

    return (limits.control_attempts * limits.control_timeout_s
            + (limits.control_attempts - 1) * limits.control_retry_gap_s)
