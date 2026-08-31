# lt25-mcp

An MCP server for the Fender Mustang LT25, plus an audio pipeline that turns a
video clip into a preset.

The amp exposes two USB devices: an audio interface, and a HID interface used
for control. This project speaks the HID side — reading, auditioning and
writing presets — so a tone can be described, built and heard without
menu-diving on the amp's encoder wheel.

## Status

Both halves work end to end. Amp control is verified against real hardware
(Mustang LT 25, firmware 2.1.4), and the analysis pipeline has been run on a
real YouTube clip through to a preset.

| | |
|---|---|
| HID packet framing | done |
| Protobuf codec (67 messages) | done |
| HID transport | done, verified on hardware |
| Session handshake + heartbeat | done, verified on hardware |
| Read + back up 60 slots | done, verified on hardware |
| Preset model + DSP catalog | done |
| Audition | done, verified on hardware |
| Guarded writes to slots 31-60 | done, verified on hardware |
| MCP server (9 tools) | done |
| Analysis: acquire / stems / features / plots / mapping / CLI | done |
| Analysis on a real clip | done — see the worked example below |

## Quick start

```bash
uv sync
./scripts/py -m pytest              # 200+ tests
./scripts/py scripts/backup.py      # back up all 60 slots first
```

Quit Fender Tone LT Desktop before using any of this — only one program can
hold the amp's control channel.

### As an MCP server

```bash
claude mcp add lt25 -- ~/Desktop/projects/lt25-mcp/scripts/mcp-server
```

Nine tools: `list_presets`, `get_preset`, `backup_presets`, `audition_preset`,
`stop_audition`, `save_preset`, `list_amp_models`, `list_effects`, `amp_status`.

### From a clip to a preset

```bash
./scripts/py -m lt25_mcp.analysis.cli \
  --url "https://youtube.com/shorts/..." --start 3 --end 12 \
  --base tests/fixtures/clean.json --out tone.json --name "SURF LEAD"

./scripts/py scripts/audition.py tone.json      # hear it, nothing saved
```

The analysis CLI never touches the amp. Writing is always a separate,
explicit step.

## A worked example

The solo from *courage* by wave to earth, taken from a 35-second lesson video:

```bash
./scripts/py -m lt25_mcp.analysis.cli \
  --url "https://www.youtube.com/watch?v=NnxbDbVYV-Q" \
  --start 5 --end 30 \
  --base tests/fixtures/clean.json --out courage.json --name "COURAGE"
```

```
measured:
  centroid        1745 Hz
  low  80-250Hz   3.71%
  mid  400-1200Hz 41.48%
  high 2-8kHz     54.81%
  crest factor    12.4 dB
  harmonic ratio  0.97
  key             D
  tuning offset   +0.48 semitones

Amp model: DELUXE CLN
  Gain     2.6/10
  Treble   7.5/10
  Middle   4.6/10
  Bass     2.9/10
  Stomp    COMPRESSOR
  Reverb   SPRING 65
```

A 1965 Deluxe Reverb, clean, bright, with spring reverb and light compression —
a defensible reading of that record. The source is a full band mix, so the
guitar was separated out first; measured on the whole mix instead it reads
2050 Hz and 13.8 dB crest, which would have chosen a different amp entirely.

Three things that run went on to fix, each invisible until real audio hit them:
band ratios measured against total energy read near-zero bass on every isolated
stem, because separation removes the bass guitar; a lead line played high on the
neck has little low end whatever the amp's bass control is set to, so knobs are
now positioned relative to a typical guitar balance rather than from absolute
share; and a continuous take never falls 30 dB below its own peak, so reverb
detection reports "inconclusive" and leaves the base preset's reverb alone
instead of inventing a hall.


## How the amp talks

Control messages travel over USB HID as fixed 64-byte reports:

```
Host -> Device:  [tag][length][value (61 bytes)][0x00]
Device -> Host:  [0x00][tag][length][value (61 bytes)]
```

Replies are shifted one byte right behind a leading pad byte, so encoding and
decoding are not mirror images. Tags mark position in a multi-packet message:
`0x33` start, `0x34` continuation, `0x35` final or only packet. The reassembled
value is a protobuf `FenderMessageLT` (proto2, oneof named `type`); presets
themselves are JSON.

Notes worth knowing, all learned the hard way:

- `responseType` is `required` with a default, but a proto2 default does not
  satisfy required-ness on serialize — it must be set explicitly.
- `hidapi` treats a read timeout of `0` as *blocking*, not polling.
- The amp sends status messages unprompted, so a request drains stale input
  first; otherwise a later read lands mid-message.
- Parameter sets differ per amp model. Factory preset `CLASSIC ROCK` stores
  `mid` as `-4.400125` and `treb` as an integer, so imposing a `0.0..1.0`
  range would reject the amp's own data.

The protocol was reverse engineered by [brentmaxwell/LtAmp][ltamp], which
documents the [wire format][protocol] and carries the [protobuf schemas][schema]
extracted from Fender's desktop application. This is an independent Python
implementation of that documented protocol; no GPL source is vendored here.

[ltamp]: https://github.com/brentmaxwell/LtAmp
[protocol]: https://github.com/brentmaxwell/LtAmp/blob/main/Docs/Protocol.md
[schema]: https://github.com/brentmaxwell/LtAmp/tree/main/Schema/protobuf

## Safety

The amp holds 60 slots: 30 factory presets and 30 empty ones. Confirmed by
reading a real amp — slots 31-60 all report `EMPTY` from the factory.

- Writes refuse any slot outside 31-60.
- Writes refuse to run unless a complete 60-slot backup exists on disk.
- After writing, the slot is read back and compared; a mismatch raises.
- `audition` plays a preset through the speaker without storing it, so
  iteration never touches amp memory.
- `scripts/restore.py` puts slots back from a backup.
- `MENU -> RESTORE` on the amp is a factory reset if all else fails.

**Do not update the amp's firmware while working on this.** The protocol is
reverse-engineered against firmware 2.1.4 and is not contractual.

## Where the tone mapping stands

Turning a spectrum into an amp model is the weak part, and the code says so.
Spectral features of a mixed, mastered, lossily-encoded recording reflect the
mix engineer and the codec as much as the amp; a rule table over eighteen
models cannot recover a signal chain.

So `mapping.py` aims for a defensible starting point, not a match, and
`describe_settings()` prints knob positions on the amp's own 0-10 scale — if
the automatic choice is wrong, that is still better than starting from the
middle of every knob.

## Development

```bash
./scripts/py -m pytest
./scripts/gen_proto.sh     # regenerate protobuf bindings
```

See [docs/troubleshooting.md](docs/troubleshooting.md) if imports fail after a
sync: uv writes `.pth` files with the macOS hidden flag and CPython 3.12+
silently skips those, which breaks the editable install. `scripts/py` works
around it.

The implementation plan is in
[docs/superpowers/plans/](docs/superpowers/plans/).

## Unofficial

Not affiliated with or endorsed by Fender. The protocol is undocumented by the
manufacturer and could change with any firmware update.
