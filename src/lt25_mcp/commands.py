"""Actions that change what the amp is doing.

Auditioning plays a preset through the speaker without storing it anywhere;
leaving the audition returns the amp to whatever it had loaded. That is the
safe way to iterate, and `audition_scope` guarantees the exit even if the
caller raises.

Writing is deliberately awkward. It refuses any slot outside 31-60 so factory
presets cannot be lost, refuses to run at all unless a complete 60-slot backup
exists on disk, and reads the slot back afterwards to prove the write landed.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from lt25_mcp.library import SLOT_MAX, WRITABLE_MIN, SlotError, latest_backup, read_preset
from lt25_mcp.dsp_catalog import EFFECT_NODES
from lt25_mcp.preset import Preset, PresetError


# The amp stores tone controls as floats and may round them on the way in and
# out. Anything larger than this is a real disagreement, not a rounding step.
PARAM_TOLERANCE = 1e-4


class WriteRefused(SlotError):
    """Raised when a write is not safe to perform."""


def audition(session, preset: Preset) -> None:
    """Play a preset through the amp without saving it."""
    session.request(auditionPreset={"presetData": preset.to_json()})


def exit_audition(session) -> None:
    """Stop auditioning and return the amp to its loaded preset."""
    session.request(exitAuditionPreset={"exit": True})


def is_auditioning(session) -> bool:
    """Whether the amp is currently playing an auditioned preset."""
    reply = session.request(
        expect="auditionStateStatus", auditionStateRequest={"request": True}
    )
    return reply.auditionStateStatus.isAuditioning


@contextmanager
def audition_scope(session, preset: Preset):
    """Audition for the duration of the block, always exiting afterwards."""
    audition(session, preset)
    try:
        yield
    finally:
        exit_audition(session)


def write_preset(session, preset: Preset, slot: int, *, backup_root: Path) -> None:
    """Save a preset into one of the writable slots.

    Refuses unless the slot is in 31-60 and a complete backup exists. After
    writing, the slot is read back and compared; a mismatch raises rather than
    reporting success.
    """
    if not isinstance(slot, int) or isinstance(slot, bool):
        raise WriteRefused(f"slot must be an integer in {WRITABLE_MIN}..{SLOT_MAX}, got {slot!r}")
    if not WRITABLE_MIN <= slot <= SLOT_MAX:
        raise WriteRefused(
            f"refusing to write to slot {slot}: only {WRITABLE_MIN}..{SLOT_MAX} are "
            "writable, slots 1-30 hold the factory presets"
        )

    backup = latest_backup(Path(backup_root))
    if backup is None:
        raise WriteRefused(
            f"no complete backup found under {backup_root}. Run scripts/backup.py "
            "before writing to the amp."
        )

    session.request(
        savePresetAs={
            "presetData": preset.to_json(),
            "isLoadPreset": False,
            "presetSlot": slot,
        }
    )

    try:
        written = Preset.from_dict(read_preset(session, slot))
    except (PresetError, ValueError) as exc:
        # A read-back the preset model cannot parse is a failed write, not a
        # parsing problem for the caller to puzzle over.
        raise WriteRefused(
            f"slot {slot} read back as something that is not a valid preset: {exc}"
        ) from exc
    difference = first_difference(preset, written)
    if difference is not None:
        raise WriteRefused(
            f"slot {slot} did not read back as written: {difference}"
        )


def first_difference(sent: Preset, written: Preset) -> str | None:
    """The first meaningful field where a read-back disagrees with what was sent.

    Compares the name, the amp model and all its parameters, and each effect
    slot's unit - not the whole document. The amp stamps its own `timestamp`
    and related metadata on save, so a full comparison would raise on every
    successful write.
    """
    if sent.display_name != written.display_name:
        return f"name is {written.display_name!r}, expected {sent.display_name!r}"
    if sent.amp_model != written.amp_model:
        return f"amp model is {written.amp_model!r}, expected {sent.amp_model!r}"

    expected_params = sent.params("amp")
    actual_params = written.params("amp")
    for name, value in expected_params.items():
        if name not in actual_params:
            return f"amp parameter {name!r} is missing"
        actual = actual_params[name]
        if isinstance(value, float) or isinstance(actual, float):
            if abs(float(actual) - float(value)) > PARAM_TOLERANCE:
                return f"amp {name} is {actual}, expected {value}"
        elif actual != value:
            return f"amp {name} is {actual!r}, expected {value!r}"

    for node in EFFECT_NODES:
        if sent.unit(node) != written.unit(node):
            return (
                f"{node} is {written.unit(node)!r}, expected {sent.unit(node)!r}"
            )
    return None
