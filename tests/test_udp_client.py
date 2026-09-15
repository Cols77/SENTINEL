import socket
import time
from collections import deque
from queue import Empty
from threading import Event, Thread

import pytest

from sentinel.client import DroneError, EmergencyAborted
from sentinel.udp_client import (
    TelloStateListener,
    UdpTelloClient,
    format_move,
    format_rotate,
    parse_response,
    parse_state,
)


class FakeSocket:
    def __init__(self, responses=(), events=None):
        self.responses = deque(responses)
        self.events = events if events is not None else []
        self.bound_to = None
        self.timeout = None
        self.sent = []
        self.closed = False
        self.close_calls = 0

    def bind(self, address):
        self.bound_to = address
        self.events.append(("bind", address))

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, payload, address):
        self.sent.append((payload, address))
        self.events.append(("sendto", payload, address))
        return len(payload)

    def recvfrom(self, _size):
        if self.closed:
            raise OSError("socket closed")
        if self.responses:
            return self.responses.popleft(), ("192.168.10.1", 8889)
        raise socket.timeout()

    def close(self):
        self.close_calls += 1
        self.closed = True
        self.events.append(("close", self))


class BlockingCommandSocket(FakeSocket):
    def __init__(self, responses=(), events=None):
        super().__init__(responses, events)
        self.recv_started = Event()
        self.release_recv = Event()

    def recvfrom(self, size):
        if self.responses:
            return super().recvfrom(size)
        self.recv_started.set()
        self.release_recv.wait(timeout=5.0)
        return b"ok", ("192.168.10.1", 8889)


class FakeStateListener:
    def __init__(self, local_ip, *, socket_factory, events):
        self.local_ip = local_ip
        self.socket_factory = socket_factory
        self.events = events
        self.latest_state = {}
        self.started = False
        self.closed = False

    def start(self):
        self.started = True
        self.events.append(("state-start", self.local_ip))

    def close(self):
        if not self.closed:
            self.closed = True
            self.events.append(("state-close", self.local_ip))


class FakeVideo:
    def __init__(self, local_ip, events=None, read_result=None):
        self.local_ip = local_ip
        self.events = events if events is not None else []
        self.read_result = read_result
        self.started = False
        self.closed = False
        self.close_calls = 0

    def start(self):
        self.started = True
        self.events.append(("video-start", self.local_ip))

    def read(self, timeout):
        assert timeout == 2.5
        if isinstance(self.read_result, BaseException):
            raise self.read_result
        return self.read_result

    def close(self):
        self.close_calls += 1
        self.closed = True
        self.events.append(("video-close", self.local_ip))


def make_client(responses, *, events=None, video_factory=None):
    events = [] if events is None else events
    command_socket = FakeSocket(responses, events)
    state = FakeStateListener

    def socket_factory(*_args):
        return command_socket

    def state_factory(local_ip, *, socket_factory):
        return state(local_ip, socket_factory=socket_factory, events=events)

    client = UdpTelloClient(
        "192.168.10.2",
        timeout=1.5,
        socket_factory=socket_factory,
        state_listener_factory=state_factory,
        video_factory=video_factory or UdpVideoClientFactory,
    )
    return client, command_socket, events


def UdpVideoClientFactory(_local_ip):
    raise AssertionError("video factory should be injected for stream tests")


def test_commands_use_tello_centimetre_and_degree_syntax():
    assert format_move("forward", 50) == "forward 50"
    assert format_move("up", 20) == "up 20"
    assert format_rotate("cw", 90) == "cw 90"


@pytest.mark.parametrize("direction", ["hover", "CW", ""])
def test_format_move_rejects_unsupported_directions(direction):
    with pytest.raises(ValueError):
        format_move(direction, 20)


@pytest.mark.parametrize("distance", [19, 501, True])
def test_format_move_rejects_distances_outside_inclusive_range(distance):
    with pytest.raises(ValueError):
        format_move("forward", distance)


@pytest.mark.parametrize("direction", ["clockwise", "stop", ""])
def test_format_rotate_rejects_unsupported_directions(direction):
    with pytest.raises(ValueError):
        format_rotate(direction, 90)


@pytest.mark.parametrize("degrees", [0, 361, False])
def test_format_rotate_rejects_degrees_outside_inclusive_range(degrees):
    with pytest.raises(ValueError):
        format_rotate("ccw", degrees)


def test_responses_strip_whitespace_normalize_case_and_replace_invalid_utf8():
    assert parse_response(b" OK\r\n") == "ok"
    assert parse_response(b"error\n") == "error"
    assert parse_response(b"\xff OK ") == "� ok"


def test_state_parser_maps_semicolon_separated_fields_and_ignores_malformed_parts():
    assert parse_state(b"pitch:1;roll:-2;bat:87;;not-a-field;temp:20;pitch:3;") == {
        "pitch": "3",
        "roll": "-2",
        "bat": "87",
        "temp": "20",
    }


def test_state_listener_binds_8890_updates_latest_state_and_closes_idempotently():
    events = []
    state_socket = FakeSocket([b"pitch:1;bat:88;"], events)
    listener = TelloStateListener(
        "192.168.10.2",
        timeout=0.01,
        socket_factory=lambda *_args: state_socket,
    )

    listener.start()
    deadline = time.monotonic() + 0.5
    while listener.latest_state != {"pitch": "1", "bat": "88"}:
        if time.monotonic() >= deadline:
            pytest.fail("state listener did not receive the fake datagram")
        time.sleep(0.005)

    listener.close()
    listener.close()

    assert state_socket.bound_to == ("192.168.10.2", 8890)
    assert state_socket.timeout == 0.01
    assert state_socket.close_calls == 1
    assert listener.thread.daemon is True
    assert not listener.thread.is_alive()
    assert listener.socket is None


