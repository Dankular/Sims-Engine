"""
core/appearance.py — Sim appearance model with validation.

Appearance is stored in sim.profile["appearance"] and is set once at
signup, editable later via PUT /auth/sim/appearance.

All string fields are validated against an allowed set so the adjudicator
receives clean, consistent descriptors rather than freeform input.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# ── Allowed values ────────────────────────────────────────────────────────────

SKIN_TONES   = {"fair", "light", "medium", "tan", "dark", "deep"}
HAIR_COLORS  = {
    "blonde", "brunette", "black", "red", "auburn", "grey",
    "white", "blue", "purple", "pink", "green", "orange",
}
HAIR_STYLES  = {
    "short", "long", "medium", "curly", "wavy", "straight",
    "bun", "braids", "mohawk", "shaved", "afro", "locs",
    "ponytail", "pixie",
}
EYE_COLORS   = {"blue", "green", "brown", "hazel", "grey", "amber", "violet"}
BUILDS       = {"slim", "athletic", "average", "curvy", "stocky", "petite"}
HEIGHTS      = {"short", "average", "tall"}
STYLES       = {
    "casual", "formal", "sporty", "bohemian", "punk", "preppy",
    "vintage", "streetwear", "minimalist", "eclectic", "goth",
    "cottagecore", "techwear",
}
ACCESSORIES  = {
    "glasses", "sunglasses", "hat", "scarf", "jewelry",
    "watch", "bag", "piercing", "tattoos", "headphones",
    "beanie", "cap", "necklace", "earrings", "bracelet",
}

_FIELD_OPTIONS: dict[str, set[str]] = {
    "skin_tone":  SKIN_TONES,
    "hair_color": HAIR_COLORS,
    "hair_style": HAIR_STYLES,
    "eye_color":  EYE_COLORS,
    "build":      BUILDS,
    "height":     HEIGHTS,
    "style":      STYLES,
}

# ── Model ─────────────────────────────────────────────────────────────────────

@dataclass
class SimAppearance:
    skin_tone:   str        = "medium"
    hair_color:  str        = "brunette"
    hair_style:  str        = "medium"
    eye_color:   str        = "brown"
    build:       str        = "average"
    height:      str        = "average"
    style:       str        = "casual"
    accessories: list[str]  = field(default_factory=list)

    # Free-text fields (validated length, not enum)
    bio:         str        = ""    # player-written flavour text, max 280 chars

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_profile_text(self) -> str:
        """Compact descriptor for adjudicator context injection."""
        acc = ", ".join(self.accessories) if self.accessories else "none"
        return (
            f"{self.height} {self.build} sim, {self.skin_tone} skin, "
            f"{self.hair_color} {self.hair_style} hair, {self.eye_color} eyes, "
            f"{self.style} style, accessories: {acc}"
        )


# ── Validation ────────────────────────────────────────────────────────────────

class AppearanceValidationError(ValueError):
    pass


def validate_appearance(data: dict) -> SimAppearance:
    """
    Parse and validate appearance data from user input.
    Returns a SimAppearance on success. Raises AppearanceValidationError on
    unknown values — lists all problems at once rather than stopping at first.
    """
    errors: list[str] = []

    def _pick(key: str, allowed: set[str], default: str) -> str:
        val = str(data.get(key, default)).strip().lower()
        if val not in allowed:
            errors.append(
                f"'{key}' value '{val}' not allowed. "
                f"Options: {sorted(allowed)}"
            )
            return default
        return val

    skin_tone   = _pick("skin_tone",  SKIN_TONES,  "medium")
    hair_color  = _pick("hair_color", HAIR_COLORS, "brunette")
    hair_style  = _pick("hair_style", HAIR_STYLES, "medium")
    eye_color   = _pick("eye_color",  EYE_COLORS,  "brown")
    build       = _pick("build",      BUILDS,      "average")
    height      = _pick("height",     HEIGHTS,     "average")
    style       = _pick("style",      STYLES,      "casual")

    raw_acc = data.get("accessories", [])
    if isinstance(raw_acc, str):
        raw_acc = [a.strip() for a in raw_acc.split(",") if a.strip()]
    accessories = []
    for acc in raw_acc:
        a = acc.strip().lower()
        if a in ACCESSORIES:
            accessories.append(a)
        else:
            errors.append(f"Unknown accessory '{a}'. Options: {sorted(ACCESSORIES)}")

    bio = str(data.get("bio", "")).strip()[:280]

    if errors:
        raise AppearanceValidationError("; ".join(errors))

    return SimAppearance(
        skin_tone=skin_tone,
        hair_color=hair_color,
        hair_style=hair_style,
        eye_color=eye_color,
        build=build,
        height=height,
        style=style,
        accessories=accessories,
        bio=bio,
    )


def default_appearance() -> SimAppearance:
    """Random-ish default appearance when user skips customisation."""
    import random
    return SimAppearance(
        skin_tone=random.choice(list(SKIN_TONES)),
        hair_color=random.choice(["blonde", "brunette", "black", "red", "auburn"]),
        hair_style=random.choice(["short", "long", "medium", "curly", "wavy"]),
        eye_color=random.choice(["blue", "green", "brown", "hazel", "grey"]),
        build=random.choice(list(BUILDS)),
        height=random.choice(list(HEIGHTS)),
        style=random.choice(["casual", "sporty", "formal", "bohemian"]),
        accessories=[],
    )


def options() -> dict[str, Any]:
    """Return all valid values per field — useful for frontend dropdowns."""
    return {
        "skin_tone":   sorted(SKIN_TONES),
        "hair_color":  sorted(HAIR_COLORS),
        "hair_style":  sorted(HAIR_STYLES),
        "eye_color":   sorted(EYE_COLORS),
        "build":       sorted(BUILDS),
        "height":      sorted(HEIGHTS),
        "style":       sorted(STYLES),
        "accessories": sorted(ACCESSORIES),
    }
