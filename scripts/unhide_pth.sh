#!/usr/bin/env bash
# uv writes .pth files with the macOS UF_HIDDEN flag, and CPython >= 3.12
# silently skips hidden .pth files, breaking the editable install.
# Re-run this after any `uv add` / `uv sync`. See docs/troubleshooting.md.
set -euo pipefail
cd "$(dirname "$0")/.."
shopt -s nullglob
files=(.venv/lib/python*/site-packages/*.pth)
if (( ${#files[@]} )); then
  chflags nohidden "${files[@]}"
  echo "unhid ${#files[@]} .pth file(s)"
else
  echo "no .pth files found" >&2
  exit 1
fi
