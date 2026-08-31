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
from lt25_mcp.preset import Preset
from lt25_mcp.session import Session
from lt25_mcp.transport import open_transport

BACKUP_ROOT = Path(__file__).resolve().parents[2] / "backups"

server = MCPServer(
    name="lt25",
    instructions=(
        "Controls a Fender Mustang LT25 guitar amplifier over USB. Presets live "
        f"in slots {SLOT_MIN}-{SLOT_MAX}; only {WRITABLE_MIN}-{SLOT_MAX} are "
        "writable, because 1-30 hold the factory presets. Prefer audition_preset "
        "over write_preset while iterating: it plays a tone through the speaker "
        "without storing it. Quit Fender Tone LT Desktop before using these tools."
    ),
)


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


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
