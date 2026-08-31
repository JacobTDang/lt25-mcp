"""A conversation with the amp.

Opening a session performs the SYNC_BEGIN / SYNC_END handshake, after which a
background thread sends a heartbeat twice a second. The amp drops the session
if it stops hearing from the host for about a second.

Only one program can hold a useful session at a time. If Fender Tone LT
Desktop is running it will answer the amp's traffic instead, which shows up
here as a handshake that never gets acknowledged.
"""

from __future__ import annotations

import threading

from lt25_mcp.messages import decode_message, encode_message, which_payload
from lt25_mcp.transport import Transport

HEARTBEAT_INTERVAL = 0.5
DEFAULT_TIMEOUT_MS = 3000

# How many unrelated messages to step over while waiting for an expected reply.
# The amp emits status messages whenever a knob moves, so a request issued
# while somebody is turning the encoder can arrive behind a few of them.
MAX_SKIPPED_REPLIES = 16


class SessionError(Exception):
    """Raised when the amp will not talk, or is asked to do so out of order."""


class Session:
    def __init__(
        self,
        transport: Transport,
        *,
        heartbeat: bool = True,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
    ) -> None:
        self._transport = transport
        self._heartbeat_enabled = heartbeat
        self._heartbeat_interval = heartbeat_interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> Session:
        if self._open:
            raise SessionError("session is already open")
        for context in ("SYNC_BEGIN", "SYNC_END"):
            self._transport.send(
                encode_message(modalStatusMessage={"context": context, "state": "OK"})
            )
            if self._transport.receive(timeout_ms=DEFAULT_TIMEOUT_MS) is None:
                raise SessionError(
                    f"amp did not acknowledge {context}. Is Fender Tone LT Desktop "
                    "running? Only one program can hold the amp's control channel."
                )
        self._open = True
        if self._heartbeat_enabled:
            self._start_heartbeat()
        return self

    def request(
        self,
        *,
        expect: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        **payload,
    ):
        """Send one message and return the amp's reply.

        With `expect`, unrelated status messages arriving first are skipped.
        """
        if not self._open:
            raise SessionError("session is not open")
        sent = next(iter(payload), "<none>")
        with self._lock:
            self._transport.send(encode_message(**payload))
            for _ in range(MAX_SKIPPED_REPLIES):
                raw = self._transport.receive(timeout_ms=timeout_ms)
                if raw is None:
                    raise SessionError(f"no reply to {sent} within {timeout_ms}ms")
                reply = decode_message(raw)
                if expect is None or which_payload(reply) == expect:
                    return reply
        raise SessionError(
            f"no reply to {sent} matching {expect!r} after "
            f"{MAX_SKIPPED_REPLIES} unrelated messages"
        )

    def firmware_version(self) -> str:
        reply = self.request(
            expect="firmwareVersionStatus", firmwareVersionRequest={"request": True}
        )
        return reply.firmwareVersionStatus.version

    def product_id(self) -> str:
        reply = self.request(
            expect="productIdentificationStatus",
            productIdentificationRequest={"request": True},
        )
        return reply.productIdentificationStatus.id

    def close(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._open or not self._transport_closed():
            self._transport.close()
        self._open = False

    def _transport_closed(self) -> bool:
        return getattr(self._transport, "closed", False)

    def _start_heartbeat(self) -> None:
        self._stop.clear()

        def beat() -> None:
            message = encode_message(heartbeat={"dummyField": True})
            while not self._stop.wait(self._heartbeat_interval):
                with self._lock:
                    if self._stop.is_set():
                        return
                    try:
                        self._transport.send(message)
                    except Exception:
                        # The transport is gone; stop rather than spin.
                        return

        self._thread = threading.Thread(target=beat, name="lt25-heartbeat", daemon=True)
        self._thread.start()

    def __enter__(self) -> Session:
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()
