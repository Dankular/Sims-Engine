"""
identity/zodiac.py — Zodiac sign system with OCEAN alignment.

Each sim gets a birthday (from Faker). Birthday → zodiac sign → OCEAN modifiers.
These modifiers are soft nudges (+/- 0.05) layered on top of the scored OCEAN.

OCEAN alignment per sign (from user-provided mapping):
  O = Openness   C = Conscientiousness  E = Extraversion
  A = Agreeableness  N = Neuroticism
  "Mid" = no modifier  "High" = +0.05  "Low" = -0.05
"""
from __future__ import annotations

import datetime
import random

# ── Sign boundaries (month, day) ──────────────────────────────────────────────
_SIGN_RANGES = [
    ("Aries",       (3, 21),  (4, 19)),
    ("Taurus",      (4, 20),  (5, 20)),
    ("Gemini",      (5, 21),  (6, 20)),
    ("Cancer",      (6, 21),  (7, 22)),
    ("Leo",         (7, 23),  (8, 22)),
    ("Virgo",       (8, 23),  (9, 22)),
    ("Libra",       (9, 23),  (10, 22)),
    ("Scorpio",     (10, 23), (11, 21)),
    ("Sagittarius", (11, 22), (12, 21)),
    ("Capricorn",   (12, 22), (1, 19)),
    ("Aquarius",    (1, 20),  (2, 18)),
    ("Pisces",      (2, 19),  (3, 20)),
]

# OCEAN modifiers per sign (0 = neutral, +1 = high nudge, -1 = low nudge)
# Mapped directly from user's table.
_SIGN_OCEAN: dict[str, dict[str, int]] = {
    "Aries":       {"O": 0,  "C": -1, "E": 1,  "A": -1, "N": 0},
    "Taurus":      {"O": -1, "C": 1,  "E": -1, "A": 1,  "N": -1},
    "Gemini":      {"O": 1,  "C": -1, "E": 1,  "A": 0,  "N": 0},
    "Cancer":      {"O": 0,  "C": 0,  "E": -1, "A": 1,  "N": 1},
    "Leo":         {"O": 0,  "C": 0,  "E": 1,  "A": 0,  "N": -1},
    "Virgo":       {"O": 0,  "C": 1,  "E": -1, "A": 0,  "N": 1},
    "Libra":       {"O": 0,  "C": 0,  "E": 1,  "A": 1,  "N": 0},
    "Scorpio":     {"O": 1,  "C": 0,  "E": -1, "A": -1, "N": 1},
    "Sagittarius": {"O": 1,  "C": -1, "E": 1,  "A": 0,  "N": -1},
    "Capricorn":   {"O": -1, "C": 1,  "E": -1, "A": -1, "N": 0},
    "Aquarius":    {"O": 1,  "C": -1, "E": -1, "A": 0,  "N": -1},
    "Pisces":      {"O": 1,  "C": -1, "E": 0,  "A": 1,  "N": 1},
}

_OCEAN_KEY_MAP = {
    "O": "openness", "C": "conscientiousness",
    "E": "extraversion", "A": "agreeableness", "N": "neuroticism",
}

_NUDGE = 0.05  # per modifier point


def birthday_to_sign(month: int, day: int) -> str:
    for sign, (sm, sd), (em, ed) in _SIGN_RANGES:
        if (sm, sd) <= (month, day) <= (em, ed):
            return sign
        # Capricorn wraps year-end
        if sign == "Capricorn" and ((month, day) >= (12, 22) or (month, day) <= (1, 19)):
            return sign
    return "Aries"  # fallback


def generate_birthday(age: int) -> tuple[int, int, int]:
    """Return (year, month, day) for a sim of the given age."""
    today = datetime.date.today()
    birth_year = today.year - age
    month = random.randint(1, 12)
    max_day = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month]
    day = random.randint(1, max_day)
    return birth_year, month, day


def apply_zodiac_nudge(ocean: dict, sign: str) -> dict:
    """Apply soft OCEAN nudges from zodiac sign. Clamps to [0, 1]."""
    mods = _SIGN_OCEAN.get(sign, {})
    result = dict(ocean)
    for short, long_key in _OCEAN_KEY_MAP.items():
        mod = mods.get(short, 0)
        if mod and long_key in result:
            result[long_key] = round(max(0.0, min(1.0, result[long_key] + mod * _NUDGE)), 2)
    return result


def sign_descriptor(sign: str) -> str:
    descriptors = {
        "Aries":       "bold, impulsive, competitive",
        "Taurus":      "grounded, patient, stubborn",
        "Gemini":      "curious, adaptable, restless",
        "Cancer":      "nurturing, sensitive, protective",
        "Leo":         "confident, dramatic, generous",
        "Virgo":       "analytical, perfectionist, practical",
        "Libra":       "diplomatic, charming, indecisive",
        "Scorpio":     "intense, private, determined",
        "Sagittarius": "optimistic, adventurous, blunt",
        "Capricorn":   "ambitious, disciplined, reserved",
        "Aquarius":    "independent, eccentric, idealistic",
        "Pisces":      "empathetic, dreamy, escapist",
    }
    return descriptors.get(sign, "")


def enrich_profile_with_zodiac(profile: dict) -> dict:
    """Add birthday, zodiac sign, and OCEAN nudge to a sim profile in-place."""
    age = profile.get("age", 25)
    year, month, day = generate_birthday(age)
    sign = birthday_to_sign(month, day)
    profile["birthday"] = {"year": year, "month": month, "day": day}
    profile["zodiac"] = sign
    profile["zodiac_descriptor"] = sign_descriptor(sign)
    # Apply soft nudge on top of scored OCEAN
    profile["ocean"] = apply_zodiac_nudge(profile["ocean"], sign)
    return profile
