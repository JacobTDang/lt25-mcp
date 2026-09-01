# Measurements taken on the amp

Numbers in the code that came from a real Mustang LT 25 rather than from
reasoning. Recorded here so they can be checked, and so nobody re-derives them
from first principles.

## Playing variance (2026-08-31)

**Question.** The convergence loop assumes the only thing changing between
iterations is the amp. The LT25's USB output carries no dry signal and accepts
no playback, so every take is played by hand. How much of a measured difference
is the playing?

**Method.** Guitar straight into the amp, auditioning the linear reference
preset (`DUBS_LinearGain`, all controls at 5). 75 seconds of continuous
playing captured in one pass, split into equal windows, every window compared
against every other. Nothing was changed between windows - not the amp, not the
guitar, not the preset.

**Result.**

| window | pairs | median distance | worst |
|---|---|---|---|
| 10s | 21 | 0.182 | 0.283 |
| 20s | 3 | 0.217 | 0.242 |
| 30s | 1 | 0.079 | 0.079 |

For scale: the convergence threshold is 0.08 and a suggested knob move shifts
distance by roughly 0.1 to 0.3. **At 20s the noise is as large as the signal.**

**Per-measurement stability**, across three 20s windows:

| measurement | spread | usable |
|---|---|---|
| crest factor | 5.9% | yes |
| harmonic ratio | 9.1% | yes |
| spectral centroid | 16.3% | marginal |
| mid band ratio | 30.3% | no |
| high band ratio | 55.0% | no |
| low band ratio | 99.4% | no |

The band ratios - which the distance metric weighted most heavily - swing with
note choice. Playing higher up the neck empties the low band, which is why it
is the worst of the three. Crest factor barely moves, so the saturation axis is
sound while the EQ axis is not.

**What changed as a result.**

- Weights moved off the band ratios towards the stable measurements.
- Deadbands are now per band and set above the measured swing (low 0.08, mid
  0.10, high 0.07) rather than a flat 0.03, which was well below the noise.
- `Comparison.significant` reports whether a gap exceeds the noise floor at
  all, and `describe()` says so.
- Takes shorter than `MIN_TAKE_SECONDS` (30s) are refused, because a 20s take
  produced five spurious knob moves in this data and a 30s take produced none.

**Re-scored with the corrected metric**, on the same recording:

| window | worst distance | spurious moves | flagged significant |
|---|---|---|---|
| 20s | 0.211 | 5 | 3 of 3 |
| 30s | 0.065 | 0 | 0 of 1 |

**Caveat.** This was free playing split into windows, not a fixed phrase
repeated deliberately, so it is an upper bound. A disciplined repeat of the
same phrase would score better. It is the right bound to design against,
because it is what an unsupervised loop actually gets.

## Guitar profile (2026-08-31)

Sterling by Music Man SUB, straight into the amp, through the reference preset:

```
output    -18.3 dBFS
centroid   1419 Hz
dynamics   18.2 dB crest
```

Pickup type still undeclared - the headstock identifies the series but not
whether it is the HSS Silo3 or the HH AX3. The measured profile supersedes the
category, so this does not need resolving for adaptation to work.

## Gain classification (2026-08-31)

**Question.** The clean / crunch / high-gain rule keyed on crest factor, set
from synthesized signals. Does it work on real amp output?

**Method.** Ten clips: nine recorded through the amp's own factory presets,
where Fender's choice of amp model and gain is the label, plus one guitar stem
from a target clip. Same guitar, room and player throughout, so the only thing
varying is the tone.

**Result: crest factor does not work.** 40% accuracy, and high gain was never
once predicted:

| label | crest factor (dB) |
|---|---|
| clean | 12.6, 19.5, 14.1, 14.5 |
| crunch | 10.8, 14.2, 14.5 |
| high gain | 12.5, 13.3, 12.2 |

The ranges overlap almost entirely. Over a whole take, crest factor measures
the *performance's* dynamic range - the gaps between notes - far more than the
saturation. A sweep of every threshold pair reached 50% at best, against 40%
for always guessing "clean".

**Spectral flatness does work.** Distortion generates harmonics and
intermodulation noise, which flattens the spectrum away from the few strong
peaks of a clean tone:

