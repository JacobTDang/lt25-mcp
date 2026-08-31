"""MCP server exposing the amp as a set of tools.

Tools open a session, act, and close it, so a failed call cannot leave the amp
holding a half-open session.

Auditioning is the exception. The amp drops a client that stops sending
heartbeats, so an audition lasts only as long as its session; `audition_preset`
therefore holds one open until `stop_audition` or a replacing audition. While
a session is held, every other tool reuses it rather than opening a second,
because only one program can hold the amp's control channel at a time.

Tools return plain dictionaries, never protobuf objects.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer

from lt25_mcp.commands import audition, exit_audition, is_auditioning, write_preset
from lt25_mcp.dsp_catalog import AMP_MODELS, EFFECTS, effect_label
from lt25_mcp.library import SLOT_MAX, SLOT_MIN, WRITABLE_MIN, backup_all, latest_backup, read_preset
from lt25_mcp.parameters import describe_parameters
from lt25_mcp.preset import Preset, PresetError
from lt25_mcp.guitar import (
    CALIBRATION_INSTRUCTIONS,
    REFERENCE_KNOBS,
    REFERENCE_PRESET_AMP,
    GuitarError,
    GuitarLibrary,
    profile_from_capture,
)
from lt25_mcp.rig import PICKUP_RATIONALE, PICKUP_TYPES, Rig, RigError, adapt_knobs, slots_to_leave_empty
from lt25_mcp.session import Session
from lt25_mcp.analysis.mapping import MappingError, describe_settings
from lt25_mcp.transport import open_transport
from lt25_mcp.tuning import STRUCTURAL_ADVICE, catalogue, remedy_for

BACKUP_ROOT = Path(__file__).resolve().parents[2] / "backups"


def _analysis_errors():
    """Errors the analysis pipeline raises, surfaced to callers as ValueError."""
    from lt25_mcp.analysis import cli as analysis_cli
    from lt25_mcp.analysis.acquire import AcquisitionError
    from lt25_mcp.analysis.features import FeatureError
    from lt25_mcp.analysis.plots import PlotError
    from lt25_mcp.analysis.stems import StemError

    return (
        analysis_cli.PipelineError, AcquisitionError, StemError,
        FeatureError, PlotError, MappingError, PresetError,
    )


_ANALYSIS_ERRORS = _analysis_errors()

INSTRUCTIONS = f"""\
Controls a Fender Mustang LT25 guitar amplifier over USB, so you can build and
tune guitar tones by ear with the player.

## The loop

You cannot hear the amp. The player can. So tuning is a conversation, not a
calculation: make one change, let them hear it, ask what is still wrong.

1. Start from a real preset - `get_preset` on a slot, or a preset produced by
   the analysis pipeline. Never invent preset JSON from scratch; parameter sets
   differ per amp model and an invented one will be rejected.
2. `describe_preset` to see what this model actually exposes and what each
   control does.
3. `audition_preset` to play it through the speaker. Nothing is saved.
4. Ask the player what is wrong, in their words - "too fizzy", "too thin",
   "not heavy enough".
5. `tuning_guide` maps those words to specific moves. Apply ONE with
   `tune_preset`, then audition again.
6. Repeat. When they are happy, `save_preset` to a slot in
   {WRITABLE_MIN}-{SLOT_MAX}.

## Scales - the easiest thing to get wrong

Presets store tone controls as 0.0-1.0. The amp's screen and its physical
knobs read 0-10. `tune_preset` and `describe_preset` both speak the 0-10
scale, so use them rather than editing raw JSON. `volume` is an exception: it
is decibels and always negative.

## What moves a tone most, in order

1. The amp model - character. Do not fight a wrong model with EQ.
2. The cabinet (`cabsimType`) - moves a tone further than any tone control.
3. Gain - clean versus saturated, and how much dynamic range survives.
4. Mid - what makes a tone cut through or disappear in a band.
5. Treble and bass - the fine adjustment, not the coarse one.

## The player's rig

A preset is not a tone on its own: the same settings sound different through
different pickups, and loading an overdrive into the stomp slot is wrong if
the player already has a real one in front of the amp. Call `get_rig` at the
start of a session. If nothing has been declared, ask - it takes one question
and it changes the answers. `set_rig` records it.

Pickup type is only a coarse prior. `calibrate_guitar` measures the actual
instrument through the amp's USB audio input and stores a profile; presets
then adapt by the measured difference between guitars rather than by category.
Offer it when the player has more than one guitar, or when a preset built for
one instrument sounds wrong through another. `tune_preset(apply_rig=True)`
uses a measured profile when there is one and falls back to the prior
otherwise, and always says which.

