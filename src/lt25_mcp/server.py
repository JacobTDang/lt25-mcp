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
from pathlib import Path
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer

from lt25_mcp.commands import audition, exit_audition, is_auditioning, write_preset
from lt25_mcp.dsp_catalog import AMP_MODELS, EFFECTS, effect_label
from lt25_mcp.library import SLOT_MAX, SLOT_MIN, WRITABLE_MIN, backup_all, latest_backup, read_preset
from lt25_mcp.parameters import describe_parameters
from lt25_mcp.preset import Preset, PresetError
from lt25_mcp.rig import PICKUP_RATIONALE, PICKUP_TYPES, Rig, RigError, adjust_for_rig, slots_to_leave_empty
from lt25_mcp.session import Session
from lt25_mcp.transport import open_transport
from lt25_mcp.tuning import STRUCTURAL_ADVICE, catalogue, remedy_for

BACKUP_ROOT = Path(__file__).resolve().parents[2] / "backups"

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


def _release_held() -> None:
    global _held
    if _held is not None:
        session, _held = _held, None
        session.close()


@contextmanager
def _amp():
    """A session for one tool call, reusing a held audition session if there is one."""
    global _held
    if _held is not None:
        yield _held
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
    _held = session
    return {"auditioning": True, "summary": preset.summary()}


@server.tool(description="Stop auditioning and return the amp to its loaded preset.")
def stop_audition() -> dict[str, Any]:
    if _held is not None:
        exit_audition(_held)
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
            rig = Rig.load()
            adjusted, why = adjust_for_rig(preset.knobs(), rig)
            for knob, value in adjusted.items():
                preset.set_knob(knob, value)
            changes.extend(why)
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
) -> dict[str, Any]:
    try:
        rig = Rig(pickups=pickups, guitar=guitar, pedals=pedals or [], notes=notes)
    except RigError as exc:
        raise ValueError(str(exc)) from exc
    path = rig.save()
    return {"saved_to": str(path), "summary": rig.describe(),
            "pickup_effect": PICKUP_RATIONALE[rig.pickups]}


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
