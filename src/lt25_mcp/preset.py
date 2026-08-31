"""A preset, as the amp stores it.

Presets are JSON documents describing a fixed signal path — stomp, mod, amp,
delay, reverb — where each node names a DSP unit by FenderId and carries that
unit's own parameter set.

Parameter sets differ per model: TWIN CLEAN has `bright`, METAL LEAD has
`master`, SUPER CLEAN has neither. So presets are always cloned from real amp
data and mutated by name; nothing here synthesises a preset from scratch, and
nothing imposes a numeric range the amp itself does not use. A real factory
preset stores `mid` as -4.4 and `treb` as an integer, and both must survive a
round trip untouched.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from lt25_mcp.dsp_catalog import AMP_MODELS, EFFECTS, PASSTHRU, amp_label
from lt25_mcp.parameters import PANEL_ORDER, spec_for

DISPLAY_NAME_LENGTH = 16

_DEFAULTS_PATH = Path(__file__).parent / "data" / "dsp_defaults.json"


class PresetError(Exception):
    """Raised when a preset would be made invalid."""


@lru_cache(maxsize=1)
def _dsp_defaults() -> dict[str, dict]:
    """Representative parameter set for each DSP unit, keyed `node/FenderId`."""
    return json.loads(_DEFAULTS_PATH.read_text())


class Preset:
    def __init__(self, data: dict) -> None:
        self._data = data

    @classmethod
    def from_dict(cls, data: dict) -> Preset:
        for key in ("audioGraph", "info"):
            if key not in data:
                raise PresetError(f"preset is missing {key!r}")
        if "nodes" not in data["audioGraph"]:
            raise PresetError("preset audioGraph has no nodes")
        return cls(copy.deepcopy(data))

    def to_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def clone(self) -> Preset:
        return Preset(copy.deepcopy(self._data))

    def to_json(self) -> str:
        """Serialized exactly as the amp expects it on the wire."""
        return json.dumps(self._data, separators=(",", ":"))

    # -- name ---------------------------------------------------------------

    @property
    def display_name(self) -> str:
        return self._data["info"]["displayName"].strip()

    @display_name.setter
    def display_name(self, value: str) -> None:
        if len(value) > DISPLAY_NAME_LENGTH:
            raise PresetError(
                f"display name must be at most {DISPLAY_NAME_LENGTH} characters, "
                f"got {len(value)}"
            )
        if not value.isascii():
            raise PresetError("display name must be ASCII; the amp cannot render more")
        self._data["info"]["displayName"] = value.ljust(DISPLAY_NAME_LENGTH)

    # -- nodes --------------------------------------------------------------

    def node(self, node_id: str) -> dict:
        for node in self._data["audioGraph"]["nodes"]:
            if node["nodeId"] == node_id:
                return node
        raise PresetError(f"preset has no node {node_id!r}")

    def unit(self, node_id: str) -> str:
        """FenderId occupying a node."""
        return self.node(node_id)["FenderId"]

    def has_effect(self, node_id: str) -> bool:
        return self.unit(node_id) != PASSTHRU

    # -- amp ----------------------------------------------------------------

    @property
    def amp_model(self) -> str:
        return self.unit("amp")

    @amp_model.setter
    def amp_model(self, fender_id: str) -> None:
        if fender_id not in AMP_MODELS:
            raise PresetError(f"unknown amp model {fender_id!r}")
        self._replace_unit("amp", fender_id)

    @property
    def amp_label(self) -> str:
        return amp_label(self.amp_model)

    # -- effects ------------------------------------------------------------

    def set_effect(self, node_id: str, fender_id: str) -> None:
        """Put an effect in a slot, or PASSTHRU to empty it."""
        if node_id not in EFFECTS:
            raise PresetError(f"{node_id!r} is not an effect slot")
        if fender_id != PASSTHRU and fender_id not in EFFECTS[node_id]:
            raise PresetError(f"{fender_id!r} is not a {node_id} effect")
        self._replace_unit(node_id, fender_id)

    def _replace_unit(self, node_id: str, fender_id: str) -> None:
        node = self.node(node_id)
        if node["FenderId"] == fender_id:
            return
        defaults = _dsp_defaults().get(f"{node_id}/{fender_id}")
        if defaults is None:
            raise PresetError(
                f"no known parameter set for {fender_id!r} in slot {node_id!r}; "
                "it was not present in the captured amp library"
            )
        node["FenderId"] = fender_id
        node["dspUnitParameters"] = copy.deepcopy(defaults)

    # -- parameters ---------------------------------------------------------

    def params(self, node_id: str) -> dict:
        return self.node(node_id)["dspUnitParameters"]

    def set_param(self, node_id: str, name: str, value) -> None:
        """Change one parameter, keeping its type.

        The parameter must already exist on the node. That is what keeps a
        preset shaped like something the amp produced rather than something
        this code invented.
        """
        params = self.params(node_id)
        if name not in params:
            raise PresetError(
                f"{name!r} is not a parameter of {self.unit(node_id)!r} "
                f"in slot {node_id!r}; available: {sorted(params)}"
            )
        current = params[name]
        if isinstance(current, bool):
            if not isinstance(value, bool):
                raise PresetError(f"{name!r} expects a boolean, got {value!r}")
        elif isinstance(current, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PresetError(f"{name!r} expects a number, got {value!r}")
        elif isinstance(current, str):
            if not isinstance(value, str):
                raise PresetError(f"{name!r} expects a string, got {value!r}")
            spec = spec_for(name)
            if spec is not None and spec.choices and value not in spec.choices:
                raise PresetError(
                    f"{value!r} is not a valid {name!r}; "
                    f"choose one of: {', '.join(spec.choices)}"
                )
        params[name] = value

    def knob(self, name: str) -> float:
        """Read a tone control on the amp's own 0-10 scale."""
        spec = spec_for(name)
        value = self.params("amp")[name]
        return spec.to_display(value) if spec else value

    def knobs(self) -> dict[str, float]:
        """Every front-panel-style control this amp model exposes, on 0-10."""
        params = self.params("amp")
        return {
            name: self.knob(name)
            for name in PANEL_ORDER
            if name in params
            and (spec := spec_for(name)) is not None
            and spec.scale == "normalized"
        }

    def set_knob(self, name: str, display_value: float) -> None:
        """Set a tone control using the amp's 0-10 scale rather than 0.0-1.0."""
        spec = spec_for(name)
        if spec is None or spec.scale != "normalized":
            raise PresetError(
                f"{name!r} is not a 0-10 tone control; use set_param for it"
            )
        if name not in self.params("amp"):
            raise PresetError(
                f"{name!r} is not a parameter of {self.amp_model!r}; "
                f"available: {sorted(self.params('amp'))}"
            )
        if not 0.0 <= float(display_value) <= 10.0:
            raise PresetError(
                f"{name!r} must be in 0..10 on the amp's scale, got {display_value}"
            )
        self.set_param("amp", name, spec.from_display(float(display_value)))

    # -- description --------------------------------------------------------

    def summary(self) -> str:
        """Human-readable settings, for dialling in by hand."""
        from lt25_mcp.dsp_catalog import EFFECT_NODES, effect_label

        amp = self.params("amp")
        lines = [f"{self.display_name}  [{self.amp_label}]"]
        for knob in ("gain", "volume", "treb", "mid", "bass"):
            if knob in amp:
                value = amp[knob]
                shown = f"{value * 10:.1f}" if 0.0 <= value <= 1.0 else f"{value:.2f}"
                lines.append(f"  {knob:8} {shown}")
        for node_id in EFFECT_NODES:
            if self.has_effect(node_id):
                lines.append(f"  {node_id:8} {effect_label(node_id, self.unit(node_id))}")
        return "\n".join(lines)
