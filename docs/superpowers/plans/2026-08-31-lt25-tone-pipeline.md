# LT25 Tone Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take a video clip of a guitar tone and turn it into a preset written to a Fender Mustang LT25 in slots 31-60.

**Architecture:** Two independent subsystems joined by one contract. Phase 1 is an MCP server speaking the amp's USB HID protobuf protocol — it reads, auditions and writes presets. Phase 2 is an offline audio pipeline that isolates a guitar stem from a clip, measures its spectral character, and emits a preset. The contract between them is a single artifact: **a preset JSON document conforming to the amp's `version: "1.1"` schema**. Either phase is useful and testable without the other.

**Tech Stack:** Python 3.12, `hidapi` (USB HID), `protobuf` (wire codec), `mcp` (server), `yt-dlp` + `ffmpeg` (acquisition), `demucs` (stem isolation), `librosa` + `matplotlib` (analysis).

## Global Constraints

- Python `>=3.12`. Package managed by `uv`. Tests run with `uv run pytest`.
- **Writes target slots 31-60 only.** Slots 1-30 are factory presets and are read-only in this codebase. A write to slot <31 must raise, not warn.
- **A full 60-slot backup must exist on disk before any write is permitted.** No exceptions, no override flag in v1.
- Preset `info.displayName` is exactly 16 characters, space-padded, rendered on the amp as two 8-character lines.
- Amp tone parameters (`gain`, `treb`, `mid`, `bass`) are normalized floats `0.0..1.0`. The amp's display shows these as `0.0..10.0`. `volume` is dB and is negative (observed `-23.05..0.0`).
- Parameter sets are **per amp model**, not universal. Never synthesize a preset from an empty dict; always clone a known-good preset and mutate named fields.
- No mock data outside `tests/`. Test fixtures come from the user's own amp (Task 5), not from third-party repos.
- `DUBS_Passthru` is the FenderId meaning "this slot is empty".
- Heartbeat must be sent at least every 1s while a connection is open or the amp drops the session.
- **Do not update the amp's firmware while this project is in development.** The
  protocol is reverse-engineered against firmware 2.1.4 and is not contractual.
  Record the amp's actual firmware version in the Task 5 backup manifest before
  anything else, and treat a version change as invalidating every assumption below.
- Only one program may hold the amp session. `Session.open()` must detect a
  conflicting client and fail loudly rather than interleave with it.
- Never mention tooling or assistants in commit messages.

## Licensing note

