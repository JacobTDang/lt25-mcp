"""Reading presets off the amp, and backing up the whole 60-slot library.

The amp holds 60 slots. Slots 1-30 are Fender's factory presets and are
treated as read-only here; 31-60 are the writable range.

A backup is a directory of 60 JSON files plus a manifest. It is only
considered valid once all 60 slots are present, so an interrupted backup can
never be mistaken for a restore point.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SLOT_MIN = 1
SLOT_MAX = 60
WRITABLE_MIN = 31


class SlotError(Exception):
    """Raised when a slot number is outside the amp's range."""


def validate_slot(slot: int) -> int:
    if isinstance(slot, bool) or not isinstance(slot, int):
        raise SlotError(f"slot must be an integer in {SLOT_MIN}..{SLOT_MAX}, got {slot!r}")
    if not SLOT_MIN <= slot <= SLOT_MAX:
        raise SlotError(f"slot must be in {SLOT_MIN}..{SLOT_MAX}, got {slot}")
    return slot


def read_preset(session, slot: int) -> dict:
    """Fetch one preset from the amp as a parsed dict."""
    validate_slot(slot)
    reply = session.request(
        expect="presetJSONMessage", retrievePreset={"slot": slot}
    )
    return json.loads(reply.presetJSONMessage.data)


def backup_all(session, dest: Path) -> Path:
    """Read all 60 slots and write them to a timestamped directory.

    The directory is assembled under a temporary name and only moved into
    place once every slot has been read, so a failure part-way through leaves
    nothing that `latest_backup` would accept.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Timestamps are second-resolution, so two backups in quick succession can
    # collide. Never overwrite an existing backup - suffix instead.
    final = dest / f"backup-{stamp}"
    suffix = 1
    while final.exists():
        suffix += 1
        final = dest / f"backup-{stamp}-{suffix}"
    staging = dest / f".partial-{final.name}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    try:
        for slot in range(SLOT_MIN, SLOT_MAX + 1):
            preset = read_preset(session, slot)
            (staging / f"slot-{slot:02d}.json").write_text(
                json.dumps(preset, indent=2, sort_keys=True)
            )
        manifest = {
            "slot_count": SLOT_MAX,
            "firmware_version": session.firmware_version(),
            "product_id": session.product_id(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    staging.rename(final)
    return final


def is_complete(backup: Path) -> bool:
    """A backup counts only if all 60 slots and the manifest are present."""
    if not backup.is_dir() or not (backup / "manifest.json").exists():
        return False
    return all((backup / f"slot-{n:02d}.json").exists() for n in range(SLOT_MIN, SLOT_MAX + 1))


def latest_backup(root: Path) -> Path | None:
    """Most recent complete backup under `root`, or None."""
    root = Path(root)
    if not root.is_dir():
        return None
    candidates = [p for p in root.glob("backup-*") if is_complete(p)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)
