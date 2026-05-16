"""
datasets/social_orientation.py — Interpersonal Circumplex social orientation labels.

Source: tee-oh-double-dee/social-orientation
30,012 utterances labelled with 8 circumplex positions from real conversations.

The 8 positions (dominance × affiliation):
  Assured-Dominant      high dom, mid aff
  Gregarious-Extraverted high dom, high aff
  Warm-Agreeable        mid dom, high aff
  Unassuming-Ingenuous  low dom, high aff
  Unassured-Submissive  low dom, mid aff
  Aloof-Introverted     low dom, low aff
  Cold                  mid dom, low aff
  Arrogant-Calculating  high dom, low aff
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "social_orientation_index"
_HF_ID     = "tee-oh-double-dee/social-orientation"
_MAX_LOAD  = 5000

ORIENTATIONS = [
    "Assured-Dominant",
    "Gregarious-Extraverted",
    "Warm-Agreeable",
    "Unassuming-Ingenuous",
    "Unassured-Submissive",
    "Aloof-Introverted",
    "Cold",
    "Arrogant-Calculating",
]

# Adjudicator descriptors
ORIENTATION_DESCRIPTORS: dict[str, str] = {
    "Assured-Dominant":      "confident, takes charge, assertive, directive",
    "Gregarious-Extraverted": "outgoing, warm, enthusiastic, seeks connection",
    "Warm-Agreeable":        "supportive, empathetic, cooperative, easy-going",
    "Unassuming-Ingenuous":  "modest, deferential, trusting, open",
    "Unassured-Submissive":  "hesitant, self-doubting, easily swayed, passive",
    "Aloof-Introverted":     "distant, reserved, prefers solitude, guarded",
    "Cold":                  "detached, unemotional, impersonal, standoffish",
    "Arrogant-Calculating":  "self-serving, manipulative, competitive, dismissive",
}

# OCEAN + needs → circumplex mapping
def orientation_from_ocean_needs(ocean: dict, needs: dict, emotion: str) -> str:
    """Derive current social orientation from OCEAN, needs pressures, and emotion."""
    extrav = ocean.get("extraversion", 0.5)
    agree  = ocean.get("agreeableness", 0.5)
    neuro  = ocean.get("neuroticism", 0.5)
    consc  = ocean.get("conscientiousness", 0.5)

    energy  = needs.get("energy", 80)
    social  = needs.get("social", 70)

    # Emotion overrides
    if emotion in ("anger", "annoyance", "disgust"):
        return "Arrogant-Calculating"
    if emotion in ("fear", "nervousness") or social < 25:
        return "Unassured-Submissive"
    if energy < 25:
        return "Aloof-Introverted"

    # OCEAN-based
    dominance   = extrav * 0.5 + consc * 0.3 - agree * 0.2
    affiliation = agree  * 0.5 + extrav * 0.3 - neuro * 0.2

    if dominance > 0.6:
        return "Gregarious-Extraverted" if affiliation > 0.55 else "Assured-Dominant"
    if dominance < 0.35:
        if affiliation > 0.55:
            return "Unassuming-Ingenuous"
        if affiliation < 0.35:
            return "Aloof-Introverted"
        return "Unassured-Submissive"
    if affiliation > 0.6:
        return "Warm-Agreeable"
    if affiliation < 0.35:
        return "Cold"
    return "Warm-Agreeable"   # neutral default


def update_orientation_after_interaction(
    current: str,
    valence: float,
    emotion: str,
    ocean: dict,
) -> str:
    """Drift orientation slightly based on interaction outcome."""
    if valence > 0.7 and emotion in ("joy", "gratitude", "love", "excitement"):
        high_aff = ["Gregarious-Extraverted", "Warm-Agreeable"]
        if current not in high_aff:
            return "Warm-Agreeable"
    if valence < -0.5 and emotion in ("anger", "annoyance", "disgust"):
        if current != "Arrogant-Calculating":
            return "Cold"
    if valence < -0.7:
        return "Unassured-Submissive"
    return current   # no change


def load_orientation_examples() -> dict[str, list[str]]:
    """Returns orientation label → [example utterances] for few-shot injection."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[str]] = {}
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= _MAX_LOAD:
                break
            text  = (row.get("utterance") or row.get("text") or "").strip()
            label = (row.get("label") or row.get("orientation") or "").strip()
            # Normalise label to our enum
            matched = next((o for o in ORIENTATIONS if o.lower() in label.lower()), None)
            if text and matched and len(text) > 10:
                index.setdefault(matched, []).append(text[:200])
                count += 1
        if index:
            cache_save(_CACHE_KEY, index)
    except Exception:
        pass
    return index


def get_orientation_example(orientation: str) -> str | None:
    examples = load_orientation_examples()
    pool = examples.get(orientation, [])
    return random.choice(pool) if pool else None
