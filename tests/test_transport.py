"""Tests for the HID transport layer.

The backend is injectable so everything above the USB boundary is testable
with no amp attached.
"""

import pytest

from lt25_mcp.framing import PACKET_SIZE, encode
from lt25_mcp.transport import PRODUCT_ID, VENDOR_ID, Transport, TransportError


class FakeBackend:
    def __init__(self, to_read=()):
        self.written = []
        self._to_read = list(to_read)
        self.closed = False

    def write(self, data):
        self.written.append(bytes(data))

    def read(self, size, timeout_ms):
        return self._to_read.pop(0) if self._to_read else b""

    def close(self):
        self.closed = True


def device_reply(payload: bytes) -> list[bytes]:
    """Frame a payload the way the amp does: shifted one byte right."""
    packets = []
    for p in encode(payload):
        tag, length = p[0], p[1]
        packets.append((bytes([0, tag, length]) + p[2 : 2 + length]).ljust(PACKET_SIZE, b"\x00"))
    return packets


class TestIdentifiers:
    def test_vendor_and_product_match_the_amp(self):
        # Verified against ioreg on a real Mustang LT 25.
        assert (VENDOR_ID, PRODUCT_ID) == (0x1ED8, 0x0037)


class TestSend:
    def test_short_payload_is_one_report(self):
        backend = FakeBackend()
        Transport(backend).send(b"hello")
        assert len(backend.written) == 1
        assert len(backend.written[0]) == PACKET_SIZE

    def test_long_payload_splits(self):
        backend = FakeBackend()
        Transport(backend).send(b"a" * 200)
        assert len(backend.written) == 4
        assert all(len(p) == PACKET_SIZE for p in backend.written)


class TestReceive:
    def test_single_packet_reply(self):
        backend = FakeBackend(device_reply(b"hello"))
        assert Transport(backend).receive(timeout_ms=10) == b"hello"

    def test_multi_packet_reply_is_reassembled(self):
        backend = FakeBackend(device_reply(b"b" * 150))
        assert Transport(backend).receive(timeout_ms=10) == b"b" * 150

    def test_timeout_returns_none(self):
        assert Transport(FakeBackend()).receive(timeout_ms=1) is None

    def test_short_read_is_padded_not_rejected(self):
        """hidapi may hand back fewer than 64 bytes; pad rather than crash."""
        (packet,) = device_reply(b"hi")
        backend = FakeBackend([packet[:10]])
        assert Transport(backend).receive(timeout_ms=10) == b"hi"

    def test_consecutive_messages_do_not_bleed(self):
        backend = FakeBackend(device_reply(b"one") + device_reply(b"two"))
        transport = Transport(backend)
        assert transport.receive(timeout_ms=10) == b"one"
        assert transport.receive(timeout_ms=10) == b"two"


class TestLifecycle:
    def test_close_closes_backend(self):
        backend = FakeBackend()
        Transport(backend).close()
        assert backend.closed

    def test_context_manager_closes(self):
        backend = FakeBackend()
        with Transport(backend):
            pass
        assert backend.closed


class TestOpen:
    def test_open_with_injected_backend_skips_usb(self):
        from lt25_mcp.transport import open_transport

        backend = FakeBackend()
        transport = open_transport(backend)
        transport.send(b"x")
        assert backend.written

    def test_missing_device_raises_actionable_error(self, monkeypatch):
        from lt25_mcp import transport as mod

        class BoomDevice:
            def __init__(self, *a, **k):
                raise OSError("not found")

        monkeypatch.setattr(mod, "_open_hid_device", lambda: BoomDevice())
        with pytest.raises(TransportError, match="Fender Tone"):
            mod.open_transport()


class TestDrain:
    def test_drain_discards_pending_packets(self):
        backend = FakeBackend(device_reply(b"stale") + device_reply(b"also stale"))
        transport = Transport(backend)
        assert transport.drain() == 2

    def test_drain_resets_partial_reassembly(self):
        """Joining a multi-packet message mid-stream must not poison the next read."""
        partial = device_reply(b"x" * 150)[:2]  # start + continuation, no end
        backend = FakeBackend(partial)
        transport = Transport(backend)
        transport.drain()
        backend._to_read = device_reply(b"fresh")
        assert transport.receive(timeout_ms=10) == b"fresh"

    def test_drain_on_quiet_line_reports_zero(self):
        assert Transport(FakeBackend()).drain() == 0