The protocol documentation and protobuf schemas come from
[brentmaxwell/LtAmp](https://github.com/brentmaxwell/LtAmp), which is **GPL-3.0**.
Protocol facts are not copyrightable, but do not vendor that project's source
or its `mockAmpState.json` into this repo — doing so would impose GPL-3.0 on
this codebase. The `.proto` files are Fender's own schema, extracted; treat
them as interface definitions and regenerate rather than copy C# output.
Test fixtures are captured from the user's amp in Task 5.

## File Structure

```
src/lt25_mcp/
  framing.py        DONE - 64-byte HID TLV chunking/reassembly
  messages.py       Task 2 - protobuf codec, FenderMessageLT wrapping
  transport.py      Task 3 - HID device open/read/write, heartbeat
  session.py        Task 4 - handshake, request/response correlation
  preset.py         Task 6 - preset JSON model, clone-and-mutate, validation
  library.py        Task 5 - backup/restore all 60 slots to disk
  commands.py       Task 7/8 - audition, write, clear
  server.py         Task 9 - MCP tool surface
  dsp_catalog.py    Task 6 - FenderId vocabulary + per-model parameter specs
analysis/
  acquire.py        Task 10 - yt-dlp + ffmpeg trim
  stems.py          Task 11 - demucs guitar isolation
  features.py       Task 12 - librosa feature extraction
  plots.py          Task 13 - spectrogram rendering
  mapping.py        Task 14 - features -> preset JSON
  cli.py            Task 15 - end-to-end entry point
tests/              mirrors the above
```

---

## PHASE 1 — Amp control

### Task 1: HID packet framing — COMPLETE

Committed as `0dc9eef`. `src/lt25_mcp/framing.py`, 27 tests passing.

**Produces:** `encode(payload: bytes) -> list[bytes]`, `PacketAssembler().feed(packet: bytes) -> bytes | None`, `FramingError`, constants `PACKET_SIZE=64`, `MAX_PAYLOAD=61`, `TAG_START=0x33`, `TAG_CONTINUE=0x34`, `TAG_END=0x35`.

---

### Task 2: Protobuf message codec

**Files:**
- Create: `src/lt25_mcp/messages.py`
- Create: `proto/` (vendored `.proto` schema files, fetched not copied from C#)
- Create: `src/lt25_mcp/_generated/` (protoc output, gitignored)
- Create: `scripts/gen_proto.sh`
- Test: `tests/test_messages.py`
- Modify: `pyproject.toml` (add `protobuf` dependency)

**Interfaces:**
- Consumes: nothing from Task 1 (codec is independent of framing).
- Produces:
  - `encode_message(**kwargs) -> bytes` — builds a `FenderMessageLT`, returns serialized bytes
  - `decode_message(data: bytes) -> FenderMessageLT`
  - `which_payload(msg) -> str` — name of the populated oneof field
  - `MessageError`

- [ ] **Step 1: Fetch the .proto schemas**

```bash
mkdir -p proto
BASE=https://raw.githubusercontent.com/brentmaxwell/LtAmp/main/Schema/protobuf
curl -s "https://api.github.com/repos/brentmaxwell/LtAmp/contents/Schema/protobuf" \
  | python3 -c "import sys,json;[print(i['name']) for i in json.load(sys.stdin) if i['name'].endswith('.proto')]" \
  | while read f; do curl -sL -o "proto/$f" "$BASE/$f"; done
ls proto | wc -l   # expect ~60
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_messages.py
import pytest
from lt25_mcp.messages import decode_message, encode_message, which_payload

def test_heartbeat_round_trips():
    raw = encode_message(heartbeat={"dummyField": True})
    msg = decode_message(raw)
    assert which_payload(msg) == "heartbeat"

def test_firmware_request_sets_response_type():
    raw = encode_message(firmwareVersionRequest={"request": True})
    msg = decode_message(raw)
    assert which_payload(msg) == "firmwareVersionRequest"

def test_retrieve_preset_carries_slot():
    raw = encode_message(retrievePreset={"slot": 42})
    msg = decode_message(raw)
    assert msg.retrievePreset.slot == 42

def test_unknown_field_raises():
    with pytest.raises(Exception):
        encode_message(notARealMessage={"x": 1})
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_messages.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'lt25_mcp.messages'`

- [ ] **Step 4: Generate Python bindings**

```bash
# scripts/gen_proto.sh
set -e
mkdir -p src/lt25_mcp/_generated
uv run python -m grpc_tools.protoc -Iproto \
  --python_out=src/lt25_mcp/_generated \
  --pyi_out=src/lt25_mcp/_generated \
  proto/*.proto
touch src/lt25_mcp/_generated/__init__.py
```

Add `protobuf>=5.0` to `dependencies` and `grpcio-tools` to the dev group, then `uv sync && bash scripts/gen_proto.sh`.

- [ ] **Step 5: Implement the codec**

```python
# src/lt25_mcp/messages.py
from google.protobuf import json_format
from lt25_mcp._generated import FenderMessageLT_pb2

class MessageError(Exception):
    """Raised when a message cannot be built or parsed."""

def encode_message(**payload) -> bytes:
    if len(payload) != 1:
        raise MessageError(f"expected exactly one payload field, got {list(payload)}")
    msg = FenderMessageLT_pb2.FenderMessageLT()
    try:
        json_format.ParseDict(payload, msg)
    except json_format.ParseError as exc:
        raise MessageError(str(exc)) from exc
    return msg.SerializeToString()

def decode_message(data: bytes):
    msg = FenderMessageLT_pb2.FenderMessageLT()
    msg.ParseFromString(data)
    return msg

def which_payload(msg) -> str:
    name = msg.WhichOneof("type")
    if name is None:
        raise MessageError("message carries no payload")
    return name
```

The oneof is named `type` (lowercase) — verified from `FenderMessageLT.proto`.
The schema is **proto2**, not proto3: fields are `optional`/`required`, absent
fields are distinguishable from defaults, and `json_format.ParseDict` will
reject unknown fields rather than silently dropping them. That last behaviour
is what makes the `test_unknown_field_raises` test pass.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_messages.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add proto scripts src/lt25_mcp/messages.py tests/test_messages.py pyproject.toml uv.lock .gitignore
git commit -m "Add protobuf codec for FenderMessageLT"
```

---

### Task 3: HID transport

**Files:**
- Create: `src/lt25_mcp/transport.py`
- Test: `tests/test_transport.py`
- Modify: `pyproject.toml` (add `hidapi`)

**Interfaces:**
- Consumes: `framing.encode`, `framing.PacketAssembler`
- Produces:
  - `VENDOR_ID = 0x1ED8`, `PRODUCT_ID = 0x0037`
  - `class Transport` with `.send(payload: bytes)`, `.receive(timeout_ms: int) -> bytes | None`, `.close()`
  - `class HidBackend(Protocol)` with `.write(bytes)`, `.read(int, int)`, `.close()` — lets tests inject a fake
  - `open_transport(backend: HidBackend | None = None) -> Transport`
  - `TransportError`

The `HidBackend` seam is the whole point of this task: everything above it is testable with no amp attached.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transport.py
import pytest
from lt25_mcp.framing import encode
from lt25_mcp.transport import Transport, TransportError

class FakeBackend:
    def __init__(self, to_read=()):
        self.written = []
        self._to_read = list(to_read)
        self.closed = False
    def write(self, data): self.written.append(bytes(data))
    def read(self, size, timeout_ms):
        return self._to_read.pop(0) if self._to_read else b""
    def close(self): self.closed = True

def device_reply(payload):
    """Frame a payload the way the amp does: shifted one byte right."""
    out = []
    for p in encode(payload):
        tag, length = p[0], p[1]
        out.append((bytes([0, tag, length]) + p[2:2+length]).ljust(64, b"\x00"))
    return out

def test_send_writes_64_byte_reports():
    backend = FakeBackend()
    Transport(backend).send(b"hello")
    assert len(backend.written) == 1
    assert len(backend.written[0]) == 64

def test_send_splits_long_payload():
    backend = FakeBackend()
    Transport(backend).send(b"a" * 200)
    assert len(backend.written) == 4

def test_receive_reassembles_multi_packet_reply():
    backend = FakeBackend(device_reply(b"b" * 150))
    assert Transport(backend).receive(timeout_ms=10) == b"b" * 150

def test_receive_returns_none_on_timeout():
    assert Transport(FakeBackend()).receive(timeout_ms=1) is None

def test_close_closes_backend():
    backend = FakeBackend()
    Transport(backend).close()
    assert backend.closed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_transport.py -v`
Expected: FAIL, no module `lt25_mcp.transport`

- [ ] **Step 3: Implement**

```python
# src/lt25_mcp/transport.py
from __future__ import annotations
from typing import Protocol
from lt25_mcp.framing import PACKET_SIZE, PacketAssembler, encode

VENDOR_ID = 0x1ED8
PRODUCT_ID = 0x0037

class TransportError(Exception):
    """Raised when the HID device cannot be reached or behaves unexpectedly."""

class HidBackend(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, size: int, timeout_ms: int) -> bytes: ...
    def close(self) -> None: ...

class Transport:
    def __init__(self, backend: HidBackend) -> None:
        self._backend = backend
        self._assembler = PacketAssembler()

    def send(self, payload: bytes) -> None:
        for packet in encode(payload):
            self._backend.write(packet)

    def receive(self, timeout_ms: int = 1000) -> bytes | None:
        while True:
            chunk = self._backend.read(PACKET_SIZE, timeout_ms)
            if not chunk:
                return None
            message = self._assembler.feed(bytes(chunk).ljust(PACKET_SIZE, b"\x00"))
            if message is not None:
                return message

    def close(self) -> None:
        self._backend.close()

def open_transport(backend: HidBackend | None = None) -> Transport:
    if backend is not None:
        return Transport(backend)
    import hid
    try:
        device = hid.Device(VENDOR_ID, PRODUCT_ID)
    except Exception as exc:
        raise TransportError(
            f"no Mustang LT found at {VENDOR_ID:#06x}:{PRODUCT_ID:#06x}. "
            "Is the amp on and is Fender Tone LT Desktop closed?"
        ) from exc
    return Transport(_HidDevice(device))

class _HidDevice:
    def __init__(self, device): self._device = device
    def write(self, data): self._device.write(bytes(data))
    def read(self, size, timeout_ms): return self._device.read(size, timeout_ms)
    def close(self): self._device.close()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_transport.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/lt25_mcp/transport.py tests/test_transport.py pyproject.toml uv.lock
git commit -m "Add HID transport with injectable backend"
```

---

### Task 4: Session handshake and heartbeat

**Files:**
- Create: `src/lt25_mcp/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `Transport`, `encode_message`, `decode_message`, `which_payload`
- Produces:
  - `class Session` with `.open()`, `.close()`, `.request(timeout=2.0, **payload) -> msg`, `.firmware_version() -> str`
  - context-manager support (`__enter__` / `__exit__`)
  - `SessionError`

Handshake per the protocol doc: send `modalStatusMessage{context: SYNC_BEGIN, state: OK}`, await ack, send `SYNC_END`, await ack. Then a background thread sends `heartbeat{dummyField: true}` every 0.5s (half the 1s requirement, for margin).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session.py
import pytest
from lt25_mcp.messages import decode_message, encode_message, which_payload
from lt25_mcp.session import Session, SessionError

class ScriptedTransport:
    """Replies to each sent message with a canned response."""
    def __init__(self, replies=None):
        self.sent = []
        self._replies = list(replies or [])
        self.closed = False
    def send(self, payload): self.sent.append(decode_message(payload))
    def receive(self, timeout_ms=1000):
        return self._replies.pop(0) if self._replies else None
    def close(self): self.closed = True

def ack_modal():
    return encode_message(modalStatusMessage={"context": "SYNC_BEGIN", "state": "OK"})

def test_open_performs_sync_handshake():
    transport = ScriptedTransport([ack_modal(), ack_modal()])
    session = Session(transport, heartbeat=False)
    session.open()
    kinds = [which_payload(m) for m in transport.sent]
    assert kinds == ["modalStatusMessage", "modalStatusMessage"]
    assert transport.sent[0].modalStatusMessage.context == 0  # SYNC_BEGIN

def test_open_raises_if_amp_never_acks():
    session = Session(ScriptedTransport([]), heartbeat=False)
    with pytest.raises(SessionError, match="did not acknowledge"):
        session.open()

def test_request_returns_matching_reply():
    reply = encode_message(firmwareVersionStatus={"version": "2.1.4"})
    transport = ScriptedTransport([ack_modal(), ack_modal(), reply])
    with Session(transport, heartbeat=False) as session:
        assert session.firmware_version() == "2.1.4"

def test_close_closes_transport():
    transport = ScriptedTransport([ack_modal(), ack_modal()])
    session = Session(transport, heartbeat=False)
    session.open()
    session.close()
    assert transport.closed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_session.py -v`
Expected: FAIL, no module `lt25_mcp.session`

- [ ] **Step 3: Implement `Session`**

Requirements the tests pin down: `open()` sends two `modalStatusMessage`s and raises `SessionError("... did not acknowledge ...")` on a `None` reply; `request()` sends one message and returns the decoded reply, raising `SessionError` on timeout; `close()` stops the heartbeat thread then closes the transport; `heartbeat=False` disables the thread for tests. Use `threading.Thread(daemon=True)` with a `threading.Event` for shutdown, and guard `Transport` access with a `threading.Lock` since the heartbeat and request paths share it.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_session.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/lt25_mcp/session.py tests/test_session.py
git commit -m "Add amp session with sync handshake and heartbeat"
```

---

### Task 5: Read and back up all 60 slots — FIRST HARDWARE MILESTONE

**Files:**
- Create: `src/lt25_mcp/library.py`
- Create: `scripts/backup.py`
- Test: `tests/test_library.py`
- Create: `tests/fixtures/` (populated from the real amp in Step 5)

**Interfaces:**
- Consumes: `Session`
- Produces:
  - `read_preset(session, slot: int) -> dict`
  - `backup_all(session, dest: Path) -> Path` — writes `dest/backup-<ISO8601>/slot-NN.json` plus `manifest.json`
  - `latest_backup(root: Path) -> Path | None`
  - `SLOT_MIN = 1`, `SLOT_MAX = 60`, `WRITABLE_MIN = 31`
  - `SlotError`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_library.py
import json
import pytest
from lt25_mcp.library import SlotError, backup_all, latest_backup, read_preset

class FakeSession:
    def __init__(self): self.requested = []
    def request(self, timeout=2.0, **payload):
        slot = payload["retrievePreset"]["slot"]
        self.requested.append(slot)
        class R:
            class presetJSONMessage:
                data = json.dumps({"info": {"displayName": f"SLOT {slot:02d}      "[:16]}})
                slotIndex = slot
        return R

def test_read_preset_returns_parsed_json():
    assert read_preset(FakeSession(), 5)["info"]["displayName"].startswith("SLOT 05")

@pytest.mark.parametrize("slot", [0, 61, -1, 999])
def test_read_preset_rejects_out_of_range_slot(slot):
    with pytest.raises(SlotError, match="1..60"):
        read_preset(FakeSession(), slot)

def test_backup_all_writes_60_slots(tmp_path):
    session = FakeSession()
    out = backup_all(session, tmp_path)
    assert session.requested == list(range(1, 61))
    assert len(list(out.glob("slot-*.json"))) == 60
    assert json.loads((out / "manifest.json").read_text())["slot_count"] == 60

def test_latest_backup_finds_most_recent(tmp_path):
    assert latest_backup(tmp_path) is None
    out = backup_all(FakeSession(), tmp_path)
    assert latest_backup(tmp_path) == out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_library.py -v`
Expected: FAIL, no module `lt25_mcp.library`

- [ ] **Step 3: Implement `library.py`**

`read_preset` validates the slot is in `1..60` (raising `SlotError("slot must be in 1..60")`), issues `retrievePreset`, and `json.loads` the `presetJSONMessage.data` field. `backup_all` loops 1..60, writes one file per slot plus a `manifest.json` recording `slot_count`, `firmware_version`, and an ISO-8601 `created_at`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_library.py -v`
Expected: 7 passed

- [ ] **Step 5: Run against the real amp**

Quit Fender Tone LT Desktop first — only one program can hold the session.

```bash
uv run python scripts/backup.py --dest ./backups
ls backups/backup-*/ | head
uv run python -c "
import json,glob
p=sorted(glob.glob('backups/backup-*/slot-*.json'))[0]
d=json.load(open(p))
print(d['info']['displayName'])
print([n['FenderId'] for n in d['audioGraph']['nodes']])
"
```

Expected: 60 files. The first slot's `displayName` should match what the amp screen shows for preset 01, and the node list should contain five `DUBS_*` ids. **This is the moment the protocol is proven end to end.**

- [ ] **Step 6: Capture test fixtures from the real amp**

Copy three real presets into `tests/fixtures/` — one clean, one high-gain, one with an empty effect slot (`DUBS_Passthru`). These become the fixtures for Task 6 and carry no third-party licence.

- [ ] **Step 7: Commit**

```bash
git add src/lt25_mcp/library.py scripts/backup.py tests/test_library.py tests/fixtures
git commit -m "Add preset reading and full 60-slot backup"
```

---

### Task 6: Preset model and DSP catalog

**Files:**
- Create: `src/lt25_mcp/preset.py`
- Create: `src/lt25_mcp/dsp_catalog.py`
- Test: `tests/test_preset.py`

**Interfaces:**
- Consumes: `tests/fixtures/*.json` from Task 5
- Produces:
  - `class Preset` wrapping the raw dict, with `.display_name` (get/set, enforcing 16 chars), `.amp_model` (get/set FenderId), `.node(node_id) -> dict`, `.set_param(node_id, name, value)`, `.to_dict()`, `.clone()`
  - `Preset.from_dict(d) -> Preset`
  - `AMP_MODELS: dict[str, str]` — FenderId to human label
  - `EFFECTS: dict[str, dict[str, str]]` — keyed by node id (`stomp`/`mod`/`delay`/`reverb`)
  - `PASSTHRU = "DUBS_Passthru"`
  - `PresetError`

The catalog seeds from the 18 amp models and 27 effects observed in factory presets:

```python
AMP_MODELS = {
    "DUBS_Twin57": "50S TWIN",         "DUBS_Ac30Tb": "60S UK CLN",
    "DUBS_Plexi87": "70S ROCK",        "DUBS_DR103": "70S UK CLN",
    "DUBS_Jcm800": "80S ROCK",         "DUBS_Rect2": "90S ROCK",
    "DUBS_Bassman59": "BASSMAN",       "DUBS_SuperSonic": "BURN",
    "DUBS_Champ57": "CHAMP",           "DUBS_Deluxe65": "DELUXE CLN",
    "DUBS_Deluxe57": "DELUXE DIRT",    "DUBS_Or120": "DOOM METAL",
    "DUBS_Excelsior": "EXCELSIOR",     "DUBS_MetalRect2": "ALT METAL",
    "DUBS_Evh3": "METAL 2000",         "DUBS_Princeton65": "PRINCETON",
    "DUBS_LinearGain": "SUPER CLEAN",  "DUBS_Twin65": "TWIN CLEAN",
}
```

Two names from the manual (`SMALLTONE`, `SUPER HEAVY`) do not appear in factory presets; leave them out until observed on the real amp, and add a test asserting the catalog covers every FenderId seen in `tests/fixtures/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preset.py
import json
from pathlib import Path
import pytest
from lt25_mcp.dsp_catalog import AMP_MODELS, PASSTHRU
from lt25_mcp.preset import Preset, PresetError

FIXTURES = Path(__file__).parent / "fixtures"

def load(name):
    return Preset.from_dict(json.loads((FIXTURES / name).read_text()))

def test_display_name_is_padded_to_16():
    p = load("clean.json")
    p.display_name = "JAZZ"
    assert p.to_dict()["info"]["displayName"] == "JAZZ" + " " * 12

def test_display_name_over_16_raises():
    with pytest.raises(PresetError, match="16"):
        load("clean.json").display_name = "A" * 17

def test_clone_is_independent():
    original = load("clean.json")
    copy = original.clone()
    copy.set_param("amp", "gain", 0.9)
    assert original.node("amp")["dspUnitParameters"]["gain"] != 0.9

def test_set_param_rejects_unknown_parameter():
    with pytest.raises(PresetError, match="not a parameter"):
        load("clean.json").set_param("amp", "nonsense", 0.5)

def test_every_factory_preset_survives_a_round_trip(backup_dir):
    """The amp's own data is the spec. If validation rejects it, validation is wrong."""
    for path in sorted(backup_dir.glob("slot-*.json")):
        raw = json.loads(path.read_text())
        assert Preset.from_dict(raw).to_dict() == raw, path.name

def test_amp_model_setter_rejects_unknown_fender_id():
    with pytest.raises(PresetError, match="unknown amp model"):
        load("clean.json").amp_model = "DUBS_NotAnAmp"

def test_empty_effect_slot_reads_as_passthru():
    assert load("empty_slot.json").node("mod")["FenderId"] == PASSTHRU

def test_catalog_covers_every_fixture_amp():
    for path in FIXTURES.glob("*.json"):
        amp = Preset.from_dict(json.loads(path.read_text())).amp_model
        assert amp in AMP_MODELS, f"{path.name} uses uncatalogued amp {amp}"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_preset.py -v`
Expected: FAIL, no module `lt25_mcp.preset`

- [ ] **Step 3: Implement**

`set_param` looks up the existing parameter dict for the node and rejects names not already present — this is what enforces "clone and mutate, never synthesize".

**Do not hard-code parameter ranges.** Factory preset 14 (`CLASSIC ROCK`,
`DUBS_Plexi87`) carries `mid = -4.400125`, well outside the `0.0..1.0` that
every other preset uses, and `treb = 1` as an *integer* rather than a float. A
validator asserting `0.0..1.0` would reject the amp's own factory data — the
plan originally contained exactly that bug.

Instead, derive ranges empirically per `(FenderId, parameter)` from the 60-slot
backup captured in Task 5, and treat anything within observed bounds as valid.
Where a parameter has only ever been observed at one value, allow the full
`0.0..1.0` rather than pinning it. Round-trip fidelity is the real invariant,
which is what `test_every_factory_preset_survives_a_round_trip` enforces.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_preset.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/lt25_mcp/preset.py src/lt25_mcp/dsp_catalog.py tests/test_preset.py
git commit -m "Add preset model and DSP unit catalog"
```

---

### Task 7: Audition

**Files:**
- Create: `src/lt25_mcp/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `Session`, `Preset`
- Produces:
  - `audition(session, preset: Preset) -> None`
  - `exit_audition(session) -> None`
  - `audition_scope(session, preset)` — context manager guaranteeing exit even on exception

Audition sends `auditionPreset{data: <json>}`; exit sends `exitAuditionPreset`. Nothing is written to amp memory.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commands.py
import pytest
from lt25_mcp.commands import audition, audition_scope, exit_audition

class RecordingSession:
    def __init__(self): self.calls = []
    def request(self, timeout=2.0, **payload):
        self.calls.append(next(iter(payload)))
        return object()

def test_audition_sends_audition_preset(sample_preset):
    s = RecordingSession()
    audition(s, sample_preset)
    assert s.calls == ["auditionPreset"]

def test_exit_audition_sends_exit(sample_preset):
    s = RecordingSession()
    exit_audition(s)
    assert s.calls == ["exitAuditionPreset"]

def test_scope_exits_even_on_exception(sample_preset):
    s = RecordingSession()
    with pytest.raises(RuntimeError):
        with audition_scope(s, sample_preset):
            raise RuntimeError("boom")
    assert s.calls == ["auditionPreset", "exitAuditionPreset"]
```

Add a `sample_preset` fixture in `tests/conftest.py` loading `tests/fixtures/clean.json`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_commands.py -v`
Expected: FAIL, no module `lt25_mcp.commands`

- [ ] **Step 3: Implement, then verify on hardware**

```bash
uv run python -c "
from lt25_mcp.session import Session
from lt25_mcp.transport import open_transport
from lt25_mcp.commands import audition_scope
from lt25_mcp.preset import Preset
import json, time
p = Preset.from_dict(json.load(open('tests/fixtures/clean.json')))
p.set_param('amp','gain',0.95); p.display_name='AUDITION TEST'
with Session(open_transport()) as s:
    with audition_scope(s, p):
        print('playing - strum something'); time.sleep(15)
print('exited; amp should be back to its previous preset')
"
```

Expected: the amp audibly changes for 15 seconds, then reverts. Slot contents unchanged.

- [ ] **Step 4: Commit**

```bash
git add src/lt25_mcp/commands.py tests/test_commands.py tests/conftest.py
git commit -m "Add preset audition with guaranteed exit"
```

---

### Task 8: Write to a slot

**Files:**
- Modify: `src/lt25_mcp/commands.py`
- Modify: `tests/test_commands.py`

**Interfaces:**
- Produces:
  - `write_preset(session, preset: Preset, slot: int, *, backup_root: Path) -> None`
  - `WriteRefused(SlotError)`

Two guards, both hard failures: slot must be `>= 31`, and `latest_backup(backup_root)` must return a directory containing 60 slot files.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("slot", [1, 30, 0, 61])
def test_write_refuses_protected_or_invalid_slots(sample_preset, tmp_path, slot):
    with pytest.raises(WriteRefused, match="31..60"):
        write_preset(RecordingSession(), sample_preset, slot, backup_root=tmp_path)

def test_write_refuses_without_backup(sample_preset, tmp_path):
    with pytest.raises(WriteRefused, match="no backup"):
        write_preset(RecordingSession(), sample_preset, 31, backup_root=tmp_path)

def test_write_succeeds_with_backup(sample_preset, tmp_path, fake_backup):
    s = RecordingSession()
    write_preset(s, sample_preset, 47, backup_root=tmp_path)
    assert "savePresetAs" in s.calls
```

`fake_backup` is a fixture creating `tmp_path/backup-.../slot-01..60.json`.

- [ ] **Step 2-4: Verify failure, implement, verify pass**

Run: `uv run pytest tests/test_commands.py -v`
Expected: all passing.

- [ ] **Step 5: Hardware check — write to slot 60, then restore**

```bash
uv run python scripts/backup.py --dest ./backups     # fresh backup first
# write, confirm on the amp screen, then re-read slot 60 and diff
```

Expected: amp preset 60 shows the new name; re-reading slot 60 returns matching JSON.

- [ ] **Step 6: Commit**

```bash
git commit -am "Add guarded preset writes to slots 31-60"
```

---

### Task 9: MCP server

**Files:**
- Create: `src/lt25_mcp/server.py`
- Test: `tests/test_server.py`
- Modify: `pyproject.toml` (add `mcp`, add `[project.scripts] lt25-mcp = "lt25_mcp.server:main"`)

**Interfaces:**
- Tools exposed: `list_presets()`, `get_preset(slot)`, `backup_presets()`, `audition_preset(preset_json)`, `stop_audition()`, `write_preset(preset_json, slot)`, `list_amp_models()`, `list_effects()`

Tools return structured dicts, never raw protobuf. Every tool that touches hardware opens a session, acts, and closes — no long-lived global connection, so a crashed tool call cannot wedge the amp.

- [ ] **Step 1-5:** Standard TDD cycle. Test each tool against a `FakeSession`, assert the returned dict shape. Then register in Claude Code:

```bash
claude mcp add lt25 -- uv --directory ~/Desktop/projects/lt25-mcp run lt25-mcp
```

- [ ] **Step 6: Commit**

```bash
git add src/lt25_mcp/server.py tests/test_server.py pyproject.toml
git commit -m "Add MCP server exposing amp preset tools"
```

---

## PHASE 2 — Tone analysis

Independently executable. Produces preset JSON consumable by Phase 1.

### Task 10: Audio acquisition

**Files:** Create `analysis/acquire.py`, `tests/test_acquire.py`. Add `yt-dlp` dependency (`brew install yt-dlp` or the Python package).

**Interfaces:**
- `fetch_audio(url: str, dest: Path) -> Path` — downloads best audio, converts to 44.1 kHz mono WAV
- `trim(src: Path, start: float, end: float, dest: Path) -> Path` — ffmpeg segment extract
- `AcquisitionError`

Tests inject a fake subprocess runner and assert the constructed argv; no network in tests. One manual step downloads a real Short to confirm the argv actually works.

- [ ] Steps 1-5: failing test on argv construction, implement, verify, one live download, commit.

---

### Task 11: Guitar stem isolation

**Files:** Create `analysis/stems.py`, `tests/test_stems.py`. Add `demucs` (pulls `torch`, ~2.5 GB).

**Interfaces:**
- `isolate_guitar(src: Path, dest: Path, model: str = "htdemucs_6s") -> Path`
- `StemError` when the model produces no guitar stem

`htdemucs_6s` splits into drums, bass, other, vocals, **guitar**, piano. Meta labels the 6-source model experimental; on material where it fails, fall back to `other` only with an explicit caller opt-in, never silently.

- [ ] Steps 1-5: test model/argv selection and the missing-stem error path with a fake runner, implement, run once on a real clip, listen to the stem to sanity-check, commit.

---

### Task 12: Feature extraction

**Files:** Create `analysis/features.py`, `tests/test_features.py`. Add `librosa`.

**Interfaces:**
- `extract(path: Path) -> ToneFeatures` (a frozen dataclass)
- Fields: `spectral_centroid_hz`, `spectral_rolloff_hz`, `mid_energy_ratio` (400-800 Hz over total), `low_energy_ratio` (<150 Hz), `crest_factor_db`, `harmonic_ratio`, `onset_strength`, `decay_time_s`, `estimated_key`, `estimated_tempo_bpm`, `tuning_offset_semitones`

Tests use **synthesized** signals with known properties, not fixtures: a pure 440 Hz sine must yield a centroid near 440 and a high harmonic ratio; white noise must yield a high centroid and low harmonic ratio; a square wave must sit between. This is how the extractor is validated without a golden-ear judgement call.

- [ ] Steps 1-5: write synthetic-signal tests, implement, verify, commit.

---

### Task 13: Spectrogram rendering

**Files:** Create `analysis/plots.py`, `tests/test_plots.py`. Add `matplotlib`.

**Interfaces:**
- `spectrogram(path: Path, dest: Path) -> Path` — log-frequency mel spectrogram PNG
- `compare(a: Path, b: Path, dest: Path) -> Path` — two spectra overlaid, target vs. current

Tests assert a non-empty PNG with expected dimensions. The real consumer is visual inspection: these images are what let a tone be judged rather than merely measured.

- [ ] Steps 1-5.

---

### Task 14: Feature-to-preset mapping

**Files:** Create `analysis/mapping.py`, `tests/test_mapping.py`.

**Interfaces:**
- `choose_amp_model(features: ToneFeatures) -> str` — returns a FenderId
- `build_preset(features: ToneFeatures, base: Preset) -> Preset`
- `MappingError`

This is the honest weak point of the pipeline and the plan should not pretend otherwise. There is no amp-model simulator to search against offline, so v1 is a rule table over the measured features:

| Condition | Choice |
|---|---|
| `crest_factor_db > 14` and `harmonic_ratio > 0.8` | clean models (`DUBS_Twin65`, `DUBS_Deluxe65`, `DUBS_Princeton65`) |
| `crest_factor_db < 8` and `spectral_centroid_hz > 2500` | high-gain (`DUBS_Evh3`, `DUBS_MetalRect2`, `DUBS_Rect2`) |
| `mid_energy_ratio > 0.35` | mid-forward (`DUBS_Plexi87`, `DUBS_Jcm800`) |
| `decay_time_s > 1.5` | add reverb; size from decay length |
| otherwise | `DUBS_Deluxe57` edge-of-breakup |

EQ is set by mapping measured band ratios onto `treb`/`mid`/`bass` in `0.0..1.0`. `build_preset` always starts from `base` (a real preset read off the amp) and mutates named parameters, per the global constraint.

Tests assert the rules fire: a synthesized clean-signal feature set must select a clean model, a compressed bright one must select high-gain. These are unit tests of the rule table, not claims about musical accuracy.

- [ ] Steps 1-5.

---

### Task 15: End-to-end CLI

**Files:** Create `analysis/cli.py`, `tests/test_cli.py`.

```
uv run python -m analysis.cli \
  --url "https://youtube.com/shorts/..." --start 3 --end 12 \
  --base tests/fixtures/clean.json --out tone.json --spectrogram tone.png
```

Emits a preset JSON and a spectrogram. Writing to the amp stays a separate, explicit step through Phase 1 — the analysis CLI never touches hardware.

- [ ] Steps 1-5.

---

## The convergence loop

Once both phases exist, the loop that actually produces a good tone:

1. Analyse the target clip → `target_features`, target spectrogram
2. `build_preset` → candidate
3. `audition` the candidate on the amp
4. Record the user playing through it via the amp's USB audio interface
5. `extract` features from that recording → `current_features`
6. `compare` spectrograms; adjust `treb`/`mid`/`bass`/`gain` toward the target
7. Repeat from 3 until close, then `write_preset` to a slot in 31-60

Steps 4-6 need the amp's USB **audio** input, which is a separate device from the HID control channel — both are available at once. This loop is deliberately out of scope for v1; it is why the interfaces above keep features and presets as plain data.

---

## Rejected alternatives

Recorded because both are genuinely competitive and were not obvious at the outset.

**A. Analysis-only, no write path.** Emit the answer as text — "DELUXE CLN,
gain 4.2, treble 6.5, middle 3.0, add SPRING 65" — and dial it in by hand on the
amp in under a minute. This eliminates every hardware risk, the firmware
coupling, and roughly half the plan. It is rejected *only* because the
audition-and-converge loop needs programmatic writes to be worth anything; if
Phase 2's mapping turns out weak (see Risks), fall back to this and delete
Tasks 7-9. **Task 14 should therefore emit a human-readable settings summary
alongside the preset JSON from day one**, so this fallback is free.

**B. Preset search instead of signal analysis.** Fender's factory presets are
already named after songs — the 60 slots include `ENTER SANDMAN`, `EVERLONG`,
`BACK IN BLACK`, `FEAR THE REAPER`, `HASH PIPE`. Semantic search over those plus
Fender Tone's cloud library would answer "get me the Everlong tone" better and
far more cheaply than deriving it from a spectrogram. Rejected as the *primary*
approach because it cannot handle a tone nobody has published a preset for —
which is the actual use case. Worth building as a first-pass lookup that runs
before the DSP pipeline.

## Risks

Ranked by likelihood × severity. See the accompanying critique for full reasoning.

1. **The mapping produces unconvincing tones** (near-certain, moderate). Spectral
   features of mixed, mastered, lossily-encoded audio reflect the mix engineer
   and the codec as much as the amp. Five rules over 18 models is thin. This is a
   product failure, not a crash, and it is the single most likely way this ends
   up unused. Mitigation: alternative A above, plus judging by ear against the
   spectrogram rather than trusting the numbers.
2. **Firmware update breaks the protocol** (possible, high). Mitigated by the
   global constraint above; note that Fender Tone actively offers updates.
3. **Interrupted write leaves a slot in an unknown state** (unlikely, moderate).
   Mitigation: Task 8 must re-read the slot after writing and compare against
   what was sent, failing loudly on mismatch.
4. **yt-dlp breakage** (near-certain over time, low). Routine; pin nothing, update often.

## Open questions to resolve during execution

Resolved during planning — recorded so they are not re-litigated:

- ~~oneof field name~~ — it is `type`, lowercase. Verified from `FenderMessageLT.proto`.
- ~~does `savePresetAs` exist~~ — yes. `SavePresetAs.proto` is present, alongside
  `SaveCurrentPreset`, `SaveCurrentPresetTo`, `RenamePresetAt`, `ShiftPreset`,
  `SwapPreset` and `ClearPreset`. 67 `.proto` files total.
- ~~is `mid = -4.4` bad data~~ — it is real factory data in slot 14. See Task 6.
- ~~will macOS gate HID access~~ — the amp reports `PrimaryUsagePage = 0xFF00`
  (vendor-defined), not a generic-desktop or keyboard page, so it should not
  require Input Monitoring permission. Still unproven until Task 3 runs.

Genuinely open:

1. Whether writing to an occupied slot requires a preceding `ClearPreset`.
2. Whether `displayName`'s 16-character limit is characters or bytes, and what
   the amp does with non-ASCII.
3. Whether `SavePresetAs` or `SaveCurrentPresetTo` is the correct write verb.
4. Whether presets are portable across LT models — device `productId` is
   `mustang-lt-25` but preset `info.product_id` is the broader `mustang-lt`.
