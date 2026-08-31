"""USB HID packet framing for the Fender Mustang LT series.

The amp exposes a HID interface alongside its USB audio interface. Control
messages travel over that HID interface as fixed 64-byte reports, chunked
with a tag/length/value header.

    Host -> Device:  [tag][length][value (61 bytes)][0x00]
    Device -> Host:  [0x00][tag][length][value (61 bytes)]

Note the asymmetry: replies from the amp are shifted one byte right behind a
leading padding byte, so encoding and decoding are not mirror images.

Protocol reference:
https://github.com/brentmaxwell/LtAmp/blob/main/Docs/Protocol.md
"""

from __future__ import annotations

PACKET_SIZE = 64

# 64 bytes total, minus the tag and length bytes, minus one trailing pad byte.
MAX_PAYLOAD = PACKET_SIZE - 3

TAG_START = 0x33
TAG_CONTINUE = 0x34
TAG_END = 0x35

_VALID_TAGS = frozenset({TAG_START, TAG_CONTINUE, TAG_END})


class FramingError(Exception):
    """Raised when a packet violates the wire format."""


def encode(payload: bytes) -> list[bytes]:
    """Split a message into 64-byte host-to-device HID reports.

    A message that fits in a single packet is tagged as final. Longer
    messages are tagged start / continue... / final so the amp can
    reassemble them.
    """
    chunks = [payload[i : i + MAX_PAYLOAD] for i in range(0, len(payload), MAX_PAYLOAD)]
    if not chunks:
        chunks = [b""]

    packets = []
    last = len(chunks) - 1
    for index, chunk in enumerate(chunks):
        if index == last:
            tag = TAG_END
        elif index == 0:
            tag = TAG_START
        else:
            tag = TAG_CONTINUE
        packet = bytes([tag, len(chunk)]) + chunk
        packets.append(packet.ljust(PACKET_SIZE, b"\x00"))
    return packets


class PacketAssembler:
    """Reassembles device-to-host packets back into whole messages.

    Feed each 64-byte report as it arrives. Returns the completed message
    when the final packet lands, otherwise None.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._started = False

    def feed(self, packet: bytes) -> bytes | None:
        if len(packet) != PACKET_SIZE:
            raise FramingError(f"expected 64 bytes, got {len(packet)}")

        # packet[0] is the leading pad byte and carries no meaning.
        tag = packet[1]
        length = packet[2]

        if tag not in _VALID_TAGS:
            raise FramingError(f"unknown tag 0x{tag:02x}")
        if length > MAX_PAYLOAD:
            raise FramingError(f"packet declares {length} bytes, capacity is {MAX_PAYLOAD}")
        if tag == TAG_CONTINUE and not self._started:
            raise FramingError("continuation packet without a start packet")

        value = packet[3 : 3 + length]

        if tag == TAG_START:
            self._buffer = bytearray(value)
            self._started = True
            return None

        if tag == TAG_CONTINUE:
            self._buffer.extend(value)
            return None

        self._buffer.extend(value)
        message = bytes(self._buffer)
        self._buffer = bytearray()
        self._started = False
        return message
