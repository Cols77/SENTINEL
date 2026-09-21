# src/sentinel/survey/cube_bridge.py
"""The real flight adapter (plan Task 11, amendments V12 + V13).

This is the only module in ``sentinel.survey`` that imports ``sentinel.cube``
-- the dependency runs survey -> cube by design, never the other way, so
every other survey module stays testable without WP5 (``envelope.py``'s own
docstring).

``envelope_from_site``/``mission_from_option`` convert a WP5 ``Site`` and a
survey ``SweepOption`` into the cube package's own ``SweepEnvelope``/
``Mission`` types. ``CubeFlight`` runs a ``SweepOption`` as a real
``CubeMissionRunner`` mission and turns its mission log into
``FlightFrame``s:

V12 (one camera model for real pixels): a ``CubeFlight`` always runs against
``sentinel.camera.TELLO_MEASURED`` -- passing any other camera is refused
outright, so there is no code path in which a frame the drone filmed can
reach the survey under the synthetic model.

V13 (every frame carries the freshest position available): a stop whose
pose fix was reliable and plausible (mission log ``status == "LOCALISED"``)
stamps the frame's ``x_cm``/``y_cm``/``heading_deg`` from that fix and
``position_kind="MEASURED"``; any other stop (unlocalised, pose off, or the
waypoint opted out of localising) stamps the dead-reckoned commanded values
and ``FACING_DEG`` -- the drone only ever translates while facing +y -- with
``position_kind="COMMANDED"``. ``z_cm`` is the runner's own measured height
telemetry (``cube/mission.py`` stamps it into the log entry right after
every ``_go_to``, not the commanded target -- the two agree within
``DEAD_BAND_CM`` by construction but are not literally the same number).
``delta_z_cm`` is that height above the tape-measured surface, replacing
``loop.DELTA_Z_CM`` for a real flight's frames (``loop._fly_and_ingest``
falls back to the constant only when a frame does not carry its own,
e.g. every replay fixture). The stop's tolerance verdict (cube/mission.py's
two tolerance circles) is carried onto the frame too, so the run summary can
say when a photo was shot from a stop the drone did not quite hover at.

A stop aborted en route (the in-move watchdog, a containment or tolerance
abort, ...) never reaches ``_look``, so its log entry has no "frames" at
all and contributes nothing -- ``fly()`` returns whatever frames were
captured before the abort rather than raising; the drone has already been
landed by the existing controlled-landing path either way.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from sentinel.camera import CameraModel, TELLO_MEASURED
from sentinel.cube.mission import CubeMissionRunner, FACING_DEG, Mission, Waypoint
from sentinel.cube.site import Site

from .envelope import SweepEnvelope
from .flight import FlightFrame


class SyntheticCameraRefused(ValueError):
    """A CubeFlight run must use the measured camera; the synthetic model
    would misplace every real object (V12)."""


def envelope_from_site(site: Site, standoff_cm: int) -> SweepEnvelope:
    """The same inset arithmetic as ``Site.envelope()`` (cube/site.py),
    producing the survey's own ``SweepEnvelope`` instead of a
    ``FlightEnvelope``: ``standoff_cm`` is a survey concept (how far the
    rail stands from what it looks at) the site file does not carry."""
    return SweepEnvelope(
        min_x_cm=round((site.cube_min_x_m + site.inset_m) * 100),
        max_x_cm=round((site.cube_max_x_m - site.inset_m) * 100),
        min_y_cm=round((site.cube_min_y_m + site.inset_m) * 100),
        max_y_cm=round((site.cube_max_y_m - site.inset_m) * 100),
        min_z_cm=site.min_z_cm, max_z_cm=site.max_z_cm, standoff_cm=standoff_cm)


def mission_from_option(option, start_x_cm: int, start_y_cm: int) -> Mission:
    """One Waypoint per stop, in order. ``localise=True`` always (V13: a
    pose burst at every stop, not only the ones an operator remembered to
    ask for)."""
    waypoints = tuple(
        Waypoint(f"{option.option_id}_{index}", x_cm, y_cm, option.z_cm,
                 looks_deg=option.looks_deg, localise=True)
        for index, (x_cm, y_cm) in enumerate(option.stops))
    return Mission(start_x_cm, start_y_cm, waypoints)


class CubeFlight:
    """Implements ``FlightPort`` by flying a real (or dry-run) cube
    mission through ``CubeMissionRunner``."""

    def __init__(self, *, client, site: Site, mode: str, pose_mode: str,
                 run_id: str, mission_log, capture_dir=None,
                 start_x_cm: int, start_y_cm: int,
                 surface_z_cm: float, sr063_amended: bool = False,
                 tag_map=None, conn=None, burst_fn: Callable[[], list] | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 minimum_battery: int = 40, confirm: bool = True,
                 camera: CameraModel = TELLO_MEASURED) -> None:
        if camera != TELLO_MEASURED:
            raise SyntheticCameraRefused(
                "CubeFlight only runs against sentinel.camera.TELLO_MEASURED; "
                f"got {camera.name!r} -- a real photograph placed with the wrong "
                "camera model misplaces every object it sees (V12).")
        if pose_mode == "reanchor" and not sr063_amended:
            # Mirrors sentinel.cli's --pose reanchor guard for cube-run.
            raise ValueError(
                "--pose reanchor lets pose correct motion, which SR-063 forbids as "
                "written. Amend SR-063 first, then pass sr063_amended=True.")
        self.client, self.site, self.mode, self.pose_mode = client, site, mode, pose_mode
        self.run_id = run_id
        self.mission_log = Path(mission_log)
        self.capture_dir = capture_dir
        self.start_x_cm, self.start_y_cm = start_x_cm, start_y_cm
        self.surface_z_cm = surface_z_cm
        self.sr063_amended = sr063_amended
        self.tag_map, self.conn = tag_map, conn
        self.burst_fn, self.sleep_fn = burst_fn, sleep_fn
        self.minimum_battery, self.confirm = minimum_battery, confirm

    def fly(self, option) -> list[FlightFrame]:
        mission = mission_from_option(option, self.start_x_cm, self.start_y_cm)
        already = (self.mission_log.read_text(encoding="utf-8").splitlines()
                  if self.mission_log.exists() else [])
        runner = CubeMissionRunner(
            self.client, self.mode, self.minimum_battery, mission=mission, site=self.site,
            pose_mode=self.pose_mode, tag_map=self.tag_map, conn=self.conn,
            run_id=self.run_id, mission_log=self.mission_log, capture_dir=self.capture_dir,
            burst_fn=self.burst_fn, sleep_fn=self.sleep_fn)
        runner.run(confirm=self.confirm)
        new_lines = self.mission_log.read_text(encoding="utf-8").splitlines()[len(already):]
        entries = [json.loads(line) for line in new_lines]
        return [
            self._frame_from(stop_index, entry, frame)
            for stop_index, entry in enumerate(entries)
            for frame in entry.get("frames", ())
        ]

    def _frame_from(self, stop_index: int, entry: dict, frame: dict) -> FlightFrame:
        measured = entry["status"] == "LOCALISED"
        if measured:
            x_cm, y_cm = entry["fix_cm"]
            heading_deg = entry["heading_deg"]
        else:
            x_cm, y_cm = entry["commanded_cm"][0], entry["commanded_cm"][1]
            heading_deg = FACING_DEG
        z_cm = entry["height_cm"]
        return FlightFrame(
            stop_index=stop_index, look_deg=frame["look_deg"], x_cm=x_cm, y_cm=y_cm,
            z_cm=z_cm, heading_deg=heading_deg,
            position_kind="MEASURED" if measured else "COMMANDED",
            image_path=frame["path"], sha256=frame["sha256"],
            artifact_id=f"cube:{self.run_id}:{entry['waypoint']}:{frame['look_deg']}",
            delta_z_cm=z_cm - self.surface_z_cm, tolerance=entry.get("tolerance"))
