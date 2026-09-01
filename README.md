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
python3 scripts/backup.py           # back up all 60 slots first
```

Scripts run with any `python3`, from any directory. They put `src/` on the
path themselves and re-execute under the project's interpreter if the one
invoking them lacks the dependencies, so there is nothing to activate and no
wrapper to remember.

For the test suite, `./scripts/py -m pytest`.

Quit Fender Tone LT Desktop before using any of this — only one program can
hold the amp's control channel.

### As an MCP server

```bash
claude mcp add lt25 -- ~/Desktop/projects/lt25-mcp/scripts/mcp-server
```

Twelve tools and one prompt. The server is built to be *driven* by an
assistant, not just called by one:

| | |
|---|---|
| Read | `list_presets`, `get_preset`, `describe_preset`, `amp_status` |
| Tune | `tune_preset`, `tuning_guide`, `list_amp_models`, `list_effects` |
| Hear | `audition_preset`, `stop_audition` |
| Commit | `backup_presets`, `save_preset` |
| Prompt | `match_tone` — the guided by-ear workflow |

Check it without an amp attached:

```bash
./scripts/py scripts/mcp_smoke.py
```

### Tuning by ear

An assistant cannot hear the amp; the player can. So the server is shaped
around a conversation rather than a calculation.

`describe_preset` answers "what can I even change here?" — every control this
particular amp model exposes, what it does musically, its value on the amp's
own 0-10 scale, and the values it accepts. Parameter sets differ per model, so
this is driven by the preset rather than by a fixed list.

`tuning_guide` closes the gap between what a player says and which knob to
move. Ask it about "too fizzy" and it answers treble -1.5, presence -1.0,
gain -0.5, with the reasoning for each — plus structural advice for the cases
where no amount of EQ is the answer, like reaching for the cabinet simulation
or the noise gate instead.

`tune_preset` applies changes on the 0-10 scale and validates as it goes: a
knob this model does not have, a value out of range, or an invented cabinet
name are all refused rather than sent to the amp.

The loop is: start from a real preset, audition it, ask what is wrong, look
the answer up, change **one** thing, audition again.

### The rig matters

A preset is not a tone on its own. The same settings sound different through
different pickups, and loading an overdrive into the stomp slot is wrong if
there is already a real one in front of the amp.

`get_rig` and `set_rig` record that once. Humbuckers put out roughly twice the
level of single-coils and are darker, so a preset built from a recording of
somebody else's guitar arrives hotter and duller: `tune_preset(apply_rig=True)`
backs the gain off a notch and opens the treble up, and says that it did.
Declared pedals mark their matching amp slot as one to leave empty rather than
stacking two of the same effect.

Adjustments only apply when the pickup type is actually known — an undeclared
rig changes nothing rather than guessing.

### Adapting to any guitar, by measurement

Pickup category is a coarse prior. "Humbucker" spans a vintage PAF and a
ceramic bridge unit that sound nothing alike, and it says nothing about string
gauge, pickup height or how hard someone picks. So rather than categorising
harder, the amp measures.

`calibrate_guitar` records through the amp's USB audio output while the player
plays, and stores what it measured: output level, brightness, pick dynamics,
sustain. The first guitar calibrated becomes the reference — the one presets
were built around — and every other guitar is stored as a delta from it.
`select_guitar` says which is plugged in now, and presets shift by that delta:

```
preset built for the reference guitar:  gain 5.0  treb 5.0
same preset through the les paul:       gain 2.9  treb 6.4
  gain 5.0 -> 2.9: epiphone lp is 6.0 dB hotter than squier strat
  treb 5.0 -> 6.4: epiphone lp is darker by 0.68 octaves of spectral centre
