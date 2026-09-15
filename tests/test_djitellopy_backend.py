import pytest

from sentinel.client import DroneClient
from sentinel.djitellopy_backend import (
    DjitellopyClient,
    EmergencyAborted,
)


class FakeSocket:
    def __init__(self, bindings):
        self.bindings = bindings
        self.closed = False

    def bind(self, address):
        self.bindings.append(address)

    def setblocking(self, _value):
        pass

    def sendto(self, _payload, _address):
        return 1

    def close(self):
        self.closed = True


class FakeVideo:
    def __init__(self, *, local_ip, port, socket_factory, **_kwargs):
        self.local_ip = local_ip
        self.port = port
        self.socket_factory = socket_factory
        self.started = False
        self.stopped = False

    def start(self):
        socket = self.socket_factory(object, object)
        socket.bind(("0.0.0.0", self.port))
        self.started = True

    def stop(self):
        self.stopped = True

    def read(self, timeout):
        return type(
            "Frame",
            (),
            {"width": 640, "height": 480, "pixel_format": "rgb24"},
        )()


class FakeTello:
    def __init__(self, *, socket_factory, **_kwargs):
        self.socket_factory = socket_factory
        self.commands = []
        self.closed = False
        self._height = 150
        self._speed = 10

        command_socket = socket_factory(object, object)
        command_socket.bind(("", 8889))
        state_socket = socket_factory(object, object)
        state_socket.bind(("0.0.0.0", 8890))

    def connect(self):
        self.commands.append("command")

    def close(self):
        self.closed = True

    def send_control_command(self, command):
        self.commands.append(command)
        return "ok"

    def send_command_without_return(self, command):
        self.commands.append(command)

    def get_battery(self):
        return "87"

    def get_height(self):
        return str(self._height)

    def query_speed(self):
        return str(self._speed)

    def query_sdk_version(self):
        return "3.0"

    def set_speed(self, speed):
        self._speed = speed

    def takeoff(self):
        self.commands.append("takeoff")

    def land(self):
        self.commands.append("land")

    def move(self, direction, distance):
        self.commands.append(f"{direction} {distance}")

    def rotate_clockwise(self, degrees):
        self.commands.append(f"cw {degrees}")

    def rotate_counter_clockwise(self, degrees):
        self.commands.append(f"ccw {degrees}")

    def stop(self):
        self.commands.append("stop")

    def streamon(self):
        self.commands.append("streamon")

    def streamoff(self):
        self.commands.append("streamoff")


def make_client(bindings):
    def socket_factory(*_args, **_kwargs):
        return FakeSocket(bindings)

    def tello_factory(**kwargs):
        return FakeTello(**kwargs)

    return DjitellopyClient(
        local_ip="192.168.10.2",
        tello_factory=tello_factory,
        socket_factory=socket_factory,
        video_factory=FakeVideo,
    )


def test_backend_binds_command_state_and_video_to_selected_local_ip():
    bindings = []
    client = make_client(bindings)

    client.connect()
    client.stream_on()

    assert bindings == [
        ("192.168.10.2", 8889),
        ("192.168.10.2", 8890),
        ("192.168.10.2", 11111),
    ]

    client.stream_off()
    client.close()


def test_backend_normalizes_djitellopy_telemetry_and_exposes_video_metadata():
    client = make_client([])
    assert isinstance(client, DroneClient)
    client.connect()

    assert client.battery() == 87
    assert client.height_cm() == 150
    assert client.speed_cm_per_second() == 10
    assert client.sdk_version() == "3.0"

    client.stream_on()
    frame = client.read_frame(timeout=0.1)
    assert (frame.width, frame.height, frame.pixel_format) == (640, 480, "rgb24")


def test_emergency_uses_dedicated_sender_and_latches_terminal_state():
    bindings = []
    client = make_client(bindings)
    client.connect()

    assert client.emergency() == "ok"

    with pytest.raises(EmergencyAborted):
        client.move("forward", 50)
    with pytest.raises(EmergencyAborted):
        client.land()

    tello = client._tello
    assert tello.commands == ["command"]
    assert bindings[-1] == ("192.168.10.2", 0)
    assert client.emergency_triggered is True
