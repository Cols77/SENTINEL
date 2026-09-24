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
import sys
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

# Recovery scenarios need successive viewpoint visits even though the real
# three-look sweep can see all six small table cells from the centre rear.
# Keep that sequencing local to those tests; the shipped site uses the full
# camera-generated matrix and offers all eight viewpoints.
THREE_VIEW_SITE = {
    "pose_visibility": (
        (CENTER, ("demo-x230-y190", "demo-x230-y230")),
        (LEFT, ("demo-x190-y190", "demo-x190-y230")),
        (RIGHT, ("demo-x270-y190", "demo-x270-y230")),
    ),
    "pose_tag_visibility": ((CENTER, (0, 2)), (LEFT, (0, 2)), (RIGHT, (0, 2))),
}


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
        #: Task V2: at this x (a region's checkpoint) the tags are out of view
        #: for ``blind_bursts`` bursts (None: always).
        self.blind_x_cm: float | None = None
        self.blind_bursts: int | None = None
        self.blind_hits = 0

    def takeoff(self):
        self.flying = True
        result = super().takeoff()
        # This room's Tello reports about 40 cm immediately after takeoff
        # although its true height is 70–80 cm (operator measurement).
        self.height_dm = 4
        return result

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
        if (self.blind_x_cm is not None and abs(self.x_cm - self.blind_x_cm) < 1
                and (self.blind_bursts is None or self.blind_hits < self.blind_bursts)):
            self.blind_hits += 1
            return []
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


def binding(site_changes=None, **drift_changes):
    loaded = live_session.load_flight_binding(
        site_path=SITE_PATH, drift_profile_path=PROFILES / "canningst-drift.json",
        energy_profile_path=PROFILES / "canningst-energy.json",
        command_limits_path=PROFILES / "canningst-command-limits.json",
        profile_paths=(PROFILES / "canningst-flight-profile.json",))
    if site_changes:
        loaded = replace(loaded, site=replace(loaded.site, **site_changes))
    if drift_changes:
        loaded = replace(loaded, drift_profile=replace(loaded.drift_profile, **drift_changes))
    return loaded


def run_mission(tmp_path, monkeypatch, *, battery=90, yaw_gust_after_capture=None, site_changes=None,
                blind_x_cm=None, blind_bursts=None, **drift_changes):
    """Fly the CanningSt count mission; returns (outcome, tello, store events, detector).

    ``yaw_gust_after_capture``: right after that many captures, a gust yaws
    the aircraft 15 deg clockwise (to 75 deg); one more burst still glimpses
    the tags there (the tracker learns the new heading), then they stay out
    of view until the aircraft faces them again."""
    # No real waiting: SETTLE/capture windows collapse to zero on the fake stack.
    monkeypatch.setattr(live_stack._TelloStack, "_within", staticmethod(lambda upper_bound_s: 0.0))
    flight = binding(site_changes, **drift_changes)
    tello = FlyingTello(battery=battery)
    tello.blind_x_cm, tello.blind_bursts = blind_x_cm, blind_bursts
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


PATH_REFUSALS = {("RETURN_NOW", "OPTIONAL_PATH_INVALID"), ("RETURN_NOW", "PROJECTED_TERMINATION_PATH_INVALID"),
                 ("RETURN_NOW", "PROJECTED_TERMINATION_LANDING_INVALID")}


def refusals(events):
    return {(e.payload.get("transition"), e.payload.get("reason")) for e in events
            if e.event_type == "SAFETY_ASSESSED" and e.payload.get("transition") == "RETURN_NOW"}


def landed_over_the_pad(outcome, tello):
    return (outcome.took_off and outcome.landing is not None and outcome.landing.state == "LANDED"
            and tello.sent.index("takeoff") < tello.sent.index("land") and not tello.flying
            and abs(tello.x_cm - 250) < 1 and abs(tello.y_cm - 190) < 1)


