"""Restore preset slots from a backup.

    ./scripts/py scripts/restore.py --slots 60
    ./scripts/py scripts/restore.py --slots 31-60
    ./scripts/py scripts/restore.py --all

Only slots 31-60 can be written, so factory presets are never touched. With no
--backup given, the most recent complete backup is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.commands import WriteRefused, write_preset
from lt25_mcp.library import SLOT_MAX, WRITABLE_MIN, load_backup, latest_backup
from lt25_mcp.preset import Preset
from lt25_mcp.session import Session
from lt25_mcp.transport import TransportError, open_transport


def parse_slots(spec: str) -> list[int]:
    """Accepts '60', '31-40', or '31,35,60'."""
    slots: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            slots.extend(range(start, end + 1))
        else:
            slots.append(int(part))
    return sorted(set(slots))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, help="backup directory to restore from")
    parser.add_argument("--root", type=Path, default=Path("backups"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slots", help="slot, range, or comma-separated list")
    group.add_argument("--all", action="store_true", help="restore every writable slot")
    args = parser.parse_args(argv)

    backup = args.backup or latest_backup(args.root)
    if backup is None:
        print(f"error: no complete backup found under {args.root}", file=sys.stderr)
        return 1

    slots = (
        list(range(WRITABLE_MIN, SLOT_MAX + 1)) if args.all else parse_slots(args.slots)
    )
    presets = load_backup(backup)
    print(f"restoring {len(slots)} slot(s) from {backup}")

    try:
        with Session(open_transport()) as session:
            for slot in slots:
                preset = Preset.from_dict(presets[slot])
                try:
                    write_preset(session, preset, slot, backup_root=args.root)
                except WriteRefused as exc:
                    print(f"  {slot:02d}  skipped: {exc}", file=sys.stderr)
                    continue
                print(f"  {slot:02d}  {preset.display_name}")
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
