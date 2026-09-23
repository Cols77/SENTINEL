"""Feasible observation stops for one alley mouth, with computed view metrics.

Deterministic geometry: the agent chooses among candidates, it never does this
arithmetic, and every candidate is inside the commanded bounds. The alley runs
along +y beyond its mouth; look offsets are relative to facing +y, + = left.
"""
import math
from dataclasses import dataclass

from sentinel.camera import TELLO_MEASURED
from sentinel.envelope import FlightEnvelope

# Coherence spec 10.5: one camera model for every consumer of real Tello
# pixels, this one included -- the 70.2 x 55.6 degree datasheet figure these
# constants used to be built from does not describe the 960x720 stream.
_left, _right = TELLO_MEASURED.horizontal_half_angles
WIDTH_PER_RANGE = math.tan(_left) + math.tan(_right)   # ~1.076 of width per unit range (was 1.406)
BELOW_HORIZON_DEG = TELLO_MEASURED.pitch_down_deg + math.degrees(
    math.atan((TELLO_MEASURED.height - TELLO_MEASURED.cy) / TELLO_MEASURED.fy))  # ~30.9 (was 36.4)


@dataclass(frozen=True)
class Candidate:
    kind: str
    x_cm: int
    y_cm: int
    z_cm: int
    look_deg: int
    standoff_cm: int
    width_fraction: float
    near_blind_cm: float


def _candidate(kind, x, y, z, mouth_x, mouth_y, width) -> Candidate:
    standoff = math.dist((x, y), (mouth_x, mouth_y))
    look = round(math.degrees(math.atan2(mouth_y - y, mouth_x - x)) - 90.0)
    return Candidate(kind, x, y, z, look, round(standoff),
                     round(min(1.0, WIDTH_PER_RANGE * standoff / width), 3),
                     round(z / math.tan(math.radians(BELOW_HORIZON_DEG)), 1))


def observation_candidates(mouth_x_cm: float, mouth_y_cm: float, width_cm: float,
                           envelope: FlightEnvelope, *, z_cm: int | None = None) -> list[Candidate]:
    z = envelope.min_z_cm if z_cm is None else z_cm
    x = round(min(max(mouth_x_cm, envelope.min_x_cm), envelope.max_x_cm))
    y_close = envelope.max_y_cm
    if x != round(mouth_x_cm):
        return [_candidate("oblique", x, y_close, z, mouth_x_cm, mouth_y_cm, width_cm)]
    candidates = [_candidate("close_and_deep", x, y_close, z, mouth_x_cm, mouth_y_cm, width_cm)]
    if candidates[0].width_fraction < 1.0:
        y_wide = math.floor(mouth_y_cm - width_cm / WIDTH_PER_RANGE)
        if y_wide >= envelope.min_y_cm:
            candidates.append(
                _candidate("back_and_wide", x, y_wide, z, mouth_x_cm, mouth_y_cm, width_cm))
    return candidates
