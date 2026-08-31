"""The amp's vocabulary of DSP units.

FenderIds were observed in the 60 factory presets read off a Mustang LT 25 on
firmware 2.1.4. Human labels come from the Mustang LT25 owner's manual, matched
to FenderIds by the amp each one is modelled on.

Note that the amp's own firmware uses ids that differ from other LT models in
places (`DUBS_MetalEvh3`, not `DUBS_Evh3`; `DUBS_Silvertone` for SMALLTONE), so
this catalog is derived from real hardware rather than from documentation.
"""

from __future__ import annotations

PASSTHRU = "DUBS_Passthru"
"""FenderId meaning 'this slot is empty'."""

AMP_MODELS: dict[str, str] = {
    "DUBS_Twin57": "50S TWIN",
    "DUBS_Ac30Tb": "60S UK CLN",
    "DUBS_Plexi87": "70S ROCK",
    "DUBS_DR103": "70S UK CLN",
    "DUBS_Jcm800": "80S ROCK",
    "DUBS_Rect2": "90S ROCK",
    "DUBS_Bassman59": "BASSMAN",
    "DUBS_SuperSonic": "BURN",
    "DUBS_Champ57": "CHAMP",
    "DUBS_Deluxe65": "DELUXE CLN",
    "DUBS_Deluxe57": "DELUXE DIRT",
    "DUBS_Or120": "DOOM METAL",
    "DUBS_Excelsior": "EXCELSIOR",
    "DUBS_MetalRect2": "ALT METAL",
    "DUBS_MetalEvh3": "METAL 2000",
    "DUBS_Princeton65": "PRINCETON",
    "DUBS_Silvertone": "SMALLTONE",
    "DUBS_LinearGain": "SUPER CLEAN",
    "DUBS_Twin65": "TWIN CLEAN",
    # Listed in the manual but not used by any factory preset, so the id is
    # inferred from the sibling EVH model rather than observed.
    "DUBS_Evh3": "SUPER HEAVY",
}

EFFECTS: dict[str, dict[str, str]] = {
    "stomp": {
        "DUBS_SimpleCompressor": "COMPRESSOR",
        "DUBS_Sustain": "SUSTAIN",
        "DUBS_MustangFiveBandEq1": "5-BAND EQ",
        "DUBS_Overdrive": "OVERDRIVE",
        "DUBS_Greenbox": "GREENBOX",
        "DUBS_Blackbox": "BLACKBOX",
        "DUBS_MythicDrive": "MYTHIC DRIVE",
        "DUBS_BigFuzz": "BIG FUZZ",
        "DUBS_VariFuzz": "VARI FUZZ",
        "DUBS_Octobot": "OCTOBOT",
        "DUBS_ChromeGate": "CHROME GATE",
    },
    "mod": {
        "DUBS_ChorusTriangle": "CHORUS",
        "DUBS_TriangleFlanger": "FLANGER",
        "DUBS_Phaser": "PHASER",
        "DUBS_SineTremolo": "TREMOLO",
        "DUBS_Vibratone": "VIBRATONE",
        "DUBS_EcFilter": "TOUCH WAH",
        "DUBS_StepFilter": "STEP FILTER",
    },
    "delay": {
        "DUBS_MonoDelay": "MONO DELAY",
        "DUBS_TapeDelayLite": "TAPE DELAY",
        "DUBS_ReverseDelay": "REVERSE DELAY",
    },
    "reverb": {
        "DUBS_SmallRoomReverb": "SMALL ROOM",
        "DUBS_LargeHallReverb": "LARGE HALL",
        "DUBS_LargePlate": "LARGE PLATE",
        "DUBS_ArenaReverb": "ARENA",
        "DUBS_Spring65": "SPRING 65",
    },
}

EFFECT_NODES = tuple(EFFECTS)
"""Node ids that hold an effect, in signal-path order."""

NODE_ORDER = ("stomp", "mod", "amp", "delay", "reverb")
"""Signal path: guitar -> stomp -> mod -> amp -> delay -> reverb -> speaker."""


def amp_label(fender_id: str) -> str:
    """Human label for an amp FenderId, or the id itself if uncatalogued."""
    return AMP_MODELS.get(fender_id, fender_id)


def effect_label(node_id: str, fender_id: str) -> str:
    if fender_id == PASSTHRU:
        return "NONE"
    return EFFECTS.get(node_id, {}).get(fender_id, fender_id)