| label | spectral flatness |
|---|---|
| clean | 0.00001, 0.00083, 0.00086, 0.00113 |
| crunch | 0.00089, 0.00130, 0.00233 |
| high gain | 0.00285, 0.00535, 0.00621 |

Boundaries at **0.00088** and **0.00259** score **90%** - perfect on crunch and
high gain, with one clean preset (COUNTRY PICKING, a Deluxe Clean with a
compressor and tape delay) landing in crunch at 0.00113, between two genuine
crunch samples. A sweep over 7,281 pairs found nothing better.

Two other features separate clean from high gain but not crunch from either:
spectral rolloff (clean 2677-3142 Hz, high gain 4376-5021 Hz) and the harmonic
ratio (clean 0.888-0.968, high gain 0.829-0.877). Flatness was chosen because
it separates all three.

**Caveats.** Ten clips, one guitar, one room, one player. The thresholds are
the best fit to that evidence, not a law. Flatness is computed over the whole
spectrum, so it moves with the sample rate - the stability harness excludes
resampled variants from agreement for that reason, and the calibration was
done at 44.1 kHz.

## Reverb detection (2026-08-31)

**Question.** `choose_reverb` read a reverb size off `decay_time_s` - the time
for the signal to fall 30 dB from its peak - with thresholds set by reasoning,
never against a recording whose reverb was known. Does any of it survive
contact with real audio?

**Method.** The nine corpus clips, each recorded through a factory preset
whose reverb unit is known from the slot backups: five with reverb (a spring,
a plate, three small rooms), four with none. Scored on presence versus
absence only - three sizes over five reverb clips is not enough to grade the
size choice.

**Result.**

| clip | true reverb | measured decay | old rule said |
|---|---|---|---|
| slot01 | Spring65 | 0.52 s | **strip the reverb** (wrong) |
| slot17 | SmallRoom | 1.95 s | LargeHall (present, oversized) |
| slot03 | SmallRoom | 3.52 s | LargeHall (present, oversized) |
| slot26 | SmallRoom | 17.76 s (saturated) | inconclusive |
| slot04 | LargePlate | 17.75 s (saturated) | inconclusive |
| slot06, 11, 14, 22 | none | 17.6-17.8 s (saturated) | inconclusive |

Three findings, each about what the measurement *cannot* do:

- **Absence is unmeasurable.** Every no-reverb clip saturated - continuous
  playing never falls 30 dB below its own peak - so a dry take looks exactly
  like an inconclusive one. The "dry, therefore strip the reverb" branch had
  no way to ever fire correctly on this material.
- **A short decay does not mean dry.** The only clip that measured under the
  0.6 s "dry" bound was recorded through a spring reverb: a subtle tail sits
  inside the 30 dB window of the note's own release, so the branch's one real
  firing would have stripped a reverb the recording audibly has.
- **Size cannot be read from the number.** Measured decay adds note sustain
  and the phrase's fade to the reverb tail. Both small rooms that measured
  conclusively landed past the old 1.6 s hall boundary and were called halls.

What survives: when a tail did measure inside the plausible window, reverb
really was present, both times.

**What changed as a result.**

- `choose_reverb` no longer returns PASSTHRU. It cannot strip a base preset's
  reverb, only add one or leave it alone; a short decay is treated as no
  information rather than as dryness (`DRY_DECAY_S` became `NOTE_DECAY_S`).
- The size split is gone. A conclusive tail claims only "some reverb is
  present" and gets the modest small room; `ROOM_DECAY_S` and the hall branch
  were removed rather than refitted to two samples.
- The corpus records each clip's true reverb (`Sample.reverb`, filled from
  the slot backups; `build_corpus.py` records it at capture), and
  `calibrate.py reverb` re-scores presence against it as the corpus grows.

On this corpus the revised rule abstains on 7 of 9 and is right on presence
2/2 where it answers at all. The honest summary: reverb mostly cannot be
recovered from a continuous take, and the pipeline's job is to inherit the
base preset's reverb unless a clip conclusively rings out.

**Caveats.** Nine clips, one player, one phrase per label, takes of ~18 s.
The two conclusive detections are both small rooms, so the "modest room"
default has never been tested against a true hall; and a target clip with a
long washy reverb under continuous playing will still read inconclusive, not
"reverb present".
