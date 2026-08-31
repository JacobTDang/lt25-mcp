"""Every script must run when invoked directly, with any python.

Running `python3 scripts/foo.py` is the obvious thing to do and it failed two
ways at once: src/ was not on sys.path, and /usr/bin/python3 has none of the
dependencies. Both produced a ModuleNotFoundError naming the wrong culprit.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(
    p for p in (ROOT / "scripts").glob("*.py")
    if p.name != "_bootstrap.py" and "lt25_mcp" in p.read_text()
)


def test_there_are_scripts_to_check():
    assert SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_runs_with_the_system_python_from_anywhere(script, tmp_path):
    """The failure mode was a user in scripts/ typing `python3 build_corpus.py`."""
    result = subprocess.run(
        ["/usr/bin/python3", str(script), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-600:]
    assert "usage:" in result.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_imports_the_bootstrap_before_the_package(script):
    text = script.read_text()
    assert "_bootstrap" in text, f"{script.name} would fail when run directly"
    assert text.index("_bootstrap") < text.index("from lt25_mcp")


def test_the_bootstrap_does_not_loop(tmp_path):
    """A re-exec that cannot find its dependencies must stop, not spin."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys; sys.path.insert(0, %r)\n"
        "import _bootstrap  # noqa\n"
        "print('reached')\n" % str(ROOT / "scripts")
    )
    result = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True, timeout=60
    )
    assert "reached" in result.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_parses_under_the_oldest_python_that_might_launch_it(script):
    """The bootstrap re-execs under the venv, but Python parses the whole file
    first - so a 3.12-only construct fails before the re-exec can help."""
    result = subprocess.run(
        ["/usr/bin/python3", "-m", "py_compile", str(script)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-400:]
