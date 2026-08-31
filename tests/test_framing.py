"""Tests for the USB HID packet framing layer.

Protocol reference: https://github.com/brentmaxwell/LtAmp/blob/main/Docs/Protocol.md

Every HID report is exactly 64 bytes.

    Host -> Device:  [tag][length][value (61 bytes)][0x00]
    Device -> Host:  [0x00][tag][length][value (61 bytes)]

Tags:
    0x33  start of a multi-packet message
    0x34  continuation of a multi-packet message
    0x35  final packet, or the only packet of a short message
"""

import pytest

from lt25_mcp.framing import (
    MAX_PAYLOAD,
    PACKET_SIZE,
    TAG_CONTINUE,
    TAG_END,
    TAG_START,
    FramingError,
    PacketAssembler,
    encode,
)


class TestConstants:
    def test_packet_is_64_bytes(self):
        assert PACKET_SIZE == 64

    def test_payload_capacity_leaves_room_for_header_and_padding(self):
        # 64 = tag + length + payload + trailing 0x00
        assert MAX_PAYLOAD == PACKET_SIZE - 3


class TestEncode:
    def test_short_message_produces_single_packet(self):
        packets = encode(b"hello")
        assert len(packets) == 1

    def test_every_packet_is_exactly_64_bytes(self):
        for payload in [b"", b"x", b"y" * 61, b"z" * 200]:
            for packet in encode(payload):
                assert len(packet) == PACKET_SIZE

    def test_single_packet_uses_end_tag(self):
        (packet,) = encode(b"hello")
        assert packet[0] == TAG_END

    def test_single_packet_encodes_payload_length(self):
        (packet,) = encode(b"hello")
        assert packet[1] == 5

    def test_single_packet_carries_payload_then_zero_padding(self):
        (packet,) = encode(b"hello")
        assert packet[2:7] == b"hello"
        assert packet[7:] == b"\x00" * (PACKET_SIZE - 7)

    def test_empty_payload_is_a_single_empty_end_packet(self):
        (packet,) = encode(b"")
        assert packet[0] == TAG_END
        assert packet[1] == 0

    def test_exactly_max_payload_still_fits_one_packet(self):
        packets = encode(b"a" * MAX_PAYLOAD)
        assert len(packets) == 1
        assert packets[0][1] == MAX_PAYLOAD

    def test_one_byte_over_capacity_splits_into_two_packets(self):
        packets = encode(b"a" * (MAX_PAYLOAD + 1))
        assert len(packets) == 2
        assert packets[0][0] == TAG_START
        assert packets[0][1] == MAX_PAYLOAD
        assert packets[1][0] == TAG_END
        assert packets[1][1] == 1

    def test_long_message_uses_start_continue_end_sequence(self):
        packets = encode(b"a" * (MAX_PAYLOAD * 3 + 5))
        assert [p[0] for p in packets] == [TAG_START, TAG_CONTINUE, TAG_CONTINUE, TAG_END]

    def test_trailing_byte_is_always_zero_padding(self):
        for packet in encode(b"a" * 200):
            assert packet[PACKET_SIZE - 1] == 0x00


class TestPacketAssembler:
    def test_single_packet_completes_immediately(self):
        assembler = PacketAssembler()
        (packet,) = encode(b"hello")
        assert assembler.feed(to_device_to_host(packet)) == b"hello"

    def test_returns_none_until_final_packet_arrives(self):
        assembler = PacketAssembler()
        packets = encode(b"a" * 200)
        for packet in packets[:-1]:
            assert assembler.feed(to_device_to_host(packet)) is None
        assert assembler.feed(to_device_to_host(packets[-1])) == b"a" * 200

    @pytest.mark.parametrize("size", [0, 1, 60, 61, 62, 122, 200, 1000])
    def test_round_trip_preserves_payload(self, size):
        payload = bytes(range(256)) * 8
        payload = payload[:size]
        assembler = PacketAssembler()
        result = None
        for packet in encode(payload):
            result = assembler.feed(to_device_to_host(packet))
        assert result == payload

    def test_assembler_resets_between_messages(self):
        assembler = PacketAssembler()
        for expected in [b"first", b"second", b"c" * 150]:
            result = None
            for packet in encode(expected):
                result = assembler.feed(to_device_to_host(packet))
            assert result == expected

    def test_unknown_tag_raises(self):
        assembler = PacketAssembler()
        bogus = bytes([0x00, 0x99, 0x01]) + b"x" + b"\x00" * 60
        with pytest.raises(FramingError, match="unknown tag"):
            assembler.feed(bogus)

    def test_continuation_without_start_raises(self):
        assembler = PacketAssembler()
        orphan = bytes([0x00, TAG_CONTINUE, 0x01]) + b"x" + b"\x00" * 60
        with pytest.raises(FramingError, match="without a start"):
            assembler.feed(orphan)

    def test_wrong_packet_size_raises(self):
        assembler = PacketAssembler()
        with pytest.raises(FramingError, match="64 bytes"):
            assembler.feed(b"\x00" * 32)

    def test_length_exceeding_capacity_raises(self):
        assembler = PacketAssembler()
        overlong = bytes([0x00, TAG_END, MAX_PAYLOAD + 1]) + b"\x00" * 61
        with pytest.raises(FramingError, match="declares"):
            assembler.feed(overlong)


def to_device_to_host(host_packet: bytes) -> bytes:
    """Re-frame a host->device packet as the device->host layout.

    The amp echoes the same tag/length/value but shifted one byte right,
    behind a leading 0x00. Encoding and decoding are therefore not
    byte-identical, and the assembler must expect the shifted layout.
    """
    tag, length = host_packet[0], host_packet[1]
    value = host_packet[2 : 2 + length]
    packet = bytes([0x00, tag, length]) + value
    return packet.ljust(PACKET_SIZE, b"\x00")
