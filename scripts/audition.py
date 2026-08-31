"""Play a preset file through the amp without saving it.

    ./scripts/py scripts/audition.py tone.json [--seconds 30]

Nothing is written to the amp; leaving the audition restores whatever was
loaded before.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from lt25_mcp.analysis.mapping import describe_settings
from lt25_mcp.commands import audition_scope
from lt25_mcp.preset import Preset
from lt25_mcp.session import Session
from lt25_mcp.transport import TransportError, open_transport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset", type=Path)
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    preset = Preset.from_dict(json.loads(args.preset.read_text()))
    print(describe_settings(preset))
    try:
        with Session(open_transport()) as session:
            with audition_scope(session, preset):
                print(f"\nplaying for {args.seconds:.0f}s - nothing is being saved")
                time.sleep(args.seconds)
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("audition ended; the amp is back to its loaded preset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
