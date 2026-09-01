# Where this stands, and what is worth doing next

Written after the first round of real calibration, when the pipeline finally
had enough measured evidence to say what it can and cannot do.

## What works, honestly

| capability | state | evidence |
|---|---|---|
| Read, back up, audition, write presets | works | verified on the amp, firmware 2.1.4 |
| Guarded writes to slots 31-60 | works | read-back compares model and every parameter |
| Gain class from audio | **90%** | 10 labelled clips, `docs/measurements.md` |
| Amp model within that class | **22%** | no better than chance |
| Reverb from audio | abstains | absence and size are not measurable |
| EQ convergence from playing | unreliable | noise floor 0.24 vs moves of 0.1-0.3 |
| Saturation convergence | sound | crest factor moved 5.9% across takes |
| Per-guitar adaptation | built | one guitar calibrated |
| Interactive tuning by ear | built | **never used by a human** |

The pattern is clear enough to plan around: **the amp half is solid, the
coarse tonal judgement is solid, and everything fine-grained is not.**

## 1. The experiment that would move the needle

Amp model choice sits at chance, and the reason is known rather than guessed:
a factory preset buries the model's voicing under its own tone settings. The
Plexi preset ships `mid: -4.4` with treble at full, so a recording through it
measures the *preset*, not the amp.

The fix is a better experiment, not better rules. Record every amp model
through a **neutralised** preset - same gain, same tone controls at 5, no
effects - so the only thing varying between takes is the model itself. Then
model choice becomes nearest-neighbour against measured signatures rather than
rules written from reputation.

Roughly nineteen takes. Worth doing in one sitting.

**This may fail, and would fail informatively.** Band ratios swing up to 99%
with note choice, so a signature built from one take each may not be stable
enough to match against. Two mitigations, in order: play the same short phrase
for every model, and build the signature from the measurements that survived
the variance test - spectral flatness (5.9%) and centroid (16.3%) - rather than
the band ratios that did not. If signatures still overlap, the honest answer is
that model choice is not recoverable from audio, and the tool should offer the
family and let the player pick within it.

## 2. Use it for real

Every part of the by-ear loop exists and none of it has been driven by a
person: `describe_preset`, `tuning_guide`, `tune_preset`, `audition_preset`,
the `match_tone` prompt. One session chasing an actual tone would find more
than another round of measurement.

Specifically worth watching for: whether `tuning_guide`'s complaint vocabulary
matches the words a player actually uses, whether one change per iteration is
the right pace, and whether the amp-model suggestion gets in the way given it
is only 22% right.

## 3. Cheap and worthwhile

- **A second guitar.** Adaptation is built and exercised by tests, but the
  delta between two real instruments has never been measured. Calibrating one
  more guitar is a few minutes and validates the whole mechanism.
- **More corpus clips.** Ten is thin. The thresholds are the best fit to that
  evidence, not a law. `calibrate.py sweep` re-runs whenever it grows.
- **Identify the guitar properly.** A Sterling SUB, but Silo3 (HSS) or AX3
  (HH) is still unknown. The measured profile supersedes the category, so this
  is tidiness rather than need.

## 4. Deferred on purpose

- **An Electron shell.** The workbench is a local page with no build step; a
  desktop shell is a `BrowserWindow` pointed at it. Worth doing when the thing
  it displays is worth looking at daily, not before.
- **Tempo-synced delay.** `noteDivision` exists on delay and modulation units
  and nothing sets it. Real, small, no evidence anyone wants it yet.

## What is not worth doing

- **Tuning thresholds harder.** Ten clips, one guitar, one room. Every number
  in `mapping.py` is already the best fit to that evidence; moving them without
  new clips is fitting noise.
- **Making the convergence loop fully automatic.** The measurement says the
  playing moves the EQ bands more than the knobs do, and the amp offers no dry
  signal and no playback, so this cannot be engineered around. It shows the
  gap; the player decides. That is the correct shape.
- **Per-model rules from nine clips.** Eighteen models, nine samples. Fitting
  those would manufacture confidence rather than earn it.
