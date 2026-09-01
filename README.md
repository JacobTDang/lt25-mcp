# lt25-mcp

An MCP server for the Fender Mustang LT25, plus an audio pipeline that turns a
video clip into a preset.

The amp exposes two USB devices: an audio interface and a HID interface used for
control. This project speaks the HID side — reading, auditioning and writing
presets — so a tone can be built and heard without menu-diving on the encoder
wheel.

Verified against real hardware (Mustang LT 25, firmware 2.1.4).

## Quick start

```bash
uv sync
python3 scripts/backup.py        # back up all 60 slots first — writes refuse without it
./scripts/py -m pytest           # test suite
```

Scripts run with any `python3`, from any directory. Quit Fender Tone LT Desktop
first — only one program can hold the amp's control channel.

## MCP server

```bash
./scripts/mcp-server             # stdio
```

```json
{ "mcpServers": { "lt25": { "command": "/path/to/lt25-mcp/scripts/mcp-server" } } }
```

### Presets

| tool | |
|---|---|
| `list_presets` | all 60 slots with names and amp models |
| `get_preset` | one slot in full, every parameter |
| `backup_presets` | back up all 60 slots; required before any write |
| `audition_preset` | play a preset without saving it |
| `stop_audition` | return the amp to its loaded preset |
| `save_preset` | write to a slot (31-60 only, read-back verified) |
| `describe_preset` | every control this amp model exposes, on the 0-10 scale |
| `tune_preset` | change knobs, get modified JSON back; touches no hardware |
| `tuning_guide` | what to change from a plain-words complaint |
| `list_amp_models` | available amp models, FenderId and panel label |
| `list_effects` | available effects per slot |
| `amp_status` | connection, firmware, backup state |

### Audio

| tool | |
|---|---|
| `analyse_clip` | URL or local audio → preset; isolates guitar, measures, builds |
| `measure_audio` | brightness, band balance, saturation, key, tuning offset |
| `compare_to_target` | target vs. current, and which knob to move next |
| `record_take` | record through the amp's USB output (30s minimum) |

### Rig

| tool | |
|---|---|
| `get_rig` / `set_rig` | guitar and pedals in front of the amp |
| `list_guitars` | calibrated guitar profiles |
| `calibrate_guitar` | measure the plugged-in guitar and store a profile |
| `select_guitar` | say which guitar is plugged in now |
| `set_reference_guitar` | the guitar presets are built around |
| `rename_guitar` / `forget_guitar` | manage profiles |

`match_tone` is a prompt: the guided by-ear workflow.

## CLI

```bash
# presets
./scripts/py scripts/backup.py --dest ./backups
./scripts/py scripts/audition.py tone.json [--seconds 30]
./scripts/py scripts/install_preset.py tone.json --slot 31
./scripts/py scripts/restore.py --slots 31-60      # or --slots 60, --all

# clip to preset
./scripts/py -m lt25_mcp.analysis.cli \
  --url "https://youtube.com/watch?v=..." --start 3 --end 45 \
  --base tests/fixtures/clean.json --out tone.json --name "TONE"

# guitar profiling
./scripts/py scripts/session.py --name "squier strat"

# calibration and checks
./scripts/py scripts/calibrate.py add <clip.wav> --label high_gain
./scripts/py scripts/calibrate.py list | evaluate | sweep | evaluate-models | reverb
./scripts/py scripts/build_corpus.py
./scripts/py scripts/stability.py path/to/guitar.wav
./scripts/py scripts/mcp_smoke.py
./scripts/py scripts/workbench_demo.py --target <guitar-stem.wav>
```

Analysis CLI flags: `--audio` for a local file instead of `--url`,
`--no-separate` to skip guitar isolation, `--allow-fallback` to use the `other`
stem when no guitar stem is produced.

## Safety

Slots 1-30 are factory presets; 31-60 are writable.

- Writes refuse any slot outside 31-60.
- Writes refuse unless a complete 60-slot backup exists on disk.
- After writing, the slot is read back and compared; a mismatch raises.
- `audition` never touches amp memory.
- `scripts/restore.py` puts slots back; `MENU -> RESTORE` is a factory reset.

**Do not update the amp's firmware while working on this.** The protocol is
reverse-engineered against firmware 2.1.4 and is not contractual.

## What it is good at

Measured, not claimed — see [docs/measurements.md](docs/measurements.md).

| | |
|---|---|
| Amp control | solid, verified on hardware |
| Gain class from audio | 90% |
| Amp model within that class | 22% — no better than chance |
| Reverb from audio | abstains |
| EQ convergence from playing | unreliable; playing varies more than the knobs |

Amp model choice is the weak part and the code says so. Spectral features of a
mixed, mastered, lossily-encoded recording reflect the mix engineer and the
codec as much as the amp. Treat the output as a starting point and tune by ear.

## Docs

[measurements.md](docs/measurements.md) — what was measured on the amp ·
[roadmap.md](docs/roadmap.md) — what is worth doing next ·
[troubleshooting.md](docs/troubleshooting.md)

## Unofficial

Not affiliated with or endorsed by Fender. The protocol is undocumented by the
manufacturer and could change with any firmware update.
