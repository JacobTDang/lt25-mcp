# Troubleshooting

## `ModuleNotFoundError: No module named 'lt25_mcp'`

**Use `./scripts/py` instead of `uv run python` and this cannot happen.**

`uv` writes its `.pth` files into `site-packages` with the macOS `UF_HIDDEN`
file flag set. Since CPython 3.12, `site.addpackage()` skips any `.pth` file
carrying that flag, and it does so silently — no warning, no error. The
editable install looks correctly registered (`uv pip list` shows it, the
`.pth` file exists and contains the right path) but `src/` never lands on
`sys.path`. The flag comes back on every `uv add` / `uv sync`.

Diagnosis:

```bash
ls -lO .venv/lib/python3.12/site-packages/*.pth   # shows "hidden"
uv run python -c "import sys; print(sys.path)"    # src/ is absent
```

### How this project avoids it

Nothing here depends on the editable install resolving:

- `scripts/py` and `scripts/mcp-server` set `PYTHONPATH` to `src/` directly.
- `pythonpath = ["src"]` in `[tool.pytest.ini_options]` does the same for tests.

So `./scripts/py -m pytest` and the MCP server both work whether the flag is
set or not.

### If you are using plain `uv run python`

Either switch to `./scripts/py`, or clear the flag by hand:

```bash
./scripts/unhide_pth.sh      # chflags nohidden on every .pth
```

That is a workaround, not a cure — the flag returns on the next sync.

## Amp is not found

Check the amp is enumerated as a HID device, not just as an audio device:

```bash
ioreg -c IOHIDInterface -r -l -w 0 | grep -A6 "Mustang"
```

Expect `MaxInputReportSize` and `MaxOutputReportSize` of 64. The amp
presents as vendor `0x1ED8`, product `0x0037`, on HID usage page `0xFF00`
(vendor-defined), which is why macOS does not require Input Monitoring
permission for it.

Only one program can hold a useful conversation with the amp at a time. Quit
Fender Tone LT Desktop before connecting from here — if it is running, the
sync handshake never gets acknowledged and `Session.open()` says so.

## A request hangs, or a `FramingError` mentions a continuation packet

The amp emits status messages unprompted, so replies can queue up behind the
one a caller consumed. `Session.request()` drains stale input before sending
to avoid landing mid-message.

If you are driving `Transport` directly, call `drain()` first. Note that
`hidapi` treats a read timeout of `0` as *blocking*, not polling — use a small
positive timeout.
