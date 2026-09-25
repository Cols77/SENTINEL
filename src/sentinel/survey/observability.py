# src/sentinel/survey/observability.py
"""Nominal object sizes and whether a class can be resolved at a given range.

The camera model itself (``CameraModel``, ``SYNTHETIC_LEVEL``,
``TELLO_MEASURED``, ``CAMERAS``) lives in ``sentinel.camera`` and is
re-exported here for existing callers; see that module's docstring for what
each model is and why both stay. Coherence spec section 10.5 makes
``TELLO_MEASURED`` the single camera model for real frames;
``tests/survey/test_camera_model.py`` fails if it drifts from the
calibration artifact.

An object of real width S at forward distance D spans S * f / D pixels.
"""
from __future__ import annotations

from sentinel.camera import CAMERAS, CameraModel, SYNTHETIC_LEVEL, TELLO_MEASURED

#: The synthetic model's constants under their original names. Kept because
#: the evaluation harness compares a scene's camera against them; they are
#: NOT the real camera (see the module docstring).
FOCAL_PX = SYNTHETIC_LEVEL.fx
IMAGE_WIDTH = SYNTHETIC_LEVEL.width
IMAGE_HEIGHT = SYNTHETIC_LEVEL.height
PRINCIPAL_X = SYNTHETIC_LEVEL.cx
#: rev 3 R1: ground-plane depth reads the box centre's pixel row against
#: this line, not against PRINCIPAL_X.
PRINCIPAL_Y = SYNTHETIC_LEVEL.cy

#: Nominal real width in centimetres, per class.
NOMINAL_WIDTH_CM = {"person": 50.0, "computer": 35.0, "phone": 15.0, "mug": 8.0}

#: Below this many pixels across, no detector we have measured returns a
#: usable box. rev 4 V4: pinned at 20.0, not the original 12.0 -- at 12.0
#: a 15 cm phone at 6 m (15 * 683 / 600 = 17.07 px) is >= 12 and so
#: resolvable, which falsifies test_moving_closer_can_make_a_phone_resolvable
#: (that test asserts phone is NOT resolvable at 600 cm). At 20.0: phone is
#: 34.2 px at 3 m (resolvable), 17.1 px at 6 m (not), 68.3 px at 1.5 m
#: (resolvable), mug 18.2 px at 3 m (not). This constant also drives
#: class_counts's NOT_OBSERVABLE decision, so it is load-bearing -- do not
#: change it without re-verifying every threshold above.
#: rev 5: those figures are the synthetic model's. On the measured camera
#: everything is 31% larger (phone 44.6 px at 3 m, 22.3 px at 6 m, mug
#: 23.9 px at 3 m), so a phone stays resolvable out to 6.7 m. The threshold
#: itself is a statement about detectors and is unchanged; it is still
#: unmeasured and the provider spike is what will pin it.
MIN_DETECTABLE_PX = 20.0


def pixels_at(class_name: str, range_cm: float,
              camera: CameraModel = SYNTHETIC_LEVEL) -> float:
    if range_cm <= 0:
        raise ValueError("range must be positive")
    return NOMINAL_WIDTH_CM[class_name] * camera.fx / range_cm


def resolvable_classes(range_cm: float,
                       camera: CameraModel = SYNTHETIC_LEVEL) -> tuple[str, ...]:
    return tuple(name for name in NOMINAL_WIDTH_CM
                 if pixels_at(name, range_cm, camera) >= MIN_DETECTABLE_PX)
