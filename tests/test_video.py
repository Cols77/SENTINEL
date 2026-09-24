import threading
import time

import pytest
from av.error import InvalidDataError

from sentinel.video import (
    VideoDecoderError,
    VideoReadTimeout,
    VideoReceiver,
)


class FakeSocket:
    def __init__(self, packets=()):
        self.packets = list(packets)
        self.bindings = []
        self.closed = False
        self.timeout = None

    def bind(self, address):
        self.bindings.append(address)

    def settimeout(self, timeout):
        self.timeout = timeout

    def recvfrom(self, _size):
        if self.packets:
            return self.packets.pop(0), ("192.168.10.1", 11111)
        while not self.closed:
            time.sleep(0.001)
            if self.packets:
                return self.packets.pop(0), ("192.168.10.1", 11111)
        raise OSError("socket closed")

    def close(self):
        self.closed = True


class FakeArray:
    shape = (480, 640, 3)


class FakeFrame:
    width = 640
    height = 480

    def __init__(self, formats):
        self.formats = formats

    def to_ndarray(self, *, format):
        self.formats.append(format)
        return FakeArray()


class FakeDecoder:
    def __init__(self, formats):
        self.formats = formats
        self.packets = []

    def decode(self, packet):
        self.packets.append(packet)
        return [FakeFrame(self.formats)]


def test_video_receiver_binds_ip_and_decodes_dimensions_in_explicit_pixel_format():
    fake_socket = FakeSocket(
        [b"a" * 1460, b"frame-1", b"b" * 1460, b"frame-2"]
    )
    formats = []
    decoder = FakeDecoder(formats)

    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        port=11111,
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=lambda: decoder,
        packet_factory=lambda payload: payload,
        pixel_format="rgb24",
    )
    receiver.start()

    frame = receiver.read(timeout=1.0)
    assert (frame.width, frame.height) == (640, 480)
    assert frame.pixel_format == "rgb24"
    deadline = time.monotonic() + 1.0
    while len(decoder.packets) < 2 and time.monotonic() < deadline:
        time.sleep(0.001)
    assert formats == ["rgb24", "rgb24"]
    assert decoder.packets == [
        b"a" * 1460 + b"frame-1",
        b"b" * 1460 + b"frame-2",
    ]
    assert fake_socket.bindings == [("192.168.10.2", 11111)]

    receiver.stop()
    assert fake_socket.closed is True


def test_video_receiver_decodes_reassembled_payload_as_one_codec_packet():
    fake_socket = FakeSocket([b"a" * 1460, b"fragment-2"])
    formats = []

    class DirectDecoder(FakeDecoder):
        def parse(self, payload):
            raise AssertionError("assembled Tello frames must use direct packets")

    decoder = DirectDecoder(formats)
    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=lambda: decoder,
        packet_factory=lambda payload: payload,
    )

    receiver.start()
    frame = receiver.read(timeout=1.0)
    assert frame.width == 640
    receiver.stop()

    assert decoder.packets == [b"a" * 1460 + b"fragment-2"]


def test_video_receiver_records_reassembled_h264_stream(tmp_path):
    fake_socket = FakeSocket([b"a" * 1460, b"frame"])
    decoder = FakeDecoder([])
    output = tmp_path / "tello.h264"
    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=lambda: decoder,
        packet_factory=lambda payload: payload,
        record_path=output,
    )

    receiver.start()
    receiver.read(timeout=1.0)
    receiver.stop()

    assert output.read_bytes() == b"a" * 1460 + b"frame"


def test_recording_write_failure_does_not_stop_decoding(tmp_path):
    fake_socket = FakeSocket()
    receiver = VideoReceiver(
        local_ip="192.168.10.2", socket_factory=lambda *_a, **_k: fake_socket,
        decoder_factory=lambda: FakeDecoder([]), packet_factory=lambda payload: payload,
        record_path=tmp_path / "tello.h264",
    )

    class BrokenFile:
        def write(self, _payload):
            raise OSError("disk full")

        def close(self):
            pass

    receiver.start()
    receiver._record_file.close()
    receiver._record_file = BrokenFile()
    fake_socket.packets.append(b"frame")
    frame = receiver.read(timeout=1.0)
    receiver.stop()

    assert frame.width == 640
    assert "disk full" in receiver.recording_error


def test_recording_close_failure_is_reported_without_failing_stop(tmp_path):
    receiver = VideoReceiver(local_ip="192.168.10.2", record_path=tmp_path / "tello.h264")

    class BrokenFile:
        def close(self):
            raise OSError("flush failed")

    receiver._record_file = BrokenFile()
    receiver.stop()

    assert "flush failed" in receiver.recording_error


