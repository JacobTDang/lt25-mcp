# Troubleshooting

## `ModuleNotFoundError: No module named 'lt25_mcp'` after `uv sync`

`uv` writes its `.pth` files into `site-packages` with the macOS `UF_HIDDEN`
file flag set. Since CPython 3.12, `site.addpackage()` skips any `.pth` file
carrying that flag, and it does so silently — no warning, no error. The
editable install looks correctly registered (`uv pip list` shows it, the
`.pth` file exists and contains the right path) but `src/` never lands on
`sys.path`.

Diagnosis:

```bash
ls -lO .venv/lib/python3.12/site-packages/*.pth   # shows "hidden"
uv run python -c "import sys; print([p for p in sys.path])"
```

Fix:

```bash
chflags nohidden .venv/lib/python3.12/site-packages/*.pth
```

**The flag comes back every time uv reinstalls the package**, so this is a
workaround rather than a cure. The test suite does not depend on it —
`pythonpath = ["src"]` in `pyproject.toml` puts `src/` on the path directly,
so `pytest` works regardless of install state.

## Amp is not found

Check the amp is enumerated as a HID device, not just as an audio device:

```bash
ioreg -c IOHIDInterface -r -l -w 0 | grep -A6 "Mustang"
```

Expect `MaxInputReportSize` and `MaxOutputReportSize` of 64. The amp
presents as vendor `0x1ED8`, product `0x0037`.

Only one program can hold a useful conversation with the amp at a time. Quit
Fender Tone LT Desktop before connecting from here.