def test_client_connect_binds_command_socket_and_records_normalized_response():
    events = []
    client, command_socket, _ = make_client([b" OK\r\n"], events=events)

    client.connect()

    assert command_socket.bound_to == ("192.168.10.2", 0)
    assert command_socket.timeout == 1.5
    assert command_socket.sent == [
        (b"command", ("192.168.10.1", 8889)),
    ]
    assert client.records[0].command == "command"
    assert client.records[0].response == "ok"

    client.close()
    assert events.index(("state-close", "192.168.10.2")) < events.index(("close", command_socket))


def test_telemetry_parsing_and_state_changes_require_valid_acknowledgements():
    client, _, _ = make_client(
        [
            b"ok",  # command
            b"87",  # battery?
            b"42",  # height?
            b"20",  # speed?
            b"2.0",  # sdk?
            b"ok",  # speed 30
            b"ok",  # takeoff
            b"ok",  # forward 20
            b"ok",  # cw 90
            b"ok",  # stop
            b"ok",  # land
        ]
    )
    client.connect()

    assert client.battery() == 87
    assert client.height_cm() == 42
    assert client.speed_cm_per_second() == 20
    assert client.sdk_version() == "2.0"
    assert client.set_speed(30) == "ok"
    assert client.takeoff() == "ok"
    assert client.airborne is True
    assert client.move("forward", 20) == "ok"
    assert client.rotate("cw", 90) == "ok"
    assert client.stop() == "ok"
    assert client.land() == "ok"
    assert client.airborne is False


def test_telemetry_rejects_non_integer_responses_and_speed_rejects_invalid_values():
    client, _, _ = make_client([b"ok", b"not-an-integer"])
    client.connect()

    with pytest.raises(DroneError, match="non-integer"):
        client.battery()
    with pytest.raises(ValueError):
        client.set_speed(9)
    with pytest.raises(ValueError):
        client.set_speed(101)


def test_commands_that_require_ok_raise_without_mutating_takeoff_state():
    client, _, _ = make_client([b"ok", b"error"])
    client.connect()

    with pytest.raises(DroneError, match="takeoff"):
        client.takeoff()
    assert client.airborne is False


def test_stream_on_and_off_manage_video_and_cleanup_when_ack_fails():
    events = []
    videos = []

    def video_factory(local_ip):
        video = FakeVideo(local_ip, events, read_result=(640, 480))
        videos.append(video)
        return video

    client, _, _ = make_client([b"ok", b"ok", b"ok"], events=events, video_factory=video_factory)
    client.connect()
    assert client.stream_on() == "ok"
    assert client.video is videos[0]
    assert videos[0].started is True
    assert client.read_frame(2.5) == (640, 480)
    assert client.stream_off() == "ok"
    assert videos[0].closed is True
    assert client.video is None

    failing, _, _ = make_client([b"ok", b"error"], video_factory=video_factory)
    failing.connect()
    with pytest.raises(DroneError, match="streamon"):
        failing.stream_on()
    assert videos[-1].closed is True
    assert failing.video is None


def test_stream_off_closes_video_even_when_tello_rejects_command():
    events = []
    video = FakeVideo("192.168.10.2", events)
    client, _, _ = make_client([b"ok", b"error"], events=events, video_factory=lambda _ip: video)
    client.connect()
    client.video = video

    with pytest.raises(DroneError, match="streamoff"):
        client.stream_off()
    assert video.closed is True
    assert client.video is None


def test_read_frame_translates_queue_timeout_to_drone_error():
    video = FakeVideo("192.168.10.2", read_result=Empty())
    client, _, _ = make_client([b"ok"], video_factory=lambda _ip: video)
    client.connect()
    client.video = video

    with pytest.raises(DroneError, match="video frame"):
        client.read_frame(2.5)


def test_emergency_sends_without_waiting_latches_and_prevents_later_commands():
    client, command_socket, _ = make_client([b"ok"])
    client.connect()

    assert client.emergency() == "ok"
    assert command_socket.sent[-1] == (b"emergency", ("192.168.10.1", 8889))
    assert client.records[-1].command == "emergency"
    assert client.records[-1].response == "ok"
    with pytest.raises(EmergencyAborted):
        client.stop()

    assert client.emergency() == "ok"
    assert [payload for payload, _ in command_socket.sent].count(b"emergency") == 1


def test_emergency_does_not_wait_behind_blocked_normal_response():
    events = []
    command_socket = BlockingCommandSocket([b"ok"], events)
    emergency_socket = FakeSocket(events=events)
    sockets = deque([command_socket, emergency_socket])

    def socket_factory(*_args):
        return sockets.popleft()

    def state_factory(local_ip, *, socket_factory):
        return FakeStateListener(local_ip, socket_factory=socket_factory, events=events)

    client = UdpTelloClient(
        "192.168.10.2",
        timeout=5.0,
        socket_factory=socket_factory,
        state_listener_factory=state_factory,
    )
    client.connect()

    normal_result = []
    normal_thread = Thread(target=lambda: normal_result.append(client.stop()))
    normal_thread.start()
    assert command_socket.recv_started.wait(timeout=0.5)

    started_at = time.monotonic()
    assert client.emergency() == "ok"
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    assert emergency_socket.bound_to == ("192.168.10.2", 0)
    assert emergency_socket.sent == [
        (b"emergency", ("192.168.10.1", 8889)),
    ]
    assert emergency_socket.closed is True
    assert client.emergency_triggered is True

    command_socket.release_recv.set()
    normal_thread.join(timeout=0.5)
    assert not normal_thread.is_alive()
    assert normal_result == ["ok"]
