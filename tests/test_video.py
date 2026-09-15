import threading
import time

import pytest

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
    fake_socket = FakeSocket([b"short", b"a much longer datagram"])
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
    assert decoder.packets == [b"short", b"a much longer datagram"]
    assert fake_socket.bindings == [("192.168.10.2", 11111)]

    receiver.stop()
    assert fake_socket.closed is True


def test_video_receiver_uses_codec_parser_for_fragmented_h264_datagrams():
    fake_socket = FakeSocket([b"fragment-1", b"fragment-2"])
    formats = []
    parsed = []

    class ParsingDecoder(FakeDecoder):
        def parse(self, payload):
            parsed.append(payload)
            return [payload]

    decoder = ParsingDecoder(formats)
    receiver = VideoReceiver(
        local_ip="192.168.10.2",
        socket_factory=lambda *_args, **_kwargs: fake_socket,
        decoder_factory=lambda: decoder,
        packet_factory=lambda _payload: (_ for _ in ()).throw(
            AssertionError("parser path must not use packet_factory")
        ),
    )

    receiver.start()
    frame = receiver.read(timeout=1.0)
    assert frame.width == 640
    deadline = time.monotonic() + 1.0
    while len(parsed) < 2 and time.monotonic() < deadline:
        time.sleep(0.001)
    receiver.stop()

    assert parsed == [b"fragment-1", b"fragment-2"]


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
