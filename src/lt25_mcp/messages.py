"""Protobuf codec for the amp's control messages.

Every exchange is a `FenderMessageLT`: a required `responseType` plus exactly
one payload selected from a oneof named `type`. Host-to-amp messages are always
`UNSOLICITED` (the proto2 default), so callers never set it.

The schema is proto2, which means required fields are genuinely required —
building a message without them raises rather than sending a half-formed
packet to the amp.

Regenerate the bindings with `scripts/gen_proto.sh`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from google.protobuf import json_format
from google.protobuf.message import DecodeError, EncodeError

# The .proto files declare no package, so protoc emits a flat directory whose
# modules import each other by bare name (`import Heartbeat_pb2`). That only
# resolves if the directory itself is on sys.path.
_GENERATED = Path(__file__).parent / "_generated"
if str(_GENERATED) not in sys.path:
    sys.path.insert(0, str(_GENERATED))

try:
    import FenderMessageLT_pb2  # noqa: E402  (requires the sys.path entry above)
except ModuleNotFoundError as exc:  # pragma: no cover - setup failure path
    raise ImportError(
        "protobuf bindings are missing. Run ./scripts/gen_proto.sh to generate "
        f"them into {_GENERATED}."
    ) from exc


class MessageError(Exception):
    """Raised when a message cannot be built or parsed."""


def _new_message():
    """An empty FenderMessageLT. Exposed for tests."""
    return FenderMessageLT_pb2.FenderMessageLT()


def encode_message(**payload) -> bytes:
    """Build and serialize a FenderMessageLT carrying exactly one payload.

    >>> encode_message(retrievePreset={"slot": 1})  # doctest: +ELLIPSIS
    b'...'
    """
    if len(payload) != 1:
        raise MessageError(
            f"expected exactly one payload field, got {sorted(payload) or 'none'}"
        )
    message = _new_message()
    # responseType is `required` with a default. In proto2 a default does not
    # satisfy required-ness on serialize, so it must be set explicitly. Every
    # host-to-amp message is UNSOLICITED.
    message.responseType = FenderMessageLT_pb2.ResponseType.UNSOLICITED
    try:
        json_format.ParseDict(payload, message)
    except json_format.ParseError as exc:
        raise MessageError(f"could not build message: {exc}") from exc
    try:
        return message.SerializeToString()
    except EncodeError as exc:
        raise MessageError(f"message is incomplete: {exc}") from exc


def decode_message(data: bytes):
    """Parse bytes off the wire into a FenderMessageLT."""
    message = _new_message()
    try:
        message.ParseFromString(data)
    except DecodeError as exc:
        raise MessageError(f"could not parse message: {exc}") from exc
    return message


def which_payload(message) -> str:
    """Name of the populated oneof field."""
    name = message.WhichOneof("type")
    if name is None:
        raise MessageError("message carries no payload")
    return name