def test_recording_open_failure_keeps_live_frames_available(tmp_path):
    receiver = VideoReceiver(
        local_ip="192.168.10.2", socket_factory=lambda *_a, **_k: FakeSocket([b"frame"]),
        decoder_factory=lambda: FakeDecoder([]), packet_factory=lambda payload: payload,
        record_path=tmp_path,
    )

    receiver.start()
    frame = receiver.read(timeout=1.0)
    receiver.stop()

    assert frame.width == 640
    assert receiver.recording_error is not None


def test_slow_recording_disk_does_not_delay_live_decode(tmp_path):
    fake_socket = FakeSocket()
    decoder = FakeDecoder([])
    receiver = VideoReceiver(
        local_ip="192.168.10.2", socket_factory=lambda *_a, **_k: fake_socket,
        decoder_factory=lambda: decoder, packet_factory=lambda payload: payload,
        record_path=tmp_path / "flight.h264",
    )
    writing = threading.Event()
    release = threading.Event()

    class SlowFile:
        def write(self, _payload):
            writing.set()
            release.wait(timeout=2)

        def flush(self):
            pass

        def close(self):
            pass

    receiver.start()
    receiver._record_file.close()
    receiver._record_file = SlowFile()
    try:
        fake_socket.packets.append(b"first")
        assert writing.wait(timeout=1)
        fake_socket.packets.append(b"second")
        deadline = time.monotonic() + 0.5
        while len(decoder.packets) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert decoder.packets == [b"first", b"second"]
        assert receiver.peek() is not None
    finally:
        release.set()
        receiver.stop()


def test_recording_buffer_overflow_marks_only_the_recording_failed(tmp_path):
    fake_socket = FakeSocket()
    decoder = FakeDecoder([])
    receiver = VideoReceiver(
        local_ip="192.168.10.2", socket_factory=lambda *_a, **_k: fake_socket,
        decoder_factory=lambda: decoder, packet_factory=lambda payload: payload,
        record_path=tmp_path / "flight.h264",
    )
    writing = threading.Event()
    release = threading.Event()

    class SlowFile:
        def write(self, _payload):
            writing.set()
            release.wait(timeout=3)

        def flush(self):
            pass

        def close(self):
            pass

    receiver.start()
    receiver._record_file.close()
    receiver._record_file = SlowFile()
    try:
        fake_socket.packets.append(b"first")
        assert writing.wait(timeout=1)
        fake_socket.packets.extend([b"frame"] * 40)
        deadline = time.monotonic() + 1
        while len(decoder.packets) < 41 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(decoder.packets) == 41
        assert receiver.recording_error == "recording buffer full"
        assert receiver.is_running
    finally:
        release.set()
        receiver.stop()


@pytest.mark.parametrize("pixel_format", ["rgb24", "bgr24"])
def test_video_receiver_makes_rgb_bgr_conversion_explicit(pixel_format):
    fake_socket = FakeSocket([b"frame"])
    formats = []
    decoder = FakeDecoder(formats)
    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=lambda: decoder,
        packet_factory=lambda payload: payload,
        pixel_format=pixel_format,
    )

    receiver.start()
    frame = receiver.read(timeout=1.0)
    receiver.stop()

    assert frame.pixel_format == pixel_format
    assert formats == [pixel_format]


def test_video_receiver_surfaces_decoder_failure():
    fake_socket = FakeSocket([b"corrupt"])

    class FailingDecoder:
        def decode(self, _packet):
            raise ValueError("bad h264 data")

    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=FailingDecoder,
        packet_factory=lambda payload: payload,
    )
    receiver.start()

    with pytest.raises(VideoDecoderError, match="bad h264 data"):
        receiver.read(timeout=1.0)

    assert receiver.last_error is not None
    receiver.stop()


def test_video_receiver_waits_through_pre_keyframe_invalid_data():
    fake_socket = FakeSocket([b"pre-keyframe", b"keyframe"])

    class RecoveringDecoder(FakeDecoder):
        def decode(self, packet):
            if packet == b"pre-keyframe":
                raise InvalidDataError(1094995529, "pre-keyframe")
            return super().decode(packet)

    formats = []
    decoder = RecoveringDecoder(formats)
    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=lambda: decoder,
        packet_factory=lambda payload: payload,
    )
    receiver.start()

    frame = receiver.read(timeout=1.0)

    assert frame.width == 640
    assert receiver.last_error is None
    receiver.stop()


def test_video_receiver_reports_stall_without_inventing_a_frame():
    fake_socket = FakeSocket()
    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=lambda: FakeDecoder([]),
        packet_factory=lambda payload: payload,
    )
    receiver.start()

    with pytest.raises(VideoReadTimeout, match="11111"):
        receiver.read(timeout=0.01)

    receiver.stop()
