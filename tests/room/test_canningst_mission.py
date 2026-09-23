"""Task B2: CanningSt count missions end to end on the fake flight stack.

The production composition (``live_session.run_count_return``) over the real
``live_stack`` Tello stack, driven through a fake DJITelloPy that flies
exactly what it is commanded and a tag burst that reports where the fake
aircraft is (perfect tags). Jev and the detector are deterministic fakes.
Nothing reaches hardware, the network or a provider.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np
import pytest

from sentinel.cube.localization import LocalizationState
from sentinel.cube.step_executor import ExecutorState
from sentinel.djitellopy_backend import DjitellopyClient
from sentinel.models import VideoFrame
from sentinel.room import live_session, live_stack
from sentinel.room.live_session import acceptance_manifest_for
from sentinel.room.models import CapabilityGrant, CapabilityProfile, canonical_grant_sha256
from sentinel.room.store import RoomStore
from sentinel.survey.detector import DetectionBatch
from tests.room import test_live_session as live_fakes
from tests.room.test_live_stack import FakeEmergencySocket, FakeTello, reports

ROOT = Path(__file__).resolve().parents[2]
SITE_PATH = ROOT / "fixtures" / "room_agent" / "v1" / "canningst-site.json"
PROFILES = ROOT / "fixtures" / "room_agent" / "v1" / "profiles"
TAG_MAP_PATH = ROOT / "docs" / "wp5-flight3-map.json"
REQUEST = "count the phones and computers in the room"
DETECTOR_ID = "gemini-openrouter:google/gemini-test"
CENTER, LEFT, RIGHT = ("center-rear-middle-toward_targets", "left-rear-middle-toward_targets",
                       "right-rear-middle-toward_targets")


class FlyingTello(FakeTello):
    """A fake DJITelloPy that is where it was told to go: axis moves at the
    canonical heading (+y forward), yaw, and the altimeter follow the SDK."""

    def __init__(self, *, x_cm=250.0, y_cm=190.0, battery=90):
        super().__init__(battery=battery)
        self.x_cm, self.y_cm, self.heading_deg = x_cm, y_cm, 90.0
        self.flying = False
        #: The two wall tags are in view only within this many degrees of the
        #: canonical heading (they stand straight ahead, +y).
        self.tag_view_deg = 180.0
        self.glimpses = 0  # bursts that still see the tags while yawed away
        self.bursts: list[float] = []

    def takeoff(self):
        self.flying = True
        return super().takeoff()

    def land(self):
        self.flying = False
        return super().land()

    def send_command_with_return(self, command, timeout=None):
        self.sent.append(command)
        verb, _, value = command.partition(" ")
        if verb in ("up", "down"):
            self.height_dm += (1 if verb == "up" else -1) * int(value) // 10
        elif verb in ("forward", "back", "left", "right"):
            import math
            offset = {"forward": 0, "back": 180, "left": 90, "right": -90}[verb]
            angle = math.radians(self.heading_deg + offset)
            self.x_cm += int(value) * math.cos(angle)
            self.y_cm += int(value) * math.sin(angle)
        elif verb in ("cw", "ccw"):
            self.heading_deg = (self.heading_deg + (1 if verb == "ccw" else -1) * int(value)) % 360
        return "ok"

    def burst(self, client, *, duration_s):
        self.bursts.append(self.heading_deg)
        off = abs((self.heading_deg - 90.0 + 180.0) % 360.0 - 180.0)
        if off > self.tag_view_deg:
            if not self.glimpses:
                return []
            self.glimpses -= 1
        return reports(self.x_cm, self.y_cm, self.heading_deg, n=10, tags=(0, 2))


class Video:
    """A 960x720 frame (the Tello's stream size), so boxes land in the calibrated camera."""

    def __init__(self, **_values):
        self.frames = 0

    def start(self):
        pass

    def stop(self):
        pass

    def read(self, timeout):
        self.frames += 1
        return VideoFrame(width=960, height=720, pixel_format="rgb24", frame_number=self.frames,
                          data=np.zeros((720, 960, 3), dtype=np.uint8))


class Detector:
    """A computer and a phone on the demo surface ahead of every pose."""

    provider = "fake-provider"

    def __init__(self):
        self.calls = 0

    def detect(self, image_path, classes):
        self.calls += 1
        detections = (
            {"class": "computer", "bbox": {"x": 380, "y": 600, "width": 200, "height": 100},
             "raw_score": None, "score_kind": None},
            {"class": "phone", "bbox": {"x": 600, "y": 640, "width": 40, "height": 30},
             "raw_score": None, "score_kind": None},
        )
        return DetectionBatch(detections=tuple(d for d in detections if d["class"] in classes), status="OK",
                              bands=tuple("high" for d in detections if d["class"] in classes), dropped=(),
                              raw={"model": DETECTOR_ID})


class ObservingJudge(live_fakes.PolicyJudge):
    """COUNT for the goal; in the room loop, an observation whenever one is
    offered (in the site's own order), otherwise an answer or the first offer."""

    def choice(self, state, instruction, *, criteria, name):
        if name == "room_action":
            observe = [label for label in criteria if label.startswith("OBSERVE:")]
            if observe:
                self.names.append(name)
                pick = observe[0]
                return {"value": pick, "probabilities": {label: .99 if label == pick else .001 for label in criteria}}
        return super().choice(state, instruction, criteria=criteria, name=name)


def binding(**drift_changes):
    loaded = live_session.load_flight_binding(
        site_path=SITE_PATH, drift_profile_path=PROFILES / "canningst-drift.json",
        energy_profile_path=PROFILES / "canningst-energy.json",
        command_limits_path=PROFILES / "canningst-command-limits.json",
        profile_paths=(PROFILES / "canningst-flight-profile.json",))
    if drift_changes:
        loaded = replace(loaded, drift_profile=replace(loaded.drift_profile, **drift_changes))
    return loaded


def run_mission(tmp_path, monkeypatch, *, battery=90, yaw_gust_after_capture=None, **drift_changes):
    """Fly the CanningSt count mission; returns (outcome, tello, store events, detector).

    ``yaw_gust_after_capture``: right after that many captures, a gust yaws
    the aircraft 15 deg clockwise (to 75 deg); one more burst still glimpses
    the tags there (the tracker learns the new heading), then they stay out
    of view until the aircraft faces them again."""
    # No real waiting: SETTLE/capture windows collapse to zero on the fake stack.
    monkeypatch.setattr(live_stack._TelloStack, "_within", staticmethod(lambda upper_bound_s: 0.0))
    flight = binding(**drift_changes)
    tello = FlyingTello(battery=battery)
    client = DjitellopyClient(local_ip="192.168.10.2", tello_factory=lambda **_: tello, video_factory=Video,
                              socket_factory=lambda *a, **k: FakeEmergencySocket())
    link = live_stack.TelloLink(build_client=lambda: client, clock=time.monotonic)
    tag_map = live_stack.load_bound_tag_map(TAG_MAP_PATH, expected_sha256=flight.site.tag_map_sha256)
    factory = live_stack.TelloFlightStackFactory(link=link, tag_map=tag_map, run_id="run-0001",
                                                 run_dir=tmp_path / "run", pose_burst=tello.burst,
                                                 home_settle_s=0.0)
    if yaw_gust_after_capture is not None:
        original = live_stack._TelloStack._capture
        gusts = {yaw_gust_after_capture} if isinstance(yaw_gust_after_capture, int) else set(yaw_gust_after_capture)

        def capture(stack, command):
            observation = original(stack, command)
            if len(list((tmp_path / "run" / "captures").glob("*.png"))) in gusts:
                tello.heading_deg, tello.tag_view_deg, tello.glimpses = 75.0, 5.0, 1
            return observation

        monkeypatch.setattr(live_stack._TelloStack, "_capture", capture)
    manifest = SimpleNamespace(mode="acceptance", run_id="run-0001", requested_capability="count_return",
                               aircraft_id=flight.energy_profile.aircraft_id,
                               requested_detector_identity=DETECTOR_ID, goal_parser_revision="rev-1")
    acceptance = acceptance_manifest_for(manifest)
    grant = CapabilityGrant("count_return", "TRIAL", flight.site.cube_site_sha256, flight.energy_profile.aircraft_id,
                            flight.sha256s, (), "2099-01-01T00:00:00+00:00", "", acceptance.manifest_sha256)
    grant = replace(grant, grant_sha256=canonical_grant_sha256(grant))
    store = RoomStore.open(tmp_path / "room.sqlite3")
    store.create_session("run-0001", REQUEST, CapabilityProfile((grant,)))
    detector = Detector()
    judge = ObservingJudge("jev:typesafe-ai/jev")
    try:
        outcome = live_session.run_count_return(
            store=store, manifest=manifest, site=flight.site, drift_profile=flight.drift_profile,
            energy_profile=flight.energy_profile, limits=flight.limits,
            calibration_profiles=flight.calibration_profiles, flight_stack_factory=factory, judge=judge,
            detector=detector, assurances=live_fakes.operator_assurances("run-0001", flight.site),
            request_text=REQUEST, frames_dir=tmp_path / "frames", clock=time.monotonic,
            wall_clock=lambda: datetime(2026, 9, 24, tzinfo=timezone.utc), before_takeoff=lambda: None)
        events = store.events("run-0001")
    finally:
        store.close()
        link.close()
    return outcome, tello, events, detector


def tool_outcomes(events):
    return [(e.payload.get("candidate_id"), e.payload.get("code")) for e in events
            if e.event_type == "ROOM_TOOL_OUTCOME"]


def succeeded_observations(events):
    return [candidate.rsplit(":", 1)[-1] for candidate, code in tool_outcomes(events)
            if code == "OBSERVE_SUCCEEDED"]


def refusals(events):
    return {(e.payload.get("transition"), e.payload.get("reason")) for e in events
            if e.event_type == "SAFETY_ASSESSED" and e.payload.get("transition") == "RETURN_NOW"}


def landed_over_the_pad(outcome, tello):
    return (outcome.took_off and outcome.landing is not None and outcome.landing.state == "LANDED"
            and tello.sent.index("takeoff") < tello.sent.index("land") and not tello.flying
            and abs(tello.x_cm - 250) < 1 and abs(tello.y_cm - 190) < 1)


def test_with_the_regenerated_profiles_the_count_mission_observes_the_center_returns_and_lands(tmp_path, monkeypatch):
    """The committed CanningSt profiles at 90 %: the center region is
    observed (frame captured, detector run, detections grounded); left and
    right are refused by the supervisor (their fixless out-and-back cannot
    stay 12 cm + drift clear of the rear face 30 cm behind the pad), so the
    aircraft returns over the pad and lands."""
    outcome, tello, events, detector = run_mission(tmp_path, monkeypatch)
    assert landed_over_the_pad(outcome, tello), outcome
    assert succeeded_observations(events) == [CENTER] and detector.calls == 1
    assert refusals(events) & {("RETURN_NOW", "OPTIONAL_PATH_INVALID"),
                               ("RETURN_NOW", "PROJECTED_TERMINATION_PATH_INVALID")}


def test_a_geometry_feasible_drift_flies_all_three_regions_then_returns_and_lands(tmp_path, monkeypatch):
    """The full mission mechanics on the fake stack: three region
    observations, the count answer, the return and the landing -- under a
    drift small enough for CanningSt's left/right geometry (0.03 cm/cm,
    0.005 deg/cm, 0.02 cm/s, 0.01 deg/s; the history does not support it)."""
    outcome, tello, events, detector = run_mission(
        tmp_path, monkeypatch, xy_drift_per_cm=0.03, heading_drift_per_cm=0.005, xy_drift_cm_s=0.02,
        heading_drift_deg_s=0.01)
    assert landed_over_the_pad(outcome, tello), outcome
    assert sorted(succeeded_observations(events)) == sorted([CENTER, LEFT, RIGHT]) and detector.calls == 3
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") in tool_outcomes(events)


def test_a_yaw_gust_that_loses_the_tags_is_recovered_by_a_yaw_to_the_mapped_heading_and_the_mission_resumes(
        tmp_path, monkeypatch):
    """Task B2 item 4 on canningst-site.json: after the first capture a gust
    yaws the aircraft to 75 deg; the tracker learns it from one last glimpse,
    then the wall tags stay out of view. The estimator failures make the
    pose RECOVERY_REQUIRED; the supervisor's recovery (not Jev) yaws back to
    the mapped-tag heading (ccw 15 to 90 deg), a tag fix is accepted there,
    and the count mission resumes: every region observed, return, landing."""
    outcome, tello, events, detector = run_mission(
        tmp_path, monkeypatch, yaw_gust_after_capture=1, xy_drift_per_cm=0.03, heading_drift_per_cm=0.005,
        xy_drift_cm_s=0.02, heading_drift_deg_s=0.01)
    assert_recovered_by_yaw(outcome, tello, events, recoveries=1)


def test_two_yaw_gusts_in_one_flight_are_both_recovered(tmp_path, monkeypatch):
    """A second recovery in the same flight is a fresh episode, never
    "recovery exhausted" (the watchdog's refresh used to re-anchor the first
    one behind the policy's back, leaving its episode closed-but-unsucceeded,
    and the second tag loss then landed in place)."""
    outcome, tello, events, detector = run_mission(
        tmp_path, monkeypatch, yaw_gust_after_capture=(1, 3), xy_drift_per_cm=0.03, heading_drift_per_cm=0.005,
        xy_drift_cm_s=0.02, heading_drift_deg_s=0.01)
    assert_recovered_by_yaw(outcome, tello, events, recoveries=2)


def assert_recovered_by_yaw(outcome, tello, events, *, recoveries):
    assert landed_over_the_pad(outcome, tello), outcome
    reasons = [(e.payload.get("transition"), e.payload.get("reason")) for e in events
               if e.event_type == "SAFETY_ASSESSED"]
    assert not any(transition == "LAND_CONTINGENCY" and reason != "LANDING_APPROACH"
                   for transition, reason in reasons), reasons
    assert any(transition == "RECOVER" for transition, _ in reasons)
    assert tello.sent.count("ccw 15") == recoveries  # the recovery's yaw back to the mapped heading
    # The policy's own audited re-anchor, at the mapped heading, after each yaw.
    reanchors = [e.payload for e in events if e.event_type == "POSE_REANCHORED"]
    assert len(reanchors) == recoveries and all(abs(p["heading_deg"] - 90.0) < 1 for p in reanchors)
    last_yaw = len(tello.sent) - 1 - tello.sent[::-1].index("ccw 15")
    assert any(command.split()[0] in ("left", "right") for command in tello.sent[last_yaw:])  # resumed
    assert sorted(set(succeeded_observations(events))) == sorted([CENTER, LEFT, RIGHT])
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") in tool_outcomes(events)


def test_at_30_percent_battery_the_mission_is_refused_before_takeoff(tmp_path, monkeypatch):
    with pytest.raises(live_session.LiveSessionError, match="takeoff refused"):
        run_mission(tmp_path, monkeypatch, battery=30)
