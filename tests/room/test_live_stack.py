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

    def get_height(self):  # the state stream: no SDK command is sent
        return self.height_dm * 10

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


class FakeEmergencySocket:
    """The raw UDP socket ``_send_emergency_datagram`` opens: no real network I/O.

    The direct motor-stop path deliberately bypasses DJITelloPy's own
    ``_tello`` object (bench B1: an interrupted command must not block it), so
    it is not reachable through ``FakeTello``; this is its own fake transport.
    """

    def __init__(self, *_args, **_kwargs):
        self.sent: tuple | None = None

    def setblocking(self, _value):
        pass

    def bind(self, _address):
        pass

    def sendto(self, payload, address):
        self.sent = (payload, address)
        return len(payload)

    def close(self):
        pass


class FakeTransport:
    """A client builder that returns the real backend over the fakes, counting builds."""

    def __init__(self, **tello_kwargs):
        self.tello = FakeTello(**tello_kwargs)
        self.builds = 0

    def __call__(self):
        self.builds += 1
        return DjitellopyClient(local_ip="192.168.10.2", tello_factory=lambda **_: self.tello,
                                video_factory=FakeVideo,
                                socket_factory=lambda *a, **k: FakeEmergencySocket())


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


def test_drone_probe_refuses_djitellopys_own_never_answered_timeout_text():
    # Q2: DJITelloPy's send_command_with_return returns this exact string on
    # its own timeout, not a reply from the aircraft. Accepting it would
    # fabricate a live SDK link.
    transport = FakeTransport(sdk="Aborting command 'sdk?'. Did not receive a response after 7 seconds")
    with pytest.raises(live_stack.LiveStackError, match="never answered sdk"):
        live_stack.tello_drone_probe(build_client=transport)
    assert transport.tello.ended is True


def test_drone_probe_normalizes_an_unknown_command_reply_to_unsupported():
    # SDK 1.3 does not define sdk?; "unknown command: sdk?" is still a real
    # reply from the aircraft (a live link), recorded plainly rather than
    # echoing the raw protocol string.
    transport = FakeTransport(sdk="unknown command: sdk?")
    result = live_stack.tello_drone_probe(build_client=transport, clock=lambda: 1.0)
    assert "sdk=unsupported" in result.identity
    assert "unknown command" not in result.identity


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


def build_stack(tmp_path, *, bursts=None, transport=None, clock=time.monotonic):
    transport = transport or FakeTransport()
    site = live_fakes.fixture_site()
    tracker = LocalizationTracker(live_fakes.drift_profile(), clock=clock, real_mode=True)
    link = live_stack.TelloLink(build_client=transport, clock=clock)
    factory = live_stack.TelloFlightStackFactory(
        link=link, tag_map=None, run_id="run-0001", run_dir=tmp_path, pose_burst=bursts or Bursts(),
        home_settle_s=0.0)
    stack = factory(tracker=tracker, clock=clock, site=site)
    return stack, transport.tello, tracker, site, link


