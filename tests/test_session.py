"""Tests for the amp session: handshake, heartbeat, request/reply."""

import pytest

from lt25_mcp.messages import decode_message, encode_message, which_payload
from lt25_mcp.session import Session, SessionError


class ScriptedTransport:
    """A transport that returns canned replies in order."""

    def __init__(self, replies=None):
        self.sent = []
        self._replies = list(replies or [])
        self.closed = False

    def send(self, payload):
        self.sent.append(decode_message(payload))

    def receive(self, timeout_ms=1000):
        return self._replies.pop(0) if self._replies else None

    def drain(self):
        return 0

    def close(self):
        self.closed = True

    def sent_kinds(self):
        return [which_payload(m) for m in self.sent]


def modal_ack(context="SYNC_BEGIN"):
    return encode_message(modalStatusMessage={"context": context, "state": "OK"})


def handshake_replies():
    return [modal_ack("SYNC_BEGIN"), modal_ack("SYNC_END")]


def make_session(replies=None):
    transport = ScriptedTransport(replies)
    return Session(transport, heartbeat=False), transport


class TestHandshake:
    def test_open_sends_sync_begin_then_sync_end(self):
        session, transport = make_session(handshake_replies())
        session.open()
        assert transport.sent_kinds() == ["modalStatusMessage", "modalStatusMessage"]
        assert transport.sent[0].modalStatusMessage.context == 0  # SYNC_BEGIN
        assert transport.sent[1].modalStatusMessage.context == 1  # SYNC_END

    def test_open_without_ack_raises_mentioning_conflicting_client(self):
        session, _ = make_session([])
        with pytest.raises(SessionError, match="Fender Tone"):
            session.open()

    def test_open_twice_raises(self):
        session, _ = make_session(handshake_replies())
        session.open()
        with pytest.raises(SessionError, match="already open"):
            session.open()


class TestRequest:
    def test_request_returns_decoded_reply(self):
        replies = [*handshake_replies(), encode_message(firmwareVersionStatus={"version": "2.1.4"})]
        session, _ = make_session(replies)
        session.open()
        reply = session.request(firmwareVersionRequest={"request": True})
        assert which_payload(reply) == "firmwareVersionStatus"

    def test_firmware_version_helper(self):
        replies = [*handshake_replies(), encode_message(firmwareVersionStatus={"version": "2.1.4"})]
        session, _ = make_session(replies)
        session.open()
        assert session.firmware_version() == "2.1.4"

    def test_product_id_helper(self):
        replies = [
            *handshake_replies(),
            encode_message(productIdentificationStatus={"id": "mustang-lt-25"}),
        ]
        session, _ = make_session(replies)
        session.open()
        assert session.product_id() == "mustang-lt-25"

    def test_request_skips_unsolicited_traffic_when_expecting(self):
        """Turning the amp's encoder mid-request must not corrupt the reply."""
        replies = [
            *handshake_replies(),
            encode_message(currentDisplayedPresetIndexStatus={"currentDisplayedPresetIndex": 7}),
            encode_message(firmwareVersionStatus={"version": "2.1.4"}),
        ]
        session, _ = make_session(replies)
        session.open()
        reply = session.request(
            expect="firmwareVersionStatus", firmwareVersionRequest={"request": True}
        )
        assert reply.firmwareVersionStatus.version == "2.1.4"

    def test_request_timeout_raises(self):
        session, _ = make_session(handshake_replies())
        session.open()
        with pytest.raises(SessionError, match="no reply"):
            session.request(firmwareVersionRequest={"request": True})

    def test_request_before_open_raises(self):
        session, _ = make_session([])
        with pytest.raises(SessionError, match="not open"):
            session.request(firmwareVersionRequest={"request": True})


class TestLifecycle:
    def test_close_closes_transport(self):
        session, transport = make_session(handshake_replies())
        session.open()
        session.close()
        assert transport.closed

    def test_close_is_idempotent(self):
        session, _ = make_session(handshake_replies())
        session.open()
        session.close()
        session.close()

    def test_context_manager_opens_and_closes(self):
        transport = ScriptedTransport(handshake_replies())
        with Session(transport, heartbeat=False) as session:
            assert session.is_open
        assert transport.closed

    def test_context_manager_closes_on_exception(self):
        transport = ScriptedTransport(handshake_replies())
        with pytest.raises(RuntimeError):
            with Session(transport, heartbeat=False):
                raise RuntimeError("boom")
        assert transport.closed


class TestHeartbeat:
    def test_heartbeat_thread_sends_periodically(self):
        transport = ScriptedTransport(handshake_replies() * 50)
        session = Session(transport, heartbeat=True, heartbeat_interval=0.01)
        session.open()
        try:
            deadline = __import__("time").time() + 1.0
            while __import__("time").time() < deadline:
                if "heartbeat" in transport.sent_kinds():
                    break
                __import__("time").sleep(0.01)
        finally:
            session.close()
        assert "heartbeat" in transport.sent_kinds()

    def test_heartbeat_stops_after_close(self):
        import time

        transport = ScriptedTransport(handshake_replies() * 200)
        session = Session(transport, heartbeat=True, heartbeat_interval=0.01)
        session.open()
        time.sleep(0.05)
        session.close()
        settled = len(transport.sent)
        time.sleep(0.1)
        assert len(transport.sent) == settled


class TestCloseIsSafe:
    def test_close_does_not_double_close_the_transport(self):
        """A real Transport has no `closed` attribute; do not rely on one."""

        class CountingTransport(ScriptedTransport):
            def __init__(self, replies=None):
                super().__init__(replies)
                self.close_count = 0

            def close(self):
                self.close_count += 1
                self.closed = True

        transport = CountingTransport(handshake_replies())
        session = Session(transport, heartbeat=False)
        session.open()
        session.close()
        session.close()
        session.close()
        assert transport.close_count == 1

    def test_close_before_open_still_closes_the_transport(self):
        transport = ScriptedTransport()
        Session(transport, heartbeat=False).close()
        assert transport.closed