def test_with_the_committed_profiles_one_sweep_covers_the_table_then_returns_and_lands(
        tmp_path, monkeypatch):
    """The six camera-generated table cells fit one centre-rear sweep.

    One OBSERVE now means three detector frames; the old three strip visits
    were a consequence of hand-written visibility, not a coverage target.
    """
    outcome, tello, events, detector = run_mission(tmp_path, monkeypatch)
    assert landed_over_the_pad(outcome, tello), outcome
    assert succeeded_observations(events) == [CENTER] and detector.calls == 3, \
        (succeeded_observations(events), detector.calls, refusals(events),
         [(e.payload.get("transition"), e.payload.get("reason")) for e in events
          if e.event_type == "SAFETY_ASSESSED"][:8])
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") in tool_outcomes(events)
    assert not refusals(events) & PATH_REFUSALS, refusals(events)
    # Task V2: the session records the room loop's Jev acceptance thresholds.
    policy = [e.payload for e in events if e.event_type == "ROOM_ACCEPTANCE_POLICY"]
    assert len(policy) == 1 and policy[0]["observe_act_at"] == .5 and policy[0]["answer_act_at"] == .6
    assert not any(c.split()[0] in ("left", "right") for c in tello.sent)


def test_with_an_absurd_drift_the_count_mission_returns_without_observing(tmp_path, monkeypatch):
    """The 0.45 cm/cm excluded outlier blocks even the nearest new view.

    The centre-rear pose is now 20 cm from home, so there is no old
    zero-translation centre observation to admit under extreme drift.
    """
    outcome, tello, events, detector = run_mission(tmp_path, monkeypatch, xy_drift_per_cm=0.45)
    assert landed_over_the_pad(outcome, tello), outcome
    assert not succeeded_observations(events) and detector.calls == 0
    assert any(e.event_type == "SAFETY_ASSESSED" and e.payload.get("transition") != "CONTINUE"
               for e in events)


@pytest.mark.parametrize("capture_number", [1, 3])
def test_a_tag_losing_yaw_gust_during_a_sweep_ends_safely_without_translation(
        tmp_path, monkeypatch, capture_number):
    """An early or late tag loss during the three-look sweep ends safely.

    The measured pose is already 20 cm behind the pad. The aircraft either
    lands there, or -- when the lateral zones are refused on their own and
    Jev answers -- returns the 20 cm to the pad and lands on it; the lateral
    continuation is never flown.
    """
    outcome, tello, events, detector = run_mission(
        tmp_path, monkeypatch, yaw_gust_after_capture=capture_number, site_changes=THREE_VIEW_SITE)
    assert outcome.landing is not None and outcome.landing.state == "LANDED"
    assert not tello.flying and abs(tello.x_cm - 250) < 1 and round(tello.y_cm) in (170, 190)
    assert any(transition == "LAND_CONTINGENCY" for transition, _ in safety_reasons(events)), safety_reasons(events)
    assert not any(c.split()[0] in ("left", "right") for c in tello.sent)
    assert set(succeeded_observations(events)) <= {CENTER}
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") not in tool_outcomes(events)


def test_at_30_percent_battery_the_mission_is_refused_before_takeoff(tmp_path, monkeypatch):
    with pytest.raises(live_session.LiveSessionError, match="takeoff refused"):
        run_mission(tmp_path, monkeypatch, battery=30)


# ---------------------------------------------------------------------------
# The conservative recovery path for a forced multi-view sequence.
# ---------------------------------------------------------------------------

#: The pre-fix-1 declared hover drift (0.1 cm/s): with a fixless out-and-back
#: no lateral region is admissible at it, so it isolates the credit. The
#: drift per cm stays the history value (0.157 cm/cm).
DOUBLED_HOVER = {"xy_drift_cm_s": 0.1}


def credited(events):
    return {e.payload.get("expected_reanchor") for e in events
            if e.event_type == "SAFETY_ASSESSED" and e.payload.get("expected_reanchor")}


def safety_reasons(events):
    return [(e.payload.get("transition"), e.payload.get("reason")) for e in events
            if e.event_type == "SAFETY_ASSESSED"]


def zone_refusals(events):
    return [e.payload for e in events
            if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("code") == "ZONE_REFUSED"]