## Working from a recording

`analyse_clip` takes a URL or a local file and returns a preset, the
measurements behind it, and how much to trust the amp-model choice. It touches
no hardware, so the result still has to be auditioned.

Closing the gap by ear is the loop above. Closing it by measurement is
`record_take` then `compare_to_target`, which says which knob to move and by
how much. Be careful with that number: every take is played by hand, so part
of any difference is the playing rather than the amp. A small change between
takes is noise, not progress.

## Rules

- Only slots {WRITABLE_MIN}-{SLOT_MAX} are writable. Slots {SLOT_MIN}-30 are
  the factory presets and are refused.
- A complete 60-slot backup must exist before any write; run `backup_presets`
  once if `amp_status` reports none.
- Prefer auditioning over saving. An audition stores nothing, so iterate there
  and only save once the player says it is right.
- Quit Fender Tone LT Desktop first - only one program can hold the amp's
  control channel.
"""

server = MCPServer(name="lt25", instructions=INSTRUCTIONS)


def _session() -> Session:
    return Session(open_transport())


# An audition only lasts as long as its session: the amp drops a client that
# stops sending heartbeats. So auditioning is the one operation that holds a
# session open between tool calls, until stop_audition or a replacing audition.
# Only one program can hold the amp's control channel, so while a session is
# held every other tool reuses it rather than opening a second.
_held: Session | None = None
_held_lock = threading.Lock()
"""Guards _held: tool calls can arrive concurrently, and the amp accepts one."""


def _release_held() -> None:
    """Close and clear the held session, if any. Safe to call concurrently."""
    global _held
    with _held_lock:
        session, _held = _held, None
    if session is not None:
        session.close()


@contextmanager
def _amp():
    """A session for one tool call, reusing a held audition session if there is one."""
    with _held_lock:
        held = _held
    if held is not None:
        yield held
        return
    with _session() as session:
        yield session


def _describe(preset: Preset, slot: int) -> dict[str, Any]:
    return {
        "slot": slot,
        "name": preset.display_name,
        "amp_model": preset.amp_model,
        "amp_label": preset.amp_label,
        "amp_parameters": preset.params("amp"),
        "effects": {
            node: effect_label(node, preset.unit(node))
            for node in EFFECTS
            if preset.has_effect(node)
        },
        "empty": preset.display_name == "EMPTY",
    }


@server.tool(description="List all 60 preset slots with their names and amp models.")
def list_presets() -> list[dict[str, Any]]:
    with _amp() as session:
        return [
            _describe(Preset.from_dict(read_preset(session, slot)), slot)
            for slot in range(SLOT_MIN, SLOT_MAX + 1)
        ]


@server.tool(description="Read one preset slot in full, including every parameter.")
def get_preset(slot: int) -> dict[str, Any]:
    with _amp() as session:
        raw = read_preset(session, slot)
    preset = Preset.from_dict(raw)
    return {**_describe(preset, slot), "summary": preset.summary(), "raw": raw}


@server.tool(description="Back up all 60 slots to disk. Required before any write.")
def backup_presets() -> dict[str, Any]:
    with _amp() as session:
        out = backup_all(session, BACKUP_ROOT)
    return {"backup": str(out), "slots": SLOT_MAX}


@server.tool(
    description=(
        "Play a preset through the amp without saving it. Takes preset JSON as "
        "returned by get_preset's 'raw' field, optionally modified."
    )
)
def audition_preset(preset_json: str) -> dict[str, Any]:
    global _held
    preset = Preset.from_dict(json.loads(preset_json))
    _release_held()
    session = _session()
    session.open()
    try:
        audition(session, preset)
    except Exception:
        session.close()
        raise
    with _held_lock:
        previous, _held = _held, session
    if previous is not None:
        # Another call raced us and parked a session; do not leak it.
        previous.close()
    return {"auditioning": True, "summary": preset.summary()}


@server.tool(description="Stop auditioning and return the amp to its loaded preset.")
def stop_audition() -> dict[str, Any]:
    with _held_lock:
        held = _held
    if held is not None:
        exit_audition(held)
        _release_held()
        return {"auditioning": False}
    with _amp() as session:
        exit_audition(session)
        return {"auditioning": is_auditioning(session)}


@server.tool(
    description=(
        f"Save a preset to a slot. Only slots {WRITABLE_MIN}-{SLOT_MAX} are "
        "writable, and a complete backup must exist first."
    )
)
def save_preset(preset_json: str, slot: int) -> dict[str, Any]:
    preset = Preset.from_dict(json.loads(preset_json))
    with _amp() as session:
        write_preset(session, preset, slot, backup_root=BACKUP_ROOT)
        return {"written": True, "slot": slot, "name": preset.display_name}


@server.tool(description="List the amp models available, by FenderId and panel label.")
def list_amp_models() -> dict[str, str]:
    return dict(AMP_MODELS)


@server.tool(description="List the effects available in each of the four effect slots.")
def list_effects() -> dict[str, dict[str, str]]:
    return {node: dict(units) for node, units in EFFECTS.items()}


@server.tool(description="Report amp connection status, firmware, and backup state.")
def amp_status() -> dict[str, Any]:
    backup = latest_backup(BACKUP_ROOT)
    with _amp() as session:
        return {
            "connected": True,
            "product_id": session.product_id(),
            "firmware_version": session.firmware_version(),
            "auditioning": is_auditioning(session),
            "latest_backup": str(backup) if backup else None,
            "writes_permitted": backup is not None,
        }


@server.tool(
    description=(
        "Explain a preset: every control this amp model exposes, what it does, "
        "its value on the amp's own 0-10 scale, and what values it accepts. "
        "Call this before tuning so you know what can actually be changed."
    )
)
def describe_preset(preset_json: str) -> dict[str, Any]:
    preset = Preset.from_dict(json.loads(preset_json))
    return {
        "name": preset.display_name,
        "amp_model": preset.amp_model,
        "amp_label": preset.amp_label,
        "knobs_on_amp_scale": preset.knobs(),
        "parameters": describe_parameters(preset.params("amp")),
        "effects": {
            node: {
                "unit": preset.unit(node),
                "label": effect_label(node, preset.unit(node)),
                "occupied": preset.has_effect(node),
            }
            for node in EFFECTS
        },
        "signal_path": "guitar -> stomp -> mod -> amp -> delay -> reverb -> speaker",
    }


@server.tool(
    description=(
        "Change a preset and get the modified JSON back. Knob values use the "
        "amp's 0-10 scale. Nothing is sent to the amp: audition or save the "
        "result afterwards. Example: knobs={'gain': 3.5, 'treb': 7.0}."
    )
)
def tune_preset(
    preset_json: str,
    knobs: dict[str, float] | None = None,
    amp_model: str | None = None,
    effects: dict[str, str] | None = None,
    name: str | None = None,
    apply_rig: bool = False,
) -> dict[str, Any]:
    preset = Preset.from_dict(json.loads(preset_json)).clone()
    changes: list[str] = []
    try:
        if amp_model is not None:
            preset.amp_model = amp_model
            changes.append(f"amp model -> {preset.amp_label}")
        for node, unit in (effects or {}).items():
            preset.set_effect(node, unit)
            changes.append(f"{node} -> {effect_label(node, unit)}")
        for knob, value in (knobs or {}).items():
            preset.set_knob(knob, value)
            changes.append(f"{knob} -> {value:.1f}/10")
        if name is not None:
            preset.display_name = name
            changes.append(f"name -> {name}")
        if apply_rig:
            adjusted, why, method = adapt_knobs(preset.knobs(), Rig.load())
            for knob, value in adjusted.items():
                preset.set_knob(knob, value)
            changes.extend(why)
            if method == "pickup_prior":
                changes.append(
                    "(adjusted from pickup type only - calibrate_guitar gives a "
                    "measured profile instead)"
                )
    except PresetError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "preset_json": json.dumps(preset.to_dict()),
        "changes": changes,
        "knobs_on_amp_scale": preset.knobs(),
        "summary": preset.summary(),
    }


@server.tool(
    description=(
        "What to change when the player describes what is wrong. Pass their "
        "words as `complaint` (e.g. 'too fizzy', 'not heavy enough') for a "
        "targeted answer, or omit it for the whole catalogue."
    )
)
def tuning_guide(complaint: str | None = None) -> dict[str, Any]:
    if complaint:
        remedy = remedy_for(complaint)
        if remedy is None:
            return {
                "matched": None,
                "complaint": complaint,
                "note": "No direct match. Consult the full catalogue.",
                "catalogue": catalogue(),
                "structural_advice": list(STRUCTURAL_ADVICE),
            }
        return {
            "matched": remedy.complaint,
            "means": remedy.means,
            "moves": [
                {"control": m.control, "delta_on_0_to_10_scale": m.delta, "why": m.why}
                for m in remedy.moves
            ],
            "note": "Apply one change, audition, then ask again.",
            "structural_advice": list(STRUCTURAL_ADVICE),
        }
    return {"catalogue": catalogue(), "structural_advice": list(STRUCTURAL_ADVICE)}


@server.tool(
    description=(
        "What guitar and pedals the player is using. Call this before tuning: "
        "pickup type shifts appropriate gain by a notch or two, and a real "
        "pedal in front means the matching amp slot should stay empty."
    )
)
def get_rig() -> dict[str, Any]:
    rig = Rig.load()
    return {
        **rig.to_dict(),
        "declared": rig.pickups != "unknown" or bool(rig.pedals),
        "summary": rig.describe(),
        "pickup_effect": PICKUP_RATIONALE[rig.pickups],
        "slots_to_leave_empty": slots_to_leave_empty(rig),
        "pickup_types": list(PICKUP_TYPES),
    }


@server.tool(
    description=(
        "List calibrated guitars. Each is measured through the same reference "
        "preset, and presets adapt by the difference between whichever guitar "
        "is plugged in and the reference one."
    )
)
def list_guitars() -> dict[str, Any]:
    library = GuitarLibrary.load()
    rig = Rig.load()
    return {
        "guitars": [p.to_dict() for p in library.guitars.values()],
        "reference": library.reference.name if library.reference else None,
        "playing": rig.playing or None,
        "how_to_add": (
            "Load a preset using amp model "
            f"{REFERENCE_PRESET_AMP} with knobs {REFERENCE_KNOBS}, audition it, "
            "then call calibrate_guitar while the player plays."
        ),
    }


@server.tool(
    description=(
        "Measure the guitar currently plugged in and store it as a profile. "
        "Records from the amp's USB audio output while the player plays, so "
        "tell them what to play and wait for them to be ready first. The first "
        "guitar calibrated becomes the reference."
    )
)
def calibrate_guitar(name: str, seconds: float = 20.0, pickups: str = "unknown") -> dict[str, Any]:
    import tempfile

    from lt25_mcp.analysis.capture import CaptureError, record

    try:
        capture = record(
            Path(tempfile.mkdtemp(prefix="lt25-calib-")) / f"{name}.wav", seconds
        )
        profile = profile_from_capture(name, capture, pickups=pickups)
    except (CaptureError, GuitarError) as exc:
        raise ValueError(str(exc)) from exc

    library = GuitarLibrary.load()
    library.add(profile)
    library.save()
    rig = Rig.load()
    rig.playing = name
    rig.save()
    return {
        "profile": profile.to_dict(),
        "summary": profile.describe(),
        "is_reference": profile.is_reference,
        "now_playing": name,
        "instructions_used": CALIBRATION_INSTRUCTIONS,
    }


@server.tool(
    description=(
        "Say which calibrated guitar is now plugged in, so presets adapt to it."
    )
)
def select_guitar(name: str) -> dict[str, Any]:
    library = GuitarLibrary.load()
    if name not in library.guitars:
        raise ValueError(
            f"no profile named {name!r}; calibrated guitars: "
            f"{sorted(library.guitars) or 'none'}"
        )
    rig = Rig.load()
    rig.playing = name
    rig.save()
    return {"playing": name, "summary": library.guitars[name].describe()}


@server.tool(
    description=(
        "Record the player's guitar and pedals. pickups is one of: unknown, "
        "single_coil, humbucker, p90, active. pedals lists what is in front of "
        "the amp: overdrive, distortion, fuzz, compressor, modulation, delay, "
        "reverb."
    )
)
def set_rig(
    pickups: str = "unknown",
    guitar: str = "",
    pedals: list[str] | None = None,
    notes: str = "",
    playing: str = "",
) -> dict[str, Any]:
    try:
        rig = Rig(pickups=pickups, guitar=guitar, pedals=pedals or [],
                  notes=notes, playing=playing)
    except RigError as exc:
        raise ValueError(str(exc)) from exc
    path = rig.save()
    return {"saved_to": str(path), "summary": rig.describe(),
            "pickup_effect": PICKUP_RATIONALE[rig.pickups]}


@server.tool(
    description=(
        "Turn a clip into a preset: download or read audio, isolate the guitar, "
        "measure it, and build a preset from a base one. Pass a URL or a local "
        "audio path, not both. Touches no hardware - audition or save the "
        "result afterwards."
    )
)
def analyse_clip(
    base_preset_json: str,
    url: str | None = None,
    audio_path: str | None = None,
    start: float | None = None,
    end: float | None = None,
    name: str | None = None,
    separate: bool = True,
) -> dict[str, Any]:
    import tempfile

    from lt25_mcp.analysis import cli as analysis_cli

    try:
        result = analysis_cli.analyse(
            url=url,
            audio=Path(audio_path) if audio_path else None,
            start=start,
            end=end,
            base=Preset.from_dict(json.loads(base_preset_json)),
            work_dir=Path(tempfile.mkdtemp(prefix="lt25-analyse-")),
            name=name,
            separate=separate,
        )
    except _ANALYSIS_ERRORS as exc:
        raise ValueError(str(exc)) from exc

    return {
        "preset_json": json.dumps(result.preset.to_dict()),
        "summary": describe_settings(result.preset, choice=result.choice),
        "amp_model": result.choice.amp_model,
        "confidence": result.choice.confidence,
        "reason": result.choice.reason,
        "alternatives": result.choice.alternatives,
        "measurements": result.features.to_dict(),
        "guitar_stem": str(result.stem) if result.stem else None,
        "spectrogram": str(result.spectrogram) if result.spectrogram else None,
        "note": (
            "Confidence reflects distance from the nearest gain boundary. Below "
            "60% the alternatives are worth auditioning too."
        ),
    }


@server.tool(
    description=(
        "Measure one audio file: brightness, band balance, saturation, key and "
        "tuning offset. Use it to inspect a captured take or a target clip."
    )
)
def measure_audio(audio_path: str) -> dict[str, Any]:
    from lt25_mcp.analysis.features import extract

    try:
        features = extract(Path(audio_path))
    except _ANALYSIS_ERRORS as exc:
        raise ValueError(str(exc)) from exc
    return {"measurements": features.to_dict(), "summary": features.describe()}


@server.tool(
    description=(
        "Compare what the amp is producing against a target, and say which knob "
        "to move next. Both paths should be audio of guitar alone - a target "
        "clip's guitar stem, and a recording of the player through the amp."
    )
)
def compare_to_target(target_path: str, current_path: str) -> dict[str, Any]:
    from lt25_mcp.analysis.converge import compare
    from lt25_mcp.analysis.features import extract

    try:
        target = extract(Path(target_path))
        current = extract(Path(current_path))
    except _ANALYSIS_ERRORS as exc:
        raise ValueError(str(exc)) from exc

    result = compare(target, current)
    return {
        "distance": result.distance,
        "converged": result.converged,
        "band_gaps": result.band_gaps,
        "centroid_octaves": result.centroid_octaves,
        "moves": [
            {"control": m.control, "delta_on_0_to_10_scale": m.delta, "why": m.why}
            for m in result.moves
        ],
        "summary": result.describe(),
        "caveat": (
            "Every take is played by hand, so some of this difference is the "
            "playing rather than the amp. Treat a small distance change as noise."
        ),
    }


@server.tool(
    description=(
        "Record the player through the amp's USB audio output. Tell them what "
        "to play and wait until they are ready before calling this. Takes "
        "shorter than 30s are refused: two takes of the same playing differ "
        "by more than a knob move at 20s."
    )
)
def record_take(seconds: float = 30.0) -> dict[str, Any]:
    import tempfile

    from lt25_mcp.analysis.capture import CaptureError, record

    dest = Path(tempfile.mkdtemp(prefix="lt25-take-")) / "take.wav"
    try:
        path = record(dest, seconds)
    except CaptureError as exc:
        raise ValueError(str(exc)) from exc
    return {"audio_path": str(path), "seconds": seconds}


@server.prompt(
    name="match_tone",
    description="Guided workflow for matching a guitar tone by ear with the player.",
)
def match_tone(target: str = "the tone you have in mind") -> str:
    return f"""\
Help me dial in {target} on my Mustang LT25.

Work this way:

1. Check `amp_status`. If no backup exists, run `backup_presets` first.
   Check `get_rig` too - if my pickups are undeclared, ask me before guessing.
2. Pick a starting preset with `get_preset` - something already in the right
   family (clean, crunch or high gain) rather than an empty slot.
3. Run `describe_preset` so you know which controls this model has.
4. Make your best opening guess with `tune_preset`, and say briefly why you
   chose that amp model and those settings.
5. `audition_preset` it, then ask me what is wrong. I will answer in plain
   words like "too dark" or "not enough bite".
6. Look my words up with `tuning_guide`, apply ONE change, and audition again.
   Tell me what you changed and what to listen for.
7. Repeat until I say it is right, then `save_preset` to a free slot in
   {WRITABLE_MIN}-{SLOT_MAX} and confirm the slot number.

You cannot hear the amp, so never claim a change sounds better - ask me.
"""


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
