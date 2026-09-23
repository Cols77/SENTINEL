# src/sentinel/survey/flight.py
"""The flight boundary: one interface, so the loop never knows whether a
drone moved."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FlightFrame:
    stop_index: int
    look_deg: float
    x_cm: float
    y_cm: float
    z_cm: float
    heading_deg: float
    position_kind: str
    image_path: str
    artifact_id: str
    sha256: str
    #: The per-frame height above the tape-measured surface (plan Task 11,
    #: V12): ``None`` (the default) falls back to ``loop.DELTA_Z_CM``, so
    #: every existing construction of a FlightFrame -- positional or
    #: keyword -- and every replay fixture without the field are unaffected.
    delta_z_cm: float | None = None
    #: The stop's tolerance verdict ("INSIDE"/"OUTER"/"OUTSIDE"/None) from
    #: cube/mission.py's tolerance_verdict, carried through so a photo shot
    #: from an OUTER-circle stop is visibly that (plan Task 11, tolerance
    #: circles). Does not change how merge.py treats a sighting.
    tolerance: str | None = None


@runtime_checkable
class FlightPort(Protocol):
    def fly(self, option) -> list[FlightFrame]: ...


class ReplayFlight:
    """Serves the frames a real sweep already produced. ``frames.json`` is a
    list of FlightFrame field dicts, each carrying its own ``option_id``."""

    def __init__(self, frames_dir) -> None:
        self.frames_dir = Path(frames_dir)

    def fly(self, option) -> list[FlightFrame]:
        manifest = json.loads((self.frames_dir / "frames.json").read_text(encoding="utf-8"))
        frames = []
        for row in manifest:
            row = dict(row)
            if row.pop("option_id", option.option_id) != option.option_id:
                continue
            frames.append(FlightFrame(**row))
        return frames
