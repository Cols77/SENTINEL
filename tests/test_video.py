import socket
import sys
from collections import deque
from types import SimpleNamespace

import pytest

from sentinel.video import H264FrameAssembler, PyAVDecoder, UdpVideoReceiver


class FakeAssembler:
    def __init__(self, decoder):
        self.decoder = decoder
        self.packets = []

    def push(self, packet):
        self.packets.append(packet)
        return [(320, 240)] if packet == b"frame-end" else []


class FakeVideoSocket:
    def __init__(self, packets=()):
        self.packets = deque(packets)
        self.bound_to = None
        self.timeout = None
        self.closed = False
        self.close_calls = 0

    def bind(self, address):
        self.bound_to = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def recvfrom(self, _size):
        if self.closed:
            raise OSError("socket closed")
        if self.packets:
            return self.packets.popleft(), ("192.168.10.1", 11111)
        raise socket.timeout()

    def close(self):
        self.close_calls += 1
        self.closed = True


def test_frame_assembler_decodes_when_tello_frame_ends():
    class FakeDecoder:
        def __init__(self):
            self.payloads = []

        def decode(self, payload):
            self.payloads.append(payload)
            return [(640, 480)] if payload else []

    decoder = FakeDecoder()
    assembler = H264FrameAssembler(decoder)

    assert assembler.push(b"a" * 1460) == []
    assert assembler.push(b"b" * 12) == [(640, 480)]
    assert decoder.payloads == [b"a" * 1460 + b"b" * 12]


def test_frame_assembler_clears_payload_after_a_short_packet():
    decoder = SimpleNamespace(decode=lambda payload: [len(payload)])
    assembler = H264FrameAssembler(decoder)

    assert assembler.push(b"a" * 1460 + b"b") == [1461]
    assert assembler.push(b"c") == [1]


def test_pyav_decoder_imports_av_only_when_first_decoding(monkeypatch):
    class FakeCodec:
        def __init__(self):
            self.packets = []

        @classmethod
        def create(cls, codec_name, mode):
            assert (codec_name, mode) == ("h264", "r")
            return cls()

        def decode(self, packet):
            self.packets.append(packet)
            return [SimpleNamespace(width=640, height=480)]

    class FakePacket:
        def __init__(self, payload):
            self.payload = payload

    fake_av = SimpleNamespace(
        CodecContext=FakeCodec,
        Packet=FakePacket,
    )
    monkeypatch.setitem(sys.modules, "av", fake_av)

    decoder = PyAVDecoder()
    assert decoder._codec is None
    assert decoder.decode(b"h264") == [(640, 480)]
    assert decoder._codec is not None


def test_udp_video_receiver_binds_local_ip_and_queues_decoded_utc_frame():
    fake_socket = FakeVideoSocket([b"frame-end"])
    fake_decoder = object()
    receiver = UdpVideoReceiver(
        "192.168.10.2",
        timeout=0.01,
        socket_factory=lambda *_args: fake_socket,
        decoder_factory=lambda: fake_decoder,
        assembler_factory=FakeAssembler,
    )

    receiver.start()
    frame = receiver.read(timeout=0.5)

    assert fake_socket.bound_to == ("192.168.10.2", 11111)
    assert fake_socket.timeout == 0.01
    assert receiver.thread.daemon is True
    assert (frame.width, frame.height) == (320, 240)
    assert frame.captured_at.tzinfo is not None
    assert frame.captured_at.utcoffset() is not None
    assert receiver.assembler.decoder is fake_decoder

    receiver.close()
    receiver.close()
    assert fake_socket.close_calls == 1
    assert receiver.socket is None


def test_udp_video_receiver_read_exposes_queue_timeout_and_close_stops_thread():
    fake_socket = FakeVideoSocket()
    receiver = UdpVideoReceiver(
        "192.168.10.2",
        timeout=0.01,
        socket_factory=lambda *_args: fake_socket,
        decoder_factory=lambda: object(),
        assembler_factory=FakeAssembler,
    )

    receiver.start()
    with pytest.raises(__import__("queue").Empty):
        receiver.read(timeout=0.01)

    receiver.close()
    assert not receiver.thread.is_alive()
    assert fake_socket.closed is True


def test_udp_video_receiver_records_decoder_error_and_continues_receiving():
    class FailingOnceDecoder:
        def __init__(self):
            self.calls = 0

        def decode(self, _payload):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("synthetic decoder failure")
            return [(800, 600)]

    fake_socket = FakeVideoSocket([b"bad-frame", b"good-frame"])
    decoder = FailingOnceDecoder()
    receiver = UdpVideoReceiver(
        "192.168.10.2",
        timeout=0.01,
        socket_factory=lambda *_args: fake_socket,
        decoder_factory=lambda: decoder,
        assembler_factory=H264FrameAssembler,
    )

    receiver.start()
    frame = receiver.read(timeout=0.5)

    assert (frame.width, frame.height) == (800, 600)
    assert isinstance(receiver.last_error, ValueError)
    assert str(receiver.last_error) == "synthetic decoder failure"
    assert receiver.error is receiver.last_error

    receiver.close()
    receiver.close()
    assert not receiver.thread.is_alive()
    assert fake_socket.close_calls == 1