class FrozenClock:
    """A monotonic clock that moves only when a test moves it: a command's
    supervised deadline can never pass merely because the machine is busy."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _inner(stack):
    """The underlying ``_TelloStack`` behind a built ``FlightStack`` (via its bound ``refresh``)."""
    return stack.refresh.__self__


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


def test_an_unusable_battery_read_keeps_the_last_reading_instead_of_aborting(tmp_path):
    stack, tello, *_ = build_stack(tmp_path, bursts=Bursts(reports(101, 99, 91.0)))
    assert stack.refresh()[1].battery_pct == 87
    tello.battery = 150
    assert stack.refresh()[1].battery_pct == 87


def test_a_drifting_or_failed_height_reading_keeps_the_dead_reckoned_height(tmp_path):
    stack, tello, tracker, *_ = build_stack(tmp_path, bursts=Bursts(reports(101, 99, 91.0)))
    first = stack.refresh()[0].position.z_cm
    _inner(stack)._climb_applied = True  # airborne: the tracker height is now the reference
    tello.height_dm = 20  # a 2 m jump: never chased
    assert stack.refresh()[0].position.z_cm == first
    tello.height_dm = -1  # an invalid read: never aborts the refresh
    assert stack.refresh()[0].position.z_cm == first


def test_a_consistent_single_tag_burst_reanchors_instead_of_demanding_recovery(tmp_path):
    bursts = Bursts(reports(101, 99, 91.0), reports(108, 99, 92.0, tags=(1,)), reports(300, 99, 92.0, tags=(1,)))
    stack, *_ = build_stack(tmp_path, bursts=bursts)
    assert stack.refresh()[0].state is LocalizationState.ANCHORED
    pose = stack.refresh()[0]
    assert pose.state is LocalizationState.ANCHORED and pose.position.x_cm == 108
    assert pose.source == "mapped_single_tag"
    pose = stack.refresh()[0]  # a far single-tag view is ignored: the tracker coasts
    assert pose.state is LocalizationState.COASTING and pose.position.x_cm == 108


def test_refresh_without_a_reliable_burst_never_invents_a_pose(tmp_path):
    stack, _, tracker, *_ = build_stack(tmp_path, bursts=Bursts(reports(n=3)))
    pose, _ = stack.refresh()
    assert pose.state is LocalizationState.LOST  # never anchored: no fix is ever assumed


def test_a_tagless_burst_coasts_and_never_counts_as_an_estimator_failure(tmp_path):
    # Permissive positioning: losing the tags while hovering or looking is
    # not a failure; the supervisor demands a fix only before a translation.
    stack, _, tracker, *_ = build_stack(tmp_path, bursts=Bursts(reports(n=3)))
    before = tracker.health.estimator_failures
    stack.refresh()
    assert tracker.health.estimator_failures == before


def test_refresh_never_anchors_from_single_tag_per_frame_bursts_even_with_two_tags_overall(tmp_path):
    # Q3: _TagFix.tag_ids used to union tag IDs over the whole burst, so 11
    # frames seeing only tag 1 plus 1 frame seeing only tag 2 looked like
    # mapped_joint_tags evidence. Only a report that itself saw two or more
    # mapped tags is real joint-tag evidence.
    now = datetime.now(timezone.utc)
    single_tag_reports = [
        DronePoseReport(f"r{i}", "run", now, "LIVE", (10.0, 10.0), 90.0, None, "APRILTAG_36H11_PNP",
                        (TagObservation(1 if i < 11 else 2, "Z", 1.5, 0.5),), 0.5)
        for i in range(12)
    ]
    stack, _, tracker, *_ = build_stack(tmp_path, bursts=Bursts(single_tag_reports))
    pose, _ = stack.refresh()
    assert pose.state is not LocalizationState.ANCHORED  # must not anchor: no frame saw two mapped tags


def test_refresh_bounds_its_tag_burst_well_under_the_full_wp5_burst_bound(tmp_path):
    # Q4: refresh() runs inside SafetySupervisor's watchdog polling loop; a
    # tagless burst must never block it anywhere near the full WP5 burst
    # bound (BURST_MAX_S) that measure_home and LOCALISE use.
    bursts = Bursts(reports(n=3))
    stack, *_ = build_stack(tmp_path, bursts=bursts)
    stack.refresh()
    assert bursts.durations[-1] == live_stack.REFRESH_BURST_S
    assert live_stack.REFRESH_BURST_S < live_stack.BURST_MAX_S


def test_refresh_during_an_intentional_look_does_not_count_as_an_estimator_failure(tmp_path):
    # Q4: a ROTATE(phase="look") makes the aircraft deliberately point away
    # from the tags; a tagless refresh while looking must not be counted as
    # an estimator failure the way an unexplained tagless burst is.
    bursts = Bursts(reports(), reports(), reports(n=3))
    stack, tello, tracker, site, _ = build_stack(tmp_path, bursts=bursts)
    stack.refresh()
    stack.executor.start(site.home)
    look = PlannedCommand.from_mapping("ROTATE", {"degrees": 90, "direction": "cw", "phase": "look"},
                                       upper_bound_s=5.0, interruptible=False)
    outcome = stack.executor.execute(_plan(look))
    assert outcome.status == "COMPLETED", outcome
    before_failures = tracker.health.estimator_failures
    pose, _ = stack.refresh()
    assert pose.state is not LocalizationState.ANCHORED
    assert tracker.health.estimator_failures == before_failures  # never counted: an intentional look


def test_start_takes_off_and_measures_home_from_tags_and_altimeter(tmp_path):
    stack, tello, tracker, site, _ = build_stack(tmp_path)
    stack.refresh()
    home = stack.executor.start(site.home)
    assert isinstance(home, HomeState)
    assert (home.pose.x_cm, home.pose.y_cm, home.pose.z_cm) == (100, 100, 100)
    assert home.route_id == site.home.route_id
    # refresh() before start() (one height? read) then the runner's own
    # preflight (another height? read), takeoff. S1: measure_home's own
    # height comes from the passive state field (no SDK command), so no
    # trailing height? follows takeoff here.
    assert tello.sent == ["command", "streamon", "height?", "height?", "speed 20", "speed?", "takeoff"]
    assert stack.executor.state is ExecutorState.AIRBORNE


def test_measure_home_reads_height_passively_issuing_no_sdk_command(tmp_path):
    # S1: StepExecutor's documented measure_home contract is that it "must
    # return data without publishing state or issuing SDK commands" --
    # refresh()'s own height read (still an explicit height? by design) and
    # preflight's altitude check are the only height? reads in this sequence.
    stack, tello, _, site, _ = build_stack(tmp_path)
    stack.refresh()
    stack.executor.start(site.home)
    assert tello.sent.count("height?") == 2


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
    frame_path, captured_at, heading_deg = stack.capture_records[observation]
    assert frame_path == saved[0]
    assert datetime.fromisoformat(captured_at).tzinfo is not None
    assert heading_deg == pytest.approx(90)
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


def test_trigger_emergency_reaches_the_runner_sends_the_motor_stop_and_latches_the_executor(tmp_path):
    # Q1: FlightStack.trigger_emergency is the direct motor-stop path
    # (FlightTestRunner.trigger_emergency), never routed through FakeTello --
    # it bypasses the SDK command queue entirely (bench B1).
    stack, tello, _, site, _ = build_stack(tmp_path)
    stack.refresh()
    stack.executor.start(site.home)
    assert stack.trigger_emergency is not None
    stack.trigger_emergency()
    assert stack.executor.state is ExecutorState.TERMINAL
    commands = [record.command for record in stack.frame_source.command_history]
    assert "emergency" in commands
    assert "land" not in tello.sent  # never the graceful forced landing


def test_rotate_is_sent_once_with_the_exact_sdk_command_and_tracks_the_look(tmp_path):
    stack, tello, tracker, site, _ = build_stack(tmp_path)
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    look = PlannedCommand.from_mapping("ROTATE", {"degrees": 30, "direction": "cw", "phase": "look"},
                                       upper_bound_s=5.0, interruptible=False)
    before = len(tello.sent)
    outcome = stack.executor.execute(_plan(look))
    assert outcome.status == "COMPLETED", outcome
    assert tello.sent[before:] == ["cw 30"]
    assert _inner(stack)._looking_away is True
    restore = PlannedCommand.from_mapping("ROTATE", {"degrees": 30, "direction": "ccw", "phase": "restore"},
                                          upper_bound_s=5.0, interruptible=False)
    outcome = stack.executor.execute(_plan(restore))
    assert outcome.status == "COMPLETED", outcome
    assert tello.sent[before + 1:] == ["ccw 30"]
    assert _inner(stack)._looking_away is False


def test_retarget_to_side_heading_suppresses_expected_tagless_refresh(tmp_path):
    bursts = Bursts(reports(), reports(), reports(), reports(n=3))
    stack, _, tracker, site, _ = build_stack(tmp_path, bursts=bursts)
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    retarget = PlannedCommand.from_mapping(
        "ROTATE", {"degrees": 90, "direction": "ccw", "phase": "retarget"},
        upper_bound_s=5.0, interruptible=False)
    assert stack.executor.execute(_plan(retarget)).status == "COMPLETED"
    assert _inner(stack)._looking_away is True
    before = tracker.health.estimator_failures
    stack.refresh()
    assert tracker.health.estimator_failures == before


def test_set_speed_is_sent_once_with_the_exact_sdk_command(tmp_path):
    stack, tello, tracker, site, _ = build_stack(tmp_path)
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    command = PlannedCommand.from_mapping("SET_SPEED", {"speed_cm_s": 30}, upper_bound_s=5.0, interruptible=False)
    before = len(tello.sent)
    outcome = stack.executor.execute(_plan(command))
    assert outcome.status == "COMPLETED", outcome
    assert tello.sent[before:] == ["speed 30"]


def test_read_telemetry_vertical_sends_the_exact_height_read(tmp_path):
    stack, tello, tracker, site, _ = build_stack(tmp_path)
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    command = PlannedCommand.from_mapping("READ_TELEMETRY", {"phase": "vertical"}, upper_bound_s=5.0,
                                          interruptible=True)
    before = len(tello.sent)
    outcome = stack.executor.execute(_plan(command))
    assert outcome.status == "COMPLETED", outcome
    assert tello.sent[before:] == ["height?"]


def test_settle_sends_no_sdk_command(tmp_path):
    # Task B2: on the real clock a 0.05 s SETTLE deadline could pass while the
    # supervised worker thread waited for a busy CPU (DEADLINE_EXCEEDED once
    # under load). The frozen clock makes the deadline a pure function of
    # the test; the production deadline logic is unchanged.
    stack, tello, tracker, site, _ = build_stack(tmp_path, clock=FrozenClock())
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    command = PlannedCommand.from_mapping("SETTLE", {}, upper_bound_s=0.05, interruptible=True)
    before = len(tello.sent)
    outcome = stack.executor.execute(_plan(command))
    assert outcome.status == "COMPLETED", outcome
    assert tello.sent[before:] == []


def test_start_with_no_reliable_fix_after_takeoff_raises_and_lands_never_assuming_home(tmp_path):
    # Q7: "measured provenance only / no nominal fallback" -- a fix-less
    # airborne burst must raise (never fall back to the nominal pad pose) and
    # the executor's own cleanup must still land.
    from sentinel.cube.step_executor import HomeMeasurementError

    stack, tello, _, site, _ = build_stack(tmp_path, bursts=Bursts(reports(n=3)))
    stack.refresh()
    with pytest.raises(HomeMeasurementError):
        stack.executor.start(site.home)
    assert "land" in tello.sent  # the executor's own cleanup lands after the failed measurement
    assert stack.executor.home is None


def test_localise_with_no_reliable_fix_fails_the_checkpoint(tmp_path):
    bursts = Bursts(reports(), reports(), reports(), reports(n=3))
    stack, _, _, site, _ = build_stack(tmp_path, bursts=bursts)
    stack.refresh()
    stack.executor.start(site.home)
    stack.refresh()
    localise = PlannedCommand.from_mapping("LOCALISE", {"phase": "capture", "settle_s": 0.0}, upper_bound_s=13.5,
                                           interruptible=True)
    outcome = stack.executor.execute(_plan(localise))
    assert outcome.status == "FAILED"


def test_cancelled_localise_never_sends_the_height_read(tmp_path):
    from threading import Event

    cancellation = Event()

    def burst(client, *, duration_s):
        cancellation.set()  # a lifecycle preemption arrives mid-burst
        return reports()

    stack, tello, _, site, _ = build_stack(tmp_path, bursts=burst)
    stack.refresh()
    stack.executor.start(site.home)
    before = len(tello.sent)
    localise = PlannedCommand.from_mapping("LOCALISE", {"phase": "capture", "settle_s": 0.0}, upper_bound_s=13.5,
                                           interruptible=True)
    with pytest.raises(RuntimeError, match="cancelled"):
        _inner(stack)._localise(localise, cancellation)
    assert "height?" not in tello.sent[before:]


def test_factory_wires_the_manifests_max_duration_s_into_the_executor_deadline(tmp_path):
    # S2/Q5: the manifest's own declared flight-duration bound must reach
    # StepExecutor's deadline (DEADLINE_EXCEEDED before each command; it also
    # caps SafetySupervisor._land's landing upper bound).
    transport = FakeTransport()
    site = live_fakes.fixture_site()
    tracker = LocalizationTracker(live_fakes.drift_profile(), clock=lambda: 100.0, real_mode=True)
    link = live_stack.TelloLink(build_client=transport, clock=lambda: 100.0)
    factory = live_stack.TelloFlightStackFactory(
        link=link, tag_map=None, run_id="run-0001", run_dir=tmp_path, pose_burst=Bursts(),
        home_settle_s=0.0, max_duration_s=300.0)
    stack = factory(tracker=tracker, clock=lambda: 100.0, site=site)
    assert stack.executor.deadline == 400.0


def test_factory_without_max_duration_s_leaves_the_executor_deadline_unbound(tmp_path):
    stack, *_ = build_stack(tmp_path)
    assert stack.executor.deadline is None


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


# ---------------------------------------------------------------------------
# Task B item 2c: the recovery adapter the supervisor re-anchors with
# ---------------------------------------------------------------------------


def test_recovery_burst_returns_one_joint_tag_pose_report_per_frame(tmp_path):
    from sentinel.cube.recovery import PoseReport, RecoveryAction
    stack, tello, tracker, site, _ = build_stack(tmp_path, bursts=Bursts(reports(120, 150, 91.0)))
    tello.sent.clear()
    action = RecoveryAction("a1", "STATIONARY_BURST", 90.0, upper_bound_s=5.0)
    found = stack.recovery_burst(action)
    assert len(found) == 10 and all(isinstance(report, PoseReport) for report in found)
    fix = found[0].fix
    assert (fix.position.x_cm, fix.position.y_cm) == (120, 150)
    assert fix.tag_ids == (1, 2) and fix.source == "mapped_joint_tags"
    assert len({report.report_id for report in found}) == 10
    assert tello.sent == []  # a supervised wait operation: data only, no SDK command


def test_recovery_burst_marks_single_tag_reports_and_never_turns_unreliable_bursts_into_reports(tmp_path):
    from sentinel.cube.recovery import SINGLE_TAG_SOURCE, RecoveryAction
    action = RecoveryAction("a1", "STATIONARY_BURST", 90.0, upper_bound_s=5.0)
    single, *_ = build_stack(tmp_path, bursts=Bursts(reports(tags=(1,))))
    fixes = [report.fix for report in single.recovery_burst(action)]
    # Single-tag evidence is marked so the recovery policy accepts it only
    # when it agrees with the current estimate, with widened uncertainty.
    assert fixes and all(f.source == SINGLE_TAG_SOURCE and f.xy_uncertainty_cm >= 15 for f in fixes)
    short, *_ = build_stack(tmp_path, bursts=Bursts(reports(n=3)))
    assert short.recovery_burst(action) == ()


def test_refresh_skips_its_burst_while_a_recovery_burst_holds_the_video_reader(tmp_path):
    bursts = Bursts(reports())
    stack, _, tracker, *_ = build_stack(tmp_path, bursts=bursts)
    inner = _inner(stack)
    before = tracker.health.estimator_failures
    with inner._burst_lock:
        stack.refresh()
    assert bursts.durations == []  # never waited for, never read the stream
    assert tracker.health.estimator_failures == before  # a skipped burst is not an estimator failure


def test_mapped_heading_tag_ids_faces_the_wall_tags():
    from sentinel.pose.tagmap import TagMap
    doc = {"schema_version": "tag-map-v1", "tags": [
        {"tag_id": 2, "zone_id": "S0", "x_units": 21.56, "y_units": 40.0, "z_units": 0.53, "yaw_deg": -90.0,
         "size_m": 0.192, "mounting": "WALL"},
        {"tag_id": 0, "zone_id": "S1", "x_units": 28.44, "y_units": 40.0, "z_units": 0.53, "yaw_deg": -90.0,
         "size_m": 0.192, "mounting": "WALL"},
        {"tag_id": 7, "zone_id": "S2", "x_units": 0.0, "y_units": 20.0, "z_units": 0.53, "yaw_deg": 0.0,
         "size_m": 0.192, "mounting": "WALL"}],
        "camera_calibration": {"fx": 1, "fy": 1, "cx": 1, "cy": 1, "dist_coeffs": [0, 0, 0, 0, 0]}}
    assert live_stack.mapped_heading_tag_ids(TagMap(doc)) == ((90.0, (0, 2)),)
    assert live_stack.mapped_heading_tag_ids(None) == ()


def test_factory_exposes_the_recovery_adapter_to_the_live_session(tmp_path):
    stack, *_ = build_stack(tmp_path)
    assert stack.recovery_burst is not None
    assert stack.mapped_heading_tag_ids == ()  # no tag map bound in this fixture: no yaw scan


# ---------------------------------------------------------------------------
# Task B (L3): the live Jev judge and detector defaults
# ---------------------------------------------------------------------------


def test_default_jev_builds_the_openrouter_jev_judge_without_calling_it(monkeypatch):
    monkeypatch.delenv("SENTINEL_SYSTEM_ONE_MODEL", raising=False)
    judge = live_stack.default_jev(environ={live_stack.PROVIDER_KEY_ENV: "k"})
    assert judge.identity.startswith("jev:")
    inner = judge._judge
    assert inner._api_key_env == live_stack.PROVIDER_KEY_ENV and inner._base_url == live_stack.JEV_BASE_URL
    assert inner._timeout_s < 10  # below the controller's provider wait


def test_default_jev_posts_to_the_pinned_openrouter_decisions_route(monkeypatch):
    # Regression: the default alias ~typesafe/jev-latest and an ".../api" base
    # (which JevJudge turned into /api/api/alpha/decisions) made every live
    # goal compile fail with an HTTP error in Mission Control's Jev rehearsal.
    from sentinel.coordinator.proposer_factory import JEV_PROPOSER_MODEL

    judge = live_stack.default_jev(environ={live_stack.PROVIDER_KEY_ENV: "k"})
    inner = judge._judge
    assert judge.identity == f"jev:{JEV_PROPOSER_MODEL}"
    assert f"{inner._base_url}{inner.ENDPOINT_PATH}" == "https://openrouter.ai/api/alpha/decisions"

    calls = []
    inner._transport = lambda payload: calls.append(payload) or {
        "answers": {"room_action": {"choice": "A", "probabilities": {"A": 0.9, "B": 0.1}}}}
    monkeypatch.setenv(live_stack.PROVIDER_KEY_ENV, "k")
    answer = judge.choice({"s": 1}, "pick", criteria={"A": "a", "B": "b"}, name="room_action")
    assert answer["value"] == "A" and len(calls) == 1


def test_default_jev_and_detector_refuse_without_the_key_by_name():
    with pytest.raises(live_stack.LiveStackError, match=live_stack.PROVIDER_KEY_ENV):
        live_stack.default_jev(environ={})
    with pytest.raises(live_stack.LiveStackError, match=live_stack.PROVIDER_KEY_ENV):
        live_stack.default_detector(environ={})


def test_default_detector_is_the_pinned_gemini_openrouter_detector_with_an_identity():
    detector = live_stack.default_detector(environ={live_stack.PROVIDER_KEY_ENV: "k",
                                                    live_stack.PROVIDER_MODEL_ENV: "google/gemini-test"})
    assert detector.provider == "gemini-openrouter" and detector.model == "google/gemini-test"
    assert detector.identity == "gemini-openrouter:google/gemini-test"


def test_start_without_a_pad_fix_anchors_the_tracker_on_the_airborne_home(tmp_path):
    # Takeoff is not gated on a pad fix: with no refresh before start, the
    # airborne home burst is the tracker's first anchor.
    stack, _, tracker, site, _ = build_stack(tmp_path)
    assert not tracker.initialized
    home = stack.executor.start(site.home)
    assert tracker.initialized
    assert tracker.health.state is LocalizationState.ANCHORED
    assert (tracker.health.position.x_cm, tracker.health.position.y_cm) == (home.pose.x_cm, home.pose.y_cm)
