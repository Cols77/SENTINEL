"""Rehearsal world for Mission Control's room-agent mode (Task C).

A fixture-driven fake CanningSt room: a fake DJITelloPy that flies exactly
what it is commanded, a tag burst that reports where it is (while the wall
tags are in view), a detector that projects placed objects through the
calibrated Tello camera from wherever the fake aircraft hovers, and a
scripted Jev. The production composition (``live_session.run_count_return``
over ``live_stack.TelloFlightStackFactory`` and the real ``DjitellopyClient``)
runs on top of it unchanged; nothing here reaches hardware, the network or a
provider.

The fakes mirror ``tests/room/test_canningst_mission.py`` (FlyingTello,
Video, perfect-tag bursts); they live here because the product imports them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from sentinel.camera import TELLO_MEASURED
from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.models import VideoFrame
from sentinel.survey.detector import DetectionBatch

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures" / "room_agent" / "v1"
SITE_PATH = FIXTURES / "canningst-site.json"
PROFILES = FIXTURES / "profiles"
TAG_MAP_PATH = REPO_ROOT / "docs" / "wp5-flight3-map.json"
DRIFT_PATH = PROFILES / "canningst-drift.json"
ENERGY_PATH = PROFILES / "canningst-energy.json"
LIMITS_PATH = PROFILES / "canningst-command-limits.json"
FLIGHT_PROFILE_PATH = PROFILES / "canningst-flight-profile.json"
REHEARSAL_DETECTOR_ID = "rehearsal-detector:canningst-scene"
SUPPORTED = ("computer", "person", "phone", "mug")
SAFE_LABELS = ("CLARIFICATION_REQUIRED", "UNSUPPORTED", "ESCALATE", "CLARIFY")
#: The takeoff pad (site home) and the canonical heading (room convention,
#: degrees CCW from +x: 90 faces the tag stands along +y).
HOME_XY_CM = (250.0, 190.0)
CANONICAL_HEADING_DEG = 90.0


@dataclass(frozen=True)
class SceneObject:
    object_id: str
    class_name: str
    x_cm: float
    y_cm: float
    width_cm: float
    height_cm: float
    band: str = "high"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    label: str
    description: str
    objects: tuple[SceneObject, ...]
    #: After this many detector calls (observations), a gust yaws the
    #: aircraft away from the tags; ``None``: no gust.
    gust_after_observation: int | None = None
    gust_heading_deg: float = 75.0


#: The site's ``ground`` support plane (reported z; the Tello reads 30-40 cm low).
GROUND_PLANE_Z_CM = -35.0

#: Objects rest on the ground plane in the y 350-390 cell row, which every rear
#: observation pose sees, so each object is corroborated from several poses.
_OBJECTS = (
    SceneObject("computer-center", "computer", 250.0, 370.0, 30.0, 20.0),
    SceneObject("phone-left", "phone", 225.0, 365.0, 8.0, 4.0),
    SceneObject("phone-right-low", "phone", 285.0, 368.0, 8.0, 4.0, band="low"),
    SceneObject("computer-right", "computer", 310.0, 372.0, 30.0, 20.0),
)

SCENARIOS = {
    "a": Scenario("a", "(a) Phones and computers in three regions",
                  "Two computers and two phones across the left/center/right regions; "
                  "one phone is a low-confidence detection.", _OBJECTS),
    "b": Scenario("b", "(b) Same room, tag loss after the 2nd observation",
                  "After the second observation a gust yaws the aircraft 15 deg away from the tags: "
                  "the pose goes COASTING then RECOVERY_REQUIRED, and the tags are only visible again "
                  "after the recovery's yaw back to the mapped-tag heading.", _OBJECTS,
                  gust_after_observation=2),
}


# ---------------------------------------------------------------------------
# the fake aircraft
# ---------------------------------------------------------------------------


def _reports(x_cm, y_cm, heading, *, n=10, tags=(0, 2)):
    now = datetime.now(timezone.utc)
    return [DronePoseReport(f"r{i}", "run", now, "LIVE", (x_cm / 10, y_cm / 10), heading, None,
                            "APRILTAG_36H11_PNP", tuple(TagObservation(t, "Z", 1.5, 0.5) for t in tags), 0.5)
            for i in range(n)]


class FakeTelloWorld:
    """The DJITelloPy surface ``DjitellopyClient`` uses, flying where it is told.

    Axis moves follow the current heading (+y forward at 90 deg), yaw and the
    altimeter follow the SDK strings. Every SDK string is recorded in
    ``sent``. The wall tags are in view within ``tag_view_deg`` of the
    canonical heading; ``glimpses`` bursts still see them while yawed away
    (the tracker learns the new heading from them)."""

    def __init__(self, *, x_cm=HOME_XY_CM[0], y_cm=HOME_XY_CM[1], battery=90, motion_delay_s=0.0):
        self.sent: list[str] = []
        self.battery = battery
        self.height_dm = 0
        self.speed = 10
        self.ended = False
        self.x_cm, self.y_cm, self.heading_deg = float(x_cm), float(y_cm), CANONICAL_HEADING_DEG
        self.flying = False
        self.tag_view_deg = 180.0
        self.glimpses = 0
        self.motion_delay_s = motion_delay_s
        self.bursts: list[dict] = []
        self.lock = threading.RLock()

    # -- DJITelloPy surface ---------------------------------------------------
    def connect(self):
        self.sent.append("command")
        return "ok"

    def get_battery(self):
        return self.battery

    def get_height(self):
        return self.height_dm * 10

    def send_read_command(self, command):
        self.sent.append(command)
        return {"height?": f"{self.height_dm}dm", "speed?": str(self.speed)}.get(command, "ok")

    def query_sdk_version(self):
        self.sent.append("sdk?")
        return "unknown command: sdk?"

    def set_speed(self, value):
        self.sent.append(f"speed {value}")
        self.speed = value
        return "ok"

    def streamon(self):
        self.sent.append("streamon")
        return "ok"

    def streamoff(self):
        self.sent.append("streamoff")
        return "ok"

    def takeoff(self):
        self.sent.append("takeoff")
        self._pause()
        self.height_dm = 10
        self.flying = True
        return "ok"

    def land(self):
        self.sent.append("land")
        self._pause()
        self.height_dm = 0
        self.flying = False
        return "ok"

    def emergency(self):  # pragma: no cover - the backend's motor stop uses its own socket
        self.sent.append("emergency")
        self.flying = False
        return "ok"

    def send_command_with_return(self, command, timeout=None):
        self.sent.append(command)
        verb, _, value = command.partition(" ")
        self._pause()
        with self.lock:
            if verb in ("up", "down"):
                self.height_dm += (1 if verb == "up" else -1) * int(value) // 10
            elif verb in ("forward", "back", "left", "right"):
                offset = {"forward": 0, "back": 180, "left": 90, "right": -90}[verb]
                angle = math.radians(self.heading_deg + offset)
                self.x_cm += int(value) * math.cos(angle)
                self.y_cm += int(value) * math.sin(angle)
            elif verb in ("cw", "ccw"):
                self.heading_deg = (self.heading_deg + (1 if verb == "ccw" else -1) * int(value)) % 360
        return "ok"

    def end(self):
        self.ended = True

    def _pause(self):
        if self.motion_delay_s:
            time.sleep(self.motion_delay_s)

    # -- the world ------------------------------------------------------------
    def pose(self):
        with self.lock:
            return self.x_cm, self.y_cm, self.height_dm * 10, self.heading_deg

    def gust(self, heading_deg: float) -> None:
        """Yaw the aircraft away from the tags; one more burst still sees them."""
        with self.lock:
            self.heading_deg, self.tag_view_deg, self.glimpses = heading_deg, 5.0, 1

    def burst(self, client, *, duration_s):
        """The injected WP5 pose burst: perfect tags while they are in view."""
        with self.lock:
            heading = self.heading_deg
            off = abs((heading - CANONICAL_HEADING_DEG + 180.0) % 360.0 - 180.0)
            visible = off <= self.tag_view_deg
            if not visible and self.glimpses:
                self.glimpses -= 1
                visible = True
            self.bursts.append({"heading_deg": heading, "tags": [0, 2] if visible else []})
            if not visible:
                return []
            return _reports(self.x_cm, self.y_cm, heading)


class FakeVideo:
    """The Tello's 960x720 stream; each read remembers where the aircraft was."""

    def __init__(self, world: FakeTelloWorld):
        self.world = world
        self.frames = 0
        self.last_pose = None
        self.pose_by_frame = {}
        self.last_frame = None

    def __call__(self, **_values):
        return self

    def start(self):
        pass

    def stop(self):
        pass

    def read(self, timeout):
        self.frames += 1
        self.last_pose = self.world.pose()
        self.pose_by_frame[self.frames] = self.last_pose
        data = np.full((720, 960, 3), 40, dtype=np.uint8)
        # Every frame is distinct content (the provider cache is keyed on the
        # frame bytes): stamp the frame number and the pose into one row.
        x, y, _z, heading = self.last_pose
        data[0, :6, 0] = (self.frames % 256, self.frames // 256 % 256, int(x) % 256, int(y) % 256,
                          int(heading) % 256, 255)
        self.last_frame = VideoFrame(width=960, height=720, timestamp=datetime.now(timezone.utc),
                                     pixel_format="rgb24", frame_number=self.frames, data=data)
        return self.last_frame

    def peek(self):
        return self.last_frame


