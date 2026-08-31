"""Tests for the protobuf message codec.

The amp speaks proto2. Every FenderMessageLT carries a required responseType
(UNSOLICITED for anything host-to-amp) plus exactly one payload from a oneof
named `type`.
"""

import pytest

from lt25_mcp.messages import (
    MessageError,
    decode_message,
    encode_message,
    which_payload,
)


class TestEncode:
    def test_heartbeat_round_trips(self):
        msg = decode_message(encode_message(heartbeat={"dummyField": True}))
        assert which_payload(msg) == "heartbeat"
        assert msg.heartbeat.dummyField is True

    def test_retrieve_preset_carries_slot(self):
        msg = decode_message(encode_message(retrievePreset={"slot": 42}))
        assert msg.retrievePreset.slot == 42

    def test_response_type_defaults_to_unsolicited(self):
        msg = decode_message(encode_message(heartbeat={"dummyField": True}))
        assert msg.responseType == 0  # UNSOLICITED

    def test_modal_status_accepts_enum_names(self):
        msg = decode_message(
            encode_message(modalStatusMessage={"context": "SYNC_BEGIN", "state": "OK"})
        )
        assert msg.modalStatusMessage.context == 0

    def test_audition_preset_uses_preset_data_field(self):
        msg = decode_message(encode_message(auditionPreset={"presetData": '{"a":1}'}))
        assert msg.auditionPreset.presetData == '{"a":1}'

    def test_save_preset_as_carries_slot_and_payload(self):
        msg = decode_message(
            encode_message(
                savePresetAs={
                    "presetData": "{}",
                    "isLoadPreset": False,
                    "presetSlot": 47,
                }
            )
        )
        assert msg.savePresetAs.presetSlot == 47
        assert msg.savePresetAs.isLoadPreset is False


class TestFailsLoudly:
    def test_unknown_message_name_raises(self):
        with pytest.raises(MessageError):
            encode_message(notARealMessage={"x": 1})

    def test_unknown_field_within_message_raises(self):
        with pytest.raises(MessageError):
            encode_message(retrievePreset={"notAField": 1})

    def test_missing_required_field_raises(self):
        # SavePresetAs requires all three fields; omitting two must not silently pass.
        with pytest.raises(MessageError):
            encode_message(savePresetAs={"presetData": "{}"})

    def test_more_than_one_payload_raises(self):
        with pytest.raises(MessageError, match="exactly one"):
            encode_message(heartbeat={"dummyField": True}, retrievePreset={"slot": 1})

    def test_no_payload_raises(self):
        with pytest.raises(MessageError, match="exactly one"):
            encode_message()

    def test_which_payload_on_empty_message_raises(self):
        from lt25_mcp import messages

        empty = messages._new_message()
        with pytest.raises(MessageError, match="no payload"):
            which_payload(empty)


class TestDecode:
    def test_decode_rejects_garbage(self):
        with pytest.raises(MessageError):
            decode_message(b"\xff\xff\xff\xff\xff\xff")

    def test_preset_json_message_decodes(self):
        raw = encode_message(presetJSONMessage={"data": '{"x":1}', "slotIndex": 3})
        msg = decode_message(raw)
        assert which_payload(msg) == "presetJSONMessage"
        assert msg.presetJSONMessage.slotIndex == 3
