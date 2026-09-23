# src/sentinel/camera.py
"""The camera model: two of them, and they must not be mixed.

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

This module holds only the camera model itself, so it can be imported by
``sentinel.cube`` without pulling in ``sentinel.survey`` (that dependency
runs the other way by design). ``sentinel.survey.observability`` re-exports
these names for its existing callers.
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
