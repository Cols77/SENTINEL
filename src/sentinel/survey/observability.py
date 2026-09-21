# src/sentinel/survey/observability.py
"""The camera model, and whether a class can be resolved at a given range.

rev 5: there are two camera models and they must not be mixed.

``SYNTHETIC_LEVEL`` is the model synthetic scenes are *rendered* with: a
level optical axis, a centred principal point and f = 683 px. It came from
reading the Tello's 82.6 degree datasheet figure as a diagonal field of
view (half-diagonal 41.3 degrees against a 600 px half-diagonal). That
figure does not describe the 960x720 stream, so this model is wrong for
real pixels -- but a scene rendered with it must be inverted with it, which
is why it stays, and stays the default for replayed synthetic scenes.

``TELLO_MEASURED`` is the camera the drone actually has: the chessboard
calibration in ``calibration/tag-map-calibrated.json`` plus the 8.6 degree
downward pitch measured from the horizon row (``docs/wp5-rehearsal-
manifest.md``). Coherence spec section 10.5 makes it the single camera
model for real frames; ``tests/survey/test_camera_model.py`` fails if the
two drift apart. Any path that handles real Tello pixels passes it
explicitly.

An object of real width S at forward distance D spans S * f / D pixels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CameraModel:
    name: str
    fx: float
    fy: float
    cx: float
    cy: float
    #: Optical-axis pitch below the horizontal, degrees. Positive looks down.
    pitch_down_deg: float
    width: int = 960
    height: int = 720

    @property
    def horizontal_half_angles(self) -> tuple[float, float]:
        """(left, right) half-angles in radians; unequal when cx is off-centre."""
        return (math.atan(self.cx / self.fx), math.atan((self.width - self.cx) / self.fx))

    @property
    def horizon_row(self) -> float:
        """The image row the horizon falls on. Boxes centred at or above it
        are not on any plane below the camera."""
        return self.cy - self.fy * math.tan(math.radians(self.pitch_down_deg))


SYNTHETIC_LEVEL = CameraModel("synthetic-level", 683.0, 683.0, 480.0, 360.0, 0.0)
TELLO_MEASURED = CameraModel("tello-measured", 892.58, 893.74, 467.75, 353.83, 8.6)

CAMERAS = {camera.name: camera for camera in (SYNTHETIC_LEVEL, TELLO_MEASURED)}

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
#: rev 5: those figures are the synthetic model's. On the measured camera
#: everything is 31% larger (phone 44.6 px at 3 m, 22.3 px at 6 m, pen
#: 3.0 px at 3 m), so a phone stays resolvable out to 6.7 m. The threshold
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