class FakeEmergencySocket:
    """The raw UDP socket of the direct motor stop: no network I/O."""

    def __init__(self, *_args, **_kwargs):
        self.sent = None

    def setblocking(self, _value):
        pass

    def bind(self, _address):
        pass

    def sendto(self, payload, address):
        self.sent = (payload, address)
        return len(payload)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# the fake detector: placed objects through the calibrated camera
# ---------------------------------------------------------------------------


def project_object(obj: SceneObject, pose, *, plane_z_cm=GROUND_PLANE_Z_CM, camera=TELLO_MEASURED):
    """The pixel box of *obj* seen from ``pose = (x, y, z, heading)``, or None.

    The exact inverse of ``survey.geometry.ground_point`` for the box's
    bottom-centre point, so the production grounding places it back where it
    stands. Boxes whose bottom centre is outside the frame are not seen."""
    x_d, y_d, z_d, heading = pose
    h = math.radians(heading)
    dx, dy = obj.x_cm - x_d, obj.y_cm - y_d
    forward = dx * math.cos(h) + dy * math.sin(h)
    lateral = dx * math.sin(h) - dy * math.cos(h)
    down = z_d - plane_z_cm
    if forward <= 0 or down <= 0:
        return None
    p = math.radians(camera.pitch_down_deg)
    depth = down * math.sin(p) + forward * math.cos(p)
    vertical = down * math.cos(p) - forward * math.sin(p)
    if depth <= 0:
        return None
    u = camera.cx + camera.fx * lateral / depth
    v = camera.cy + camera.fy * vertical / depth
    width = camera.fx * obj.width_cm / depth
    height = camera.fy * obj.height_cm / depth
    if not (width / 2 <= u <= camera.width - width / 2 and height <= v <= camera.height):
        return None
    return {"x": u - width / 2, "y": v - height, "width": width, "height": height}


