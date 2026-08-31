"""USB HID transport for the Mustang LT amps.

Sits between the framing layer and the physical device. The backend is an
injectable protocol so the transport can be exercised without hardware; only
`_open_hid_device` touches USB.

The amp presents as vendor-defined HID (usage page 0xFF00), which is why it
needs no driver and no macOS Input Monitoring permission.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Protocol

from lt25_mcp.framing import PACKET_SIZE, PacketAssembler, encode

DRAIN_TIMEOUT_MS = 5
"""Poll timeout when clearing stale input. hidapi treats 0 as blocking."""

VENDOR_ID = 0x1ED8
PRODUCT_ID = 0x0037

LOCK_PATH = Path.home() / ".config" / "lt25-mcp" / "amp.lock"
"""Advisory lock so two processes cannot hold the amp at once."""


class TransportError(Exception):
    """Raised when the HID device cannot be reached or behaves unexpectedly."""


class AmpLock:
    """An advisory file lock guarding access to the amp.

    Only one program can hold the amp's control channel, and hidapi reports a
    collision as a bare OSError that says nothing useful. This turns it into a
    message naming the process that has it, and the lock is released by the
    kernel if that process dies without cleaning up.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path or LOCK_PATH)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = os.read(fd, 32).decode(errors="replace").strip() or "unknown"
            os.close(fd)
            raise TransportError(
                f"the amp is already in use by another program (pid {holder}). "
                "Only one can hold its control channel - close the other one, "
                "or wait for it to finish."
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> AmpLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


class HidBackend(Protocol):
    """Minimal surface the transport needs from a HID device."""

    def write(self, data: bytes) -> None: ...
    def read(self, size: int, timeout_ms: int) -> bytes: ...
    def close(self) -> None: ...


class Transport:
    """Sends and receives whole messages over a HID backend."""

    def __init__(self, backend: HidBackend) -> None:
        self._backend = backend
        self._assembler = PacketAssembler()

    def send(self, payload: bytes) -> None:
        for packet in encode(payload):
            self._backend.write(packet)

    def receive(self, timeout_ms: int = 1000) -> bytes | None:
        """Read until a complete message arrives, or the device goes quiet."""
        while True:
            chunk = self._backend.read(PACKET_SIZE, timeout_ms)
            if not chunk:
                return None
            # hidapi can return a short read; the framing layer requires
            # exactly 64 bytes and the tail is zero padding either way.
            packet = bytes(chunk).ljust(PACKET_SIZE, b"\x00")
            message = self._assembler.feed(packet)
            if message is not None:
                return message

    def drain(self) -> int:
        """Discard anything the amp has already sent and reset reassembly.

        The amp emits status messages of its own accord, and a large exchange
        can leave replies queued behind the one a caller consumed. Starting a
        new request on top of that leftover data lands mid-message, which the
        framing layer correctly rejects as a continuation with no start.

        Returns the number of stale packets dropped, so callers can tell the
        difference between a clean line and a noisy one.
        """
        # A timeout of 0 means "block forever" in hidapi, not "poll" - use the
        # smallest positive timeout instead.
        dropped = 0
        while True:
            chunk = self._backend.read(PACKET_SIZE, DRAIN_TIMEOUT_MS)
            if not chunk:
                break
            dropped += 1
        self._assembler = PacketAssembler()
        return dropped

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class _HidDevice:
    """Adapts the `hid` package to the HidBackend protocol."""

    def __init__(self, device) -> None:
        self._device = device

    def write(self, data: bytes) -> None:
        self._device.write(bytes(data))

    def read(self, size: int, timeout_ms: int) -> bytes:
        return self._device.read(size, timeout_ms)

    def close(self) -> None:
        self._device.close()


def _open_hid_device():
    """Open the amp's HID interface. Separated out so tests can patch it.

    The `hidapi` package uses the older lowercase `hid.device()` factory with a
    separate `.open()`, not `hid.Device(...)`.

    Reports go out as a bare 64 bytes with no report-ID prefix; that was
    confirmed against the amp, which replies identically whether or not a
    leading 0x00 is prepended.
    """
    import hid

    device = hid.device()
    device.open(VENDOR_ID, PRODUCT_ID)
    return device


def open_transport(backend: HidBackend | None = None) -> Transport:
    """Open a transport, either over real USB or an injected backend."""
    if backend is not None:
        return Transport(backend)
    try:
        device = _open_hid_device()
    except Exception as exc:
        raise TransportError(
            f"no Mustang LT found at {VENDOR_ID:#06x}:{PRODUCT_ID:#06x}. "
            "Check the amp is powered on and connected by USB, and that "
            "Fender Tone LT Desktop is closed - only one program can hold "
            "the amp's control channel."
        ) from exc
    return Transport(_HidDevice(device))
