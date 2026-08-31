"""Make `python3 scripts/whatever.py` work, whichever python that is.

Two things go wrong when a script is run directly rather than through
scripts/py, and both produce a ModuleNotFoundError that points at the wrong
culprit.

First, src/ is not on sys.path: uv writes its .pth files with the macOS hidden
flag and CPython >= 3.12 skips those, so the editable install does not take
effect. That is fixed by inserting src/ here.

Second, the interpreter may not be the project's. `/usr/bin/python3` has none
of the dependencies, and an active venv prompt is no guarantee that `python3`
resolves inside it. Rather than failing three imports later with "No module
named 'google'", this re-executes the same script under the project's
interpreter, once, and gets on with it.

Importing this first works because Python puts the running script's own
directory at the front of sys.path.
"""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"

# Guard against an exec loop if the venv itself is somehow incomplete.
_REEXEC_FLAG = "LT25_BOOTSTRAPPED"


def _has_dependencies() -> bool:
    from importlib.util import find_spec

    try:
        return find_spec("google.protobuf") is not None
    except (ImportError, ValueError):
        return False


if str(_SRC) not in sys.path and _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

if not _has_dependencies() and not os.environ.get(_REEXEC_FLAG):
    if _VENV_PYTHON.exists() and Path(sys.executable) != _VENV_PYTHON:
        os.environ[_REEXEC_FLAG] = "1"
        os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])
    raise SystemExit(
        f"This needs the project's dependencies.\n"
        f"  running under: {sys.executable}\n"
        f"  expected:      {_VENV_PYTHON}\n\n"
        f"Run `uv sync` in {_ROOT}, then try again."
    )