class SceneDetector:
    """Detections of the scenario's placed objects from the last frame's pose."""

    provider = "rehearsal"
    identity = REHEARSAL_DETECTOR_ID
    model = "canningst-scene"

    def __init__(self, scenario: Scenario, video: FakeVideo, world: FakeTelloWorld, *, delay_s=0.0):
        self.scenario, self.video, self.world, self.delay_s = scenario, video, world, delay_s
        self.calls = 0
        self._calls_lock = threading.Lock()

    def detect(self, image_path, classes):
        from PIL import Image

        with self._calls_lock:
            self.calls += 1
            call_number = self.calls
        if self.delay_s:
            time.sleep(self.delay_s)
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            frame_number = rgb.getpixel((0, 0))[0] + 256 * rgb.getpixel((1, 0))[0]
        pose = self.video.pose_by_frame[frame_number]
        detections, bands = [], []
        for obj in self.scenario.objects:
            if obj.class_name not in classes:
                continue
            box = project_object(obj, pose)
            if box is None:
                continue
            detections.append({"class": obj.class_name, "bbox": box, "raw_score": None, "score_kind": None})
            bands.append(obj.band)
        gust = self.scenario.gust_after_observation
        if gust is not None and call_number == gust:
            self.world.gust(self.scenario.gust_heading_deg)
        return DetectionBatch(detections=tuple(detections), status="OK", bands=tuple(bands), dropped=(),
                              raw={"model": self.model})


# ---------------------------------------------------------------------------
# the scripted Jev
# ---------------------------------------------------------------------------


def requested_classes(request_text: str) -> tuple[str, ...]:
    text = request_text.lower()
    aliases = {"computer": r"\b(computers?|laptops?)\b", "phone": r"\b(phones?|smartphones?|mobiles?)\b",
               "person": r"\b(people|persons?|humans?)\b", "mug": r"\bmugs?\b"}
    found = [(match.start(), name) for name, pattern in aliases.items()
             for match in [re.search(pattern, text)] if match]
    return tuple(name for _, name in sorted(found))