```

No categories, no magic constants — it works for whatever gets plugged in,
including the same guitar after a pickup swap or a change of string gauge.
Capture goes through ffmpeg's avfoundation input, so it needs no audio library
beyond what is already installed.

The same calibration runs without an assistant:

```bash
./scripts/py scripts/session.py --name "squier strat"
```

It auditions the reference preset itself and starts each take when it hears
playing, so nothing has to be timed. With two or more takes it also reports
how much identical playing varies between takes — the noise floor under the
convergence loop, measured in [docs/measurements.md](docs/measurements.md).

Two limits stated plainly in the code: the first guitar calibrated gets no
adjustment, which is correct because there is nothing yet to adapt between;
and measured levels are comparable only between captures made the same way,
never against a mixed and mastered recording off the internet.

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


## The workbench

A live view of a run, served locally:

```bash
./scripts/py scripts/workbench_demo.py --target <guitar-stem.wav>
```

It opens at `127.0.0.1:8765` and shows what is happening as it happens, not
after. Deliberately plain: system fonts, square corners, dense tables and
sunken progress bars, in the idiom of a utility rather than a dashboard.

- **Pipeline** — one row per stage: status, a progress bar, how long it took,
  and the detail. A failed stage turns red and says why, in the row.
- **Signal chain** — the amp's fixed path as the row of units a player thinks
  in. Guitar, stomp, mod, amp, delay, reverb, speaker; lit units show their
  model, mode and live control values, empty slots read "slot empty".
- **Convergence** — distance, whether the last iteration got closer, and the
  trend.
- **Band balance** — each band's gap as a number, a state, and a deviation
  bar, next to the knob move it implies.
- **Spectrum** — the target with the amp's own laid over it, so convergence
  is visible rather than asserted.
- **Next move** — the change and the measurement that justifies it.

Updates arrive over server-sent events, so an iteration appears the moment it
is recorded. No framework, no CDN, no build step: a local page reading local
state, which is also why wrapping it in a desktop shell later is only a matter
of pointing one at the URL. The demo stands in for the amp by applying each
suggested knob move as a real EQ change through ffmpeg — the loop, the
measurements and the convergence are all genuine, only the amp is simulated.

Chart colours are the validated default from the data-viz method, run through
its own checker rather than eyeballed.

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

So `mapping.py` aims for a defensible starting point, not a match, and says
how much to trust it. `choose_amp()` returns an `AmpChoice` carrying a
confidence score — how far the measurement sits from the nearest decision
boundary — the reasoning behind the pick, and, when the call is close, the amp
it would have chosen just the other side of that boundary:

```
Confidence in the amp model: 34%
  because crest 9.5 dB and harmonic ratio 0.90 read as clean; centroid
  1400 Hz and 28% midrange chose the model
  this one is borderline - also worth auditioning: DELUXE DIRT
```

`describe_settings()` prints knob positions on the amp's own 0-10 scale, so
even a wrong pick beats starting from the middle of every knob.

### Calibrating the thresholds

Arguing about whether 9.5 dB is the right clean boundary is not worth doing in
prose. Label some clips and count:

```bash
./scripts/py scripts/calibrate.py add riff.wav --label high_gain
./scripts/py scripts/calibrate.py evaluate         # score the current thresholds
./scripts/py scripts/calibrate.py sweep            # search for better ones
./scripts/py scripts/calibrate.py evaluate-models  # score the model chooser
```

`sweep` searches the threshold pair against the labelled corpus, reports
accuracy and a confusion matrix, and prints what to change — it never edits
`mapping.py`, because a threshold worth adopting is worth reading first. It
warns when a label has fewer than three clips, since a sweep over four samples
is fitting noise.

The gain class has real evidence behind it: 90% on ten clips, per the
calibration above. The choice of model *within* the class was measured the
same way — nine clips were recorded through known factory presets, so
Fender's own model pick is the ground truth per clip — and it does not hold
up: exact model 2 of 9, right gain family 8 of 9, which is about what
guessing inside the family would score. `evaluate-models` reports exact and
family accuracy with a confusion matrix, and deliberately has no sweep
counterpart: nine clips over eighteen models is one sample per rule, and
fitting per-model boundaries to that would be fitting noise. The honest
reading, recorded in `mapping.py` itself: trust the family, treat the
specific model as a suggestion to audition. Details in
[docs/measurements.md](docs/measurements.md).

The amp's own factory presets were also checked as a possible source of
per-model gain defaults and rejected: `DUBS_Plexi87` appears at gain 4.1 and
at 10.0 across three presets, so a preset's gain reflects the tone Fender
wanted there, not the model's character. The groupings survive that check —
the clean models really are used clean, and JCM800 really is used flat out —
but per-model gain defaults would have been inventing a pattern the data does
not contain.

## Development

```bash
./scripts/py -m pytest
./scripts/gen_proto.sh     # regenerate protobuf bindings
```

Use `./scripts/py` rather than `uv run python`. It sets `PYTHONPATH` to
`src/` so nothing depends on the editable install resolving — uv writes `.pth`
files with the macOS hidden flag and CPython 3.12+ silently skips those. See
[docs/troubleshooting.md](docs/troubleshooting.md).

The implementation plan is in
[docs/superpowers/plans/](docs/superpowers/plans/), what was measured on the amp
is in [docs/measurements.md](docs/measurements.md), and what is worth doing
next is in [docs/roadmap.md](docs/roadmap.md).

## Unofficial

Not affiliated with or endorsed by Fender. The protocol is undocumented by the
manufacturer and could change with any firmware update.
