"""The signal chain, as something to look at.

The amp runs a fixed path - guitar, stomp, modulation, amp, delay, reverb,
speaker - and each slot holds one modelled unit. Describing that as text loses
the thing a player actually thinks in, which is a row of boxes with knobs on
them, some lit and some not.

This turns a preset into that row. Knob values come back normalised 0-1 so the
page can draw a pointer without knowing anything about the amp.
"""

from __future__ import annotations

from typing import Any

from lt25_mcp.dsp_catalog import EFFECT_NODES, NODE_ORDER, PASSTHRU, effect_label
from lt25_mcp.parameters import PANEL_ORDER, spec_for
from lt25_mcp.preset import Preset

# Parameters worth drawing as a knob, per slot kind. Anything else is detail
# the picture does not need.
DRAWN = ("level", "gain", "tone", "sustain", "depth", "rate", "decay", "dwell",
         "diffuse", "feedback", "time", "dlyTime", "wetLvl", "blend", "mix",
         "threshold", "attenuate", "outputLevel", "high", "mid", "low")

MAX_KNOBS = 3


# Knob faces are narrow; these are the names that do not fit.
SHORT_NAMES = {
    "feedback": "fdbk", "wetLvl": "wet", "outputLevel": "out",
    "dlyTime": "time", "diffuse": "diff", "threshold": "thrsh",
    "attenuate": "atten", "sustain": "sustn",
}


def _knobs_for(params: dict[str, Any]) -> list[dict[str, Any]]:
    knobs = []
    for name in DRAWN:
        if name not in params:
            continue
        value = params[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        knobs.append({
            "name": SHORT_NAMES.get(name, name),
            "full_name": name,
            "value": max(0.0, min(1.0, float(value))),
        })
        if len(knobs) == MAX_KNOBS:
            break
    return knobs


# Units whose only real control is a named mode rather than a knob - the
# compressor is just `type: medium`. Draw the mode as a caption instead of an
# empty face.
SKIP_SETTINGS = {"bypassType", "gateDetectorPosition", "sag", "cabsimType"}

# Values that mean "nothing is set here" and are noise on a pedal face.
NULL_SETTINGS = {"off", "none", ""}


def _setting_for(params: dict[str, Any]) -> str:
    """A named mode worth printing under the unit's name.

    `noteDivision` is the tempo-sync division on delays and modulation. It is
    worth showing when it is set to a division, and pure clutter when it reads
    'off', which is most of the time.
    """
    for name, value in params.items():
        if name in SKIP_SETTINGS or not isinstance(value, str):
            continue
        if value.strip().lower() in NULL_SETTINGS:
            continue
        return value
    return ""


def describe_chain(preset: Preset) -> list[dict[str, Any]]:
    """One entry per node in signal-path order."""
    chain: list[dict[str, Any]] = []
    for node in NODE_ORDER:
        try:
            unit = preset.unit(node)
            params = preset.params(node)
        except Exception:
            continue

        if node == "amp":
            knobs = [
                {"name": name, "full_name": name,
                 "value": max(0.0, min(1.0, float(params[name])))}
                for name in PANEL_ORDER
                if name in params
                and (spec := spec_for(name)) is not None
                and spec.scale == "normalized"
                and isinstance(params[name], (int, float))
                and not isinstance(params[name], bool)
            ][:4]
            chain.append({
                "node": "amp",
                "kind": "amp",
                "label": preset.amp_label,
                "unit": unit,
                "occupied": True,
                "knobs": knobs,
                "setting": str(params.get("cabsimType", "")),
            })
            continue

        occupied = unit != PASSTHRU
        chain.append({
            "node": node,
            "kind": "pedal",
            "label": effect_label(node, unit) if occupied else "empty",
            "unit": unit,
            "occupied": occupied,
            "knobs": _knobs_for(params) if occupied else [],
            "setting": _setting_for(params) if occupied else "",
        })
    return chain


assert set(EFFECT_NODES) <= set(NODE_ORDER)