class ScriptedJev:
    """A deterministic judgment model for goal compilation and room turns.

    Goal compilation: COUNT, the request's classes (a class group when it
    names several), otherwise the first controller-offered label. Room turns:
    an observation whenever one is offered (in the site's order), otherwise
    an answer, otherwise the first offer."""

    identity = "scripted-jev:rehearsal"

    def __init__(self, request_text: str, *, delay_s: float = 0.0):
        self.classes = requested_classes(request_text) or ("computer",)
        self.delay_s = delay_s
        self.names: list[str] = []

    def choice(self, state, instruction, *, criteria, name):
        self.names.append(name)
        if self.delay_s and name == "room_action":
            time.sleep(self.delay_s)
        labels = list(criteria)
        offered = [label for label in labels if label not in SAFE_LABELS] or labels
        pick = offered[0]
        if name == "goal_family" and "COUNT" in labels:
            pick = "COUNT"
        elif name == "target_classes":
            if len(self.classes) > 1 and "CLASS_GROUP" in labels:
                pick = "CLASS_GROUP"
            elif f"CLASS:{self.classes[0]}" in labels:
                pick = f"CLASS:{self.classes[0]}"
        elif name == "class_group":
            wanted = [f"INCLUDE:{c}" for c in self.classes if f"INCLUDE:{c}" in labels]
            pick = wanted[0] if wanted else ("GROUP_DONE" if "GROUP_DONE" in labels else pick)
        elif name == "room_action":
            observe = [label for label in offered if label.startswith("OBSERVE:")]
            answers = [label for label in offered if label.startswith("ANSWER")]
            pick = observe[0] if observe else (answers[0] if answers else pick)
        return {"value": pick, "probabilities": {label: .99 if label == pick else .001 for label in labels}}


# ---------------------------------------------------------------------------
# the composition
# ---------------------------------------------------------------------------


@dataclass
class RehearsalWorld:
    scenario: Scenario
    world: FakeTelloWorld
    video: FakeVideo
    detector: SceneDetector
    client: object
    link: object
    factory: object
    extras: dict = field(default_factory=dict)


def build_world(scenario: Scenario, *, run_id: str, run_dir: Path, pace: bool = True):
    """The fake aircraft behind the production real-Tello stack factory."""
    from sentinel.djitellopy_backend import DjitellopyClient
    from sentinel.room import live_stack

    world = FakeTelloWorld(motion_delay_s=0.4 if pace else 0.0)
    video = FakeVideo(world)
    detector = SceneDetector(scenario, video, world, delay_s=0.6 if pace else 0.0)
    client = DjitellopyClient(local_ip="192.168.10.2", tello_factory=lambda **_: world, video_factory=video,
                              socket_factory=lambda *a, **k: FakeEmergencySocket())
    link = live_stack.TelloLink(build_client=lambda: client, clock=time.monotonic)
    return RehearsalWorld(scenario, world, video, detector, client, link, None)


def flight_binding():
    from sentinel.room import live_session

    return live_session.load_flight_binding(
        site_path=SITE_PATH, drift_profile_path=DRIFT_PATH, energy_profile_path=ENERGY_PATH,
        command_limits_path=LIMITS_PATH, profile_paths=(FLIGHT_PROFILE_PATH,))


def trial_grant(binding, run_id: str, manifest):
    """The count_return TRIAL grant bound to the CanningSt profile files."""
    from sentinel.room.live_session import acceptance_manifest_for
    from sentinel.room.models import CapabilityGrant, canonical_grant_sha256

    acceptance = acceptance_manifest_for(manifest)
    grant = CapabilityGrant("count_return", "TRIAL", binding.site.cube_site_sha256,
                            binding.energy_profile.aircraft_id, binding.sha256s, (),
                            "2099-01-01T00:00:00+00:00", "", acceptance.manifest_sha256)
    return replace(grant, grant_sha256=canonical_grant_sha256(grant))


def rehearsal_manifest(binding, run_id: str, detector_identity: str):
    return SimpleNamespace(mode="acceptance", run_id=run_id, requested_capability="count_return",
                           aircraft_id=binding.energy_profile.aircraft_id,
                           requested_detector_identity=detector_identity, goal_parser_revision="mission-control")


def rehearsal_assurances(run_id: str, site, *, clock_domain_id="rehearsal-operator"):
    """Simulated operator clear-floor assurances (Rehearsal only; never Live)."""
    from sentinel.room.site import ContingencyAssurance

    now_ms = int(time.monotonic() * 1000)
    return tuple(
        ContingencyAssurance(f"rehearsal-{entry.contingency_id}", entry.contingency_id, run_id, clock_domain_id,
                             "CURRENT", datetime.now(timezone.utc).isoformat(), now_ms - 100,
                             now_ms + 30 * 60 * 1000, ("rehearsal:simulated-clear-floor",))
        for entry in site.terminal_contingencies)
