"""Write a preset file to a slot on the amp.

    ./scripts/py scripts/install_preset.py courage.json --slot 31

Takes a preset produced by the analysis pipeline, or read off the amp and
edited, and saves it. `restore.py` only replays a backup; this installs a new
preset.

All the write guards apply: slots 1-30 hold the factory presets and are
refused, a complete backup must exist first, and the slot is read back and
compared field by field afterwards. Auditions it first by default, so the
preset is heard before it is committed to memory.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.analysis.mapping import describe_settings
from lt25_mcp.commands import WriteRefused, audition, exit_audition, write_preset
from lt25_mcp.library import backup_all, latest_backup, read_preset
from lt25_mcp.preset import Preset, PresetError
from lt25_mcp.session import Session
from lt25_mcp.transport import TransportError, open_transport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset", type=Path)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--name", help="override the preset's display name")
    parser.add_argument("--backups", type=Path, default=Path("backups"))
    parser.add_argument("--audition", type=float, default=15.0,
                        help="seconds to play it before saving; 0 to skip")
    args = parser.parse_args(argv)

    try:
        preset = Preset.from_dict(json.loads(args.preset.read_text()))
        if args.name:
            preset.display_name = args.name
    except (OSError, ValueError, PresetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(describe_settings(preset))
    print()

    try:
        with Session(open_transport()) as amp:
            if latest_backup(args.backups) is None:
                print("no backup yet - taking one before writing anything")
                backup_all(amp, args.backups)

            occupant = Preset.from_dict(read_preset(amp, args.slot))
            print(f"slot {args.slot} currently holds {occupant.display_name!r}")

            if args.audition > 0:
                audition(amp, preset)
                try:
                    print(f"auditioning for {args.audition:.0f}s - play something")
                    time.sleep(args.audition)
                finally:
                    exit_audition(amp)

            write_preset(amp, preset, args.slot, backup_root=args.backups)
            saved = Preset.from_dict(read_preset(amp, args.slot))
            print(f"\nslot {args.slot} now holds {saved.display_name!r} "
                  f"({saved.amp_label})")
    except WriteRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
