# src/sentinel/survey/observability.py
"""Whether a class can be resolved at all at a given range.

Derived in the design spec section 5.1 from the Tello's 82.6 degree
*diagonal* field of view over a 960x720 frame: half-diagonal 41.3 degrees
against a 600 px image half-diagonal gives f = 600 / tan(41.3) = 683 px.
An object of real width S at range D spans S * f / D pixels.
"""
from __future__ import annotations

FOCAL_PX = 683.0
IMAGE_WIDTH = 960
IMAGE_HEIGHT = 720
PRINCIPAL_X = IMAGE_WIDTH / 2.0

#: Nominal real width in centimetres, per class.
NOMINAL_WIDTH_CM = {"person": 50.0, "computer": 35.0, "phone": 15.0, "pen": 1.0}

#: Below this many pixels across, no detector we have measured returns a
#: usable box. rev 4 V4: pinned at 20.0, not the original 12.0 -- at 12.0
#: a 15 cm phone at 6 m (15 * 683 / 600 = 17.07 px) is >= 12 and so
#: resolvable, which falsifies test_moving_closer_can_make_a_phone_resolvable
#: (that test asserts phone is NOT resolvable at 600 cm). At 20.0: phone is
#: 34.2 px at 3 m (resolvable), 17.1 px at 6 m (not), 68.3 px at 1.5 m
#: (resolvable), pen 2.3 px at 3 m (not). This constant also drives
#: class_counts's NOT_OBSERVABLE decision, so it is load-bearing -- do not
#: change it without re-verifying every threshold above.
MIN_DETECTABLE_PX = 20.0


def pixels_at(class_name: str, range_cm: float) -> float:
    if range_cm <= 0:
        raise ValueError("range must be positive")
    return NOMINAL_WIDTH_CM[class_name] * FOCAL_PX / range_cm


def resolvable_classes(range_cm: float) -> tuple[str, ...]:
    return tuple(name for name in NOMINAL_WIDTH_CM
                 if pixels_at(name, range_cm) >= MIN_DETECTABLE_PX)
