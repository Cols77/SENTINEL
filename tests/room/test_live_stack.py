"""Task A item 1: the production real-Tello flight stack and the L0 probes.

Everything here is OFFLINE. The real ``DjitellopyClient`` is driven over a
fake DJITelloPy object (``FakeTello``) and a fake video receiver, so the SDK
command sequence each adapter sends is asserted exactly while no socket,
adapter selection or provider call is ever made. Tag-pose bursts are
injected as ready-made ``DronePoseReport`` lists, except in the one test that
runs the default WP5 burst over rendered AprilTag frames.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import sqlite3
import time

import numpy as np
import pytest

from sentinel.contracts.pose import DronePoseReport, TagObservation
from sentinel.cube.checkpoint import CheckpointPlan, PlannedCommand
from sentinel.cube.energy import EnergySample
from sentinel.cube.localization import LocalizationState, LocalizationTracker
from sentinel.cube.step_executor import ExecutorState, HomeState
from sentinel.djitellopy_backend import DjitellopyClient
from sentinel.models import Position, VideoFrame
from sentinel.room import connectivity, live_session, live_stack
from tests.room import test_live_session as live_fakes

#: Commands the motors-off connectivity check must never send.
MOTOR_COMMANDS = ("takeoff", "motoron", "rc")
TAG_MAP_FIXTURE = "docs/superpowers/specs/contracts/fixtures/tag-map-v1.valid.json"


class FakeTello:
    """The DJITelloPy surface ``DjitellopyClient`` uses; records every SDK string sent."""

    def __init__(self, *, battery=87, sdk="unknown command: sdk?"):
        self.sent: list[str] = []
        self.battery = battery
        self.sdk = sdk
        self.height_dm = 0
        self.speed = 10
        self.ended = False

    def connect(self):
        self.sent.append("command")
        return "ok"

    def get_battery(self):  # the state stream: no SDK command is sent
        return self.battery

    def send_read_command(self, command):
        self.sent.append(command)
        return {"height?": f"{self.height_dm}dm", "speed?": str(self.speed)}[command]

    def query_sdk_version(self):
        self.sent.append("sdk?")
        return self.sdk

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
        self.height_dm = 10
        return "ok"

    def land(self):
        self.sent.append("land")
        self.height_dm = 0
        return "ok"

    def send_command_with_return(self, command, timeout=None):
        self.sent.append(command)
        return "ok"

    def end(self):
        self.ended = True


class FakeVideo:
    def __init__(self, **_values):
        self.frames = 0
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def read(self, timeout):
        # VideoReceiver hands PyAV's decoded ndarray through as the frame data.
        self.frames += 1
        return VideoFrame(width=4, height=4, pixel_format="rgb24", frame_number=self.frames,
                          data=np.arange(48, dtype=np.uint8).reshape(4, 4, 3))


class FakeTransport:
    """A client builder that returns the real backend over the fakes, counting builds."""

    def __init__(self, **tello_kwargs):
        self.tello = FakeTello(**tello_kwargs)
        self.builds = 0

    def __call__(self):
        self.builds += 1
        return DjitellopyClient(local_ip="192.168.10.2", tello_factory=lambda **_: self.tello,
                                video_factory=FakeVideo)


def reports(x_cm=100.0, y_cm=100.0, heading=90.0, n=10, tags=(1, 2)):
    now = datetime.now(timezone.utc)
    return [DronePoseReport(f"r{i}", "run", now, "LIVE", (x_cm / 10, y_cm / 10), heading, None,
                            "APRILTAG_36H11_PNP", tuple(TagObservation(t, "Z", 1.5, 0.5) for t in tags), 0.5)
            for i in range(n)]


class Bursts:
    """Injected pose bursts: each call returns the next scripted burst (the last one repeats)."""

    def __init__(self, *scripted):
        self.scripted = list(scripted) or [reports()]
        self.durations: list[float] = []

    def __call__(self, client, *, duration_s):
        self.durations.append(duration_s)
        return self.scripted.pop(0) if len(self.scripted) > 1 else self.scripted[0]


def sent_motor_commands(sent):
    return [command for command in sent if command.split()[0] in MOTOR_COMMANDS]


# ---------------------------------------------------------------------------
# L0: the motors-off drone probe
# ---------------------------------------------------------------------------


def test_drone_probe_reads_fresh_telemetry_sends_no_motor_command_and_closes():
    transport = FakeTransport(battery=87, sdk="30")
    result = live_stack.tello_drone_probe(build_client=transport, clock=lambda: 42.0)
    assert isinstance(result, connectivity.ProbeResult)
    assert result.checked_at == 42.0
    # Command mode, then the two read commands; battery comes from the state stream.
    assert transport.tello.sent == ["command", "height?", "sdk?"]
    assert sent_motor_commands(transport.tello.sent) == []
    assert transport.tello.ended is True
    assert "battery=87%" in result.identity and "height=0cm" in result.identity
    assert "sdk=30" in result.identity


def test_drone_probe_refuses_invalid_telemetry_and_still_closes():
    transport = FakeTransport(battery=150)
    with pytest.raises(live_stack.LiveStackError, match="battery"):
        live_stack.tello_drone_probe(build_client=transport)
    assert transport.tello.ended is True
    assert sent_motor_commands(transport.tello.sent) == []


def test_link_probe_keeps_one_connection_for_the_flight_stack():
    # DJITelloPy's receivers are process-global: the preflight probe and the
    # flight stack must share one connected client, never build a second one.
    transport = FakeTransport()
    link = live_stack.TelloLink(build_client=transport, clock=time.monotonic)
    link.probe()
    assert link.client() is link.client()
    assert transport.builds == 1 and transport.tello.ended is False
    link.close()
    assert transport.tello.ended is True
    with pytest.raises(live_stack.LiveStackError):
        link.client()


# ---------------------------------------------------------------------------
# L0: the provider reachability probe
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_provider_probe_checks_the_configured_key_and_model():
    calls = []

    def http_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return FakeResponse(200)

    environ = {"OPENROUTER_API_KEY": "test-key", "GEMINI_OPENROUTER_MODEL": "google/gemini-test"}
    result = live_stack.openrouter_provider_probe(environ=environ, http_get=http_get, clock=lambda: 7.0)
    assert result == connectivity.ProbeResult("openrouter:google/gemini-test", 7.0)
    assert calls == [(live_stack.OPENROUTER_KEY_URL, {"Authorization": "Bearer test-key"},
                      live_stack.PROVIDER_TIMEOUT_S)]


@pytest.mark.parametrize("environ, message", [
    ({"GEMINI_OPENROUTER_MODEL": "m"}, "OPENROUTER_API_KEY"),
    ({"OPENROUTER_API_KEY": "k"}, "GEMINI_OPENROUTER_MODEL"),
])
def test_provider_probe_names_a_missing_setting_without_calling_out(environ, message):
    def http_get(url, *, headers, timeout):
        raise AssertionError("no call without both settings")

    with pytest.raises(live_stack.LiveStackError, match=message):
        live_stack.openrouter_provider_probe(environ=environ, http_get=http_get)


def test_provider_probe_reads_the_live_detectors_own_key_and_model_settings():
    from scripts.survey_run import _PROVIDER_ENV

    key_env, model_env, _ = _PROVIDER_ENV["gemini-openrouter"]
    assert (live_stack.PROVIDER_KEY_ENV, live_stack.PROVIDER_MODEL_ENV) == (key_env, model_env)


def test_provider_probe_refuses_a_rejected_key_without_echoing_it():
    environ = {"OPENROUTER_API_KEY": "secret-value", "GEMINI_OPENROUTER_MODEL": "m"}
    with pytest.raises(live_stack.LiveStackError) as error:
        live_stack.openrouter_provider_probe(environ=environ,
                                             http_get=lambda url, *, headers, timeout: FakeResponse(401))
    assert "401" in str(error.value) and "secret-value" not in str(error.value)


# ---------------------------------------------------------------------------
# bindings: tag map and envelope
# ---------------------------------------------------------------------------


def test_bound_tag_map_refuses_a_file_whose_hash_is_not_the_manifests(tmp_path):
    raw = open(TAG_MAP_FIXTURE, "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    assert live_stack.load_bound_tag_map(TAG_MAP_FIXTURE, expected_sha256=digest).has_tag(0)
    with pytest.raises(live_stack.LiveStackError, match="tag map"):
        live_stack.load_bound_tag_map(TAG_MAP_FIXTURE, expected_sha256="0" * 64)


def test_site_envelope_is_the_semantic_envelope_volume():
    envelope = live_stack.site_envelope(live_fakes.fixture_site())
    assert (envelope.min_x_cm, envelope.max_x_cm, envelope.min_y_cm, envelope.max_y_cm) == (0, 200, 0, 200)
    assert (envelope.min_z_cm, envelope.max_z_cm) == (-20, 220)


def test_site_envelope_refuses_a_site_without_a_semantic_frame():
    with pytest.raises(live_stack.LiveStackError, match="semantic frame"):
        live_stack.site_envelope(replace(live_fakes.fixture_site(), semantic_frame=None))


# ---------------------------------------------------------------------------
# the flight stack
# ---------------------------------------------------------------------------


def build_stack(tmp_path, *, bursts=None, transport=None):
    transport = transport or FakeTransport()
    site = live_fakes.fixture_site()
    tracker = LocalizationTracker(live_fakes.drift_profile(), clock=time.monotonic, real_mode=True)
    link = live_stack.TelloLink(build_client=transport, clock=time.monotonic)
    factory = live_stack.TelloFlightStackFactory(
        link=link, tag_map=None, run_id="run-0001", run_dir=tmp_path, pose_burst=bursts or Bursts(),
        home_settle_s=0.0)
    stack = factory(tracker=tracker, clock=time.monotonic, site=site)
    return stack, transport.tello, tracker, site, link


def test_flight_stack_is_a_live_session_flight_stack_connected_motors_off(tmp_path):
    stack, tello, *_ = build_stack(tmp_path)
    assert isinstance(stack, live_session.FlightStack)
    assert stack.executor.state is ExecutorState.NEW
    # Connected and streaming (tag poses need video before takeoff); nothing else.
    assert tello.sent == ["command", "streamon"]


def test_refresh_anchors_the_tracker_from_a_tag_burst_and_reads_the_battery(tmp_path):
    stack, tello, tracker, site, _ = build_stack(tmp_path, bursts=Bursts(reports(101, 99, 91.0)))
    pose, sample = stack.refresh()
    assert pose.state is LocalizationState.ANCHORED
    assert pose.source == "mapped_joint_tags"
    assert (pose.position.x_cm, pose.position.y_cm, pose.position.z_cm) == (101, 99, 0)
    assert pose.heading_deg == pytest.approx(91.0)
    assert isinstance(sample, EnergySample) and sample.battery_pct == 87
    assert sent_motor_commands(tello.sent) == []


def test_refresh_without_a_reliable_burst_never_invents_a_pose(tmp_path):
    stack, _, tracker, *_ = build_stack(tmp_path, bursts=Bursts(reports(n=3)))
    pose, _ = stack.refresh()
    assert pose.state is LocalizationState.LOST  # never anchored: no fix is ever assumed


def test_start_takes_off_and_measures_home_from_tags_and_altimeter(tmp_path):
    stack, tello, tracker, site, _ = build_stack(tmp_path)
    stack.refresh()
    home = stack.executor.start(site.home)
    assert isinstance(home, HomeState)
    assert (home.pose.x_cm, home.pose.y_cm, home.pose.z_cm) == (100, 100, 100)
    assert home.route_id == site.home.route_id
    # The runner's own preflight (one connection, one stream), takeoff, then
    # the altimeter read for the measured home height.
    assert tello.sent == ["command", "streamon", "height?", "height?", "speed 20", "speed?", "takeoff",
                          "height?"]
    assert stack.executor.state is ExecutorState.AIRBORNE


def test_refresh_after_takeoff_keeps_the_tracker_anchored_through_the_climb(tmp_path):
    stack, _, tracker, site, _ = build_stack(tmp_path)
    stack.refresh()  # anchored on the pad
    stack.executor.start(site.home)
    pose, _ = stack.refresh()
    assert pose.state is LocalizationState.ANCHORED
    assert pose.position.z_cm == 100


def _plan(*commands):
    return CheckpointPlan("cp", Position(100, 100, 100), Position(100, 100, 100), 90, 90, commands,
                          sum(command.upper_bound_s for command in commands))


def test_capture_returns_a_saved_frame_observation_and_localise_returns_the_tag_pose(tmp_path):
    bursts = Bursts(reports(), reports(), reports(), reports(104, 97))
    stack, _, _, site, _ = build_stack(tmp_path, bursts=bursts)
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    capture = PlannedCommand.from_mapping("CAPTURE", {"index": 0, "look_deg": 0}, upper_bound_s=10.0,
                                          interruptible=True)
    localise = PlannedCommand.from_mapping("LOCALISE", {"phase": "capture", "settle_s": 0.0}, upper_bound_s=13.5,
                                           interruptible=True)
    outcome = stack.executor.execute(_plan(capture, localise))
    assert outcome.status == "COMPLETED", outcome
    (observation,) = outcome.observation_ids
    saved = list((tmp_path / "captures").glob("*.png"))
    assert len(saved) == 1
    assert observation == f"frame-{saved[0].stem}-{hashlib.sha256(saved[0].read_bytes()).hexdigest()[:16]}"
    assert outcome.pose == Position(104, 97, 100)


def test_move_is_sent_once_and_propagates_the_tracker(tmp_path):
    stack, tello, tracker, site, _ = build_stack(tmp_path)
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    move = PlannedCommand.from_mapping("MOVE", {"axis": "x", "direction": "right", "distance_cm": 20,
                                                "optional": "true"}, upper_bound_s=8.0, interruptible=False)
    before = len(tello.sent)
    outcome = stack.executor.execute(_plan(move))
    assert outcome.status == "COMPLETED", outcome
    assert tello.sent[before:] == ["right 20"]
    assert outcome.pose == Position(120, 100, 100)
    # The tracker dead-reckons the acknowledged move (heading 90 = +y, so right = +x).
    assert tracker.health.position.x_cm == pytest.approx(120, abs=1e-6)


def test_emergency_lands_through_the_executor(tmp_path):
    stack, tello, _, site, _ = build_stack(tmp_path)
    stack.refresh()
    stack.executor.start(site.home)
    outcome = stack.emergency(stack.executor)
    assert outcome.state == "LANDED"
    assert tello.sent[-1] == "land"
    assert "emergency" not in tello.sent  # a forced landing, never the motor-stop datagram


def test_default_burst_runs_the_wp5_pose_stream_and_persists_reports(tmp_path):
    from tests.pose.test_live_stream import FakeClient, _render_mapped_tag
    from sentinel.pose.persistence import ensure_schema
    from sentinel.pose.tagmap import load_tag_map

    conn = sqlite3.connect(tmp_path / "pose.sqlite3")
    ensure_schema(conn)
    client = FakeClient([_render_mapped_tag(0)] * 3)
    client.video_enabled = True  # the flight stream stays open: the burst must not toggle it
    found = live_stack.wp5_pose_burst(client, load_tag_map(TAG_MAP_FIXTURE), conn, run_id="burst",
                                      duration_s=2.0)
    assert len(found) == 3
    assert client.video_enabled is True
    assert conn.execute("SELECT COUNT(*) FROM pose_events WHERE run_id = 'burst'").fetchone()[0] == 3
