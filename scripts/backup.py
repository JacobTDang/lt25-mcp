"""Back up every preset on the amp to disk.

    ./scripts/py scripts/backup.py --dest ./backups

Quit Fender Tone LT Desktop first; only one program can hold the amp's
control channel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (puts src/ on sys.path)

from lt25_mcp.library import backup_all
from lt25_mcp.session import Session
from lt25_mcp.transport import TransportError, open_transport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path("backups"))
    args = parser.parse_args(argv)

    try:
        with Session(open_transport()) as session:
            print(f"connected: {session.product_id()} firmware {session.firmware_version()}")
            out = backup_all(session, args.dest)
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    slots = sorted(out.glob("slot-*.json"))
    print(f"backed up {len(slots)} slots to {out}")
    for path in slots:
        import json

        name = json.loads(path.read_text())["info"]["displayName"].strip()
        print(f"  {path.stem[-2:]}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