def test_if_the_next_region_is_not_admissible_it_is_refused_and_the_aircraft_answers_from_the_pad(
        tmp_path, monkeypatch):
    """Force more than one visit only in this test's visibility matrix.

    The first centre sweep succeeds. The next 40 cm leg to left-rear fails
    the forecast's xy-uncertainty gate although the aircraft is freshly
    anchored, so a re-anchor cannot help: that zone alone is refused, with a
    reason Jev sees, and no translation is issued. With nothing else
    reachable Jev answers incomplete and the aircraft returns to the pad.
    The production matrix does not demand this second visit.
    """
    outcome, tello, events, detector = run_mission(tmp_path, monkeypatch, site_changes=THREE_VIEW_SITE)
    assert succeeded_observations(events) == [CENTER] and detector.calls == 3
    refused = zone_refusals(events)
    assert refused and all("tag fix" in item["detail"] for item in refused)
    assert ("LAND_CONTINGENCY", "recovery_exhausted") not in safety_reasons(events)
    assert outcome.landing is not None and outcome.landing.state == "LANDED"
    assert not tello.flying and abs(tello.x_cm - 250) < 1 and abs(tello.y_cm - 190) < 1
    assert not any(c.split()[0] in ("left", "right") for c in tello.sent)
    assert not [e for e in events if e.event_type == "POSE_REANCHORED"]
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") not in tool_outcomes(events)


def test_without_declared_tags_doubled_hover_drift_never_claims_reanchor(tmp_path, monkeypatch):
    """An inflated drift profile plus no declared tags gets no speculative fix."""
    outcome, tello, events, detector = run_mission(
        tmp_path, monkeypatch, site_changes={**THREE_VIEW_SITE, "pose_tag_visibility": ()}, **DOUBLED_HOVER)
    assert outcome.landing is not None and outcome.landing.state == "LANDED"
    assert not credited(events) and not succeeded_observations(events)
    assert not any(c.split()[0] in ("left", "right") for c in tello.sent)


def test_no_left_tag_fix_does_not_cause_an_unsafe_lateral_leg(tmp_path, monkeypatch):
    """The left blind source is unreachable because the forecast refuses its leg."""
    outcome, tello, events, detector = run_mission(
        tmp_path, monkeypatch, blind_x_cm=210.0, site_changes=THREE_VIEW_SITE, **DOUBLED_HOVER)
    assert zone_refusals(events)
    assert outcome.landing is not None and outcome.landing.state == "LANDED" and not tello.flying
    assert tello.blind_hits == 0 and not any(c.split()[0] in ("left", "right") for c in tello.sent)
    assert not [e for e in events if e.event_type == "POSE_REANCHORED"]


def test_delayed_left_tags_are_not_assumed_before_the_lateral_leg(tmp_path, monkeypatch):
    """Even a future fix cannot justify travel before that fix exists."""
    outcome, tello, events, detector = run_mission(
        tmp_path, monkeypatch, blind_x_cm=210.0, blind_bursts=2,
        site_changes=THREE_VIEW_SITE, **DOUBLED_HOVER)
    assert zone_refusals(events)
    assert outcome.landing is not None and outcome.landing.state == "LANDED" and not tello.flying
    assert tello.blind_hits == 0 and not any(c.split()[0] in ("left", "right") for c in tello.sent)
    assert not [e for e in events if e.event_type == "POSE_REANCHORED"]


def test_an_off_mark_takeoff_still_observes_and_answers(tmp_path, monkeypatch):
    """Live 2026-09-24: the Tello took off 35-50 cm from the declared pad.
    Declared poses follow the measured home, so the mission still observes
    the table and answers its count."""
    original = FlyingTello
    monkeypatch.setattr(sys.modules[__name__], "FlyingTello",
                        lambda **kwargs: original(x_cm=256.0, y_cm=225.0, **kwargs))
    outcome, tello, events, detector = run_mission(tmp_path, monkeypatch)
    assert outcome.landing is not None and outcome.landing.state == "LANDED", outcome
    assert succeeded_observations(events) == [CENTER], refusals(events)
    assert ("ANSWER:COUNTS", "ANSWERED_COUNT") in tool_outcomes(events)
