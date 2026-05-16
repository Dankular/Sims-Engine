"""
datasets/trauma.py — Trauma arc dataset grounding.

Source: yenopoya/thousand-voices-trauma
  3,000 synthetic PE therapy dialogues, 20 trauma types,
  tracks anxiety → peak distress → cognitive processing progression.

After high-magnitude loss/conflict events (|valence| > 0.8):
  - Small permanent OCEAN drift (neuroticism +0.03, openness -0.02)
  - trauma_events: list[str] tracks what the Sim has been through
  - Subsequent interactions seeded with trauma-aware dialogue texture
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "trauma_index"
_HF_ID     = "yenopoya/thousand-voices-trauma"
_MAX_LOAD  = 1500

# Trauma types → OCEAN drift tuples (neuroticism_delta, openness_delta)
TRAUMA_OCEAN_DRIFT: dict[str, tuple[float, float]] = {
    "loss":             (+0.04, -0.02),
    "betrayal":         (+0.03, -0.03),
    "rejection":        (+0.03, -0.01),
    "conflict":         (+0.02, -0.01),
    "accident":         (+0.03, -0.02),
    "default":          (+0.03, -0.02),
}


def load_trauma_index() -> dict[str, list[str]]:
    """Returns {trauma_type: [therapeutic_dialogue_excerpts]}."""
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
            trauma_type = (row.get("trauma_type") or row.get("type") or
                           row.get("category") or "default").lower()
            text = (row.get("dialogue") or row.get("text") or
                    row.get("patient_speech") or row.get("utterance") or "").strip()
            stage = (row.get("stage") or row.get("phase") or "processing")
            if text and len(text) > 20:
                key = f"{trauma_type}_{stage}" if stage else trauma_type
                index.setdefault(key, []).append(text[:300])
                index.setdefault(trauma_type, []).append(text[:300])
                count += 1
        cache_save(_CACHE_KEY, index)
    except Exception:
        pass
    return index


def get_ocean_drift(event_type: str) -> tuple[float, float]:
    """Return (neuroticism_delta, openness_delta) for a trauma event type."""
    for key in TRAUMA_OCEAN_DRIFT:
        if key in event_type.lower():
            return TRAUMA_OCEAN_DRIFT[key]
    return TRAUMA_OCEAN_DRIFT["default"]


def apply_trauma_drift(sim, event_type: str) -> None:
    """Apply small permanent OCEAN drift from a traumatic event."""
    n_delta, o_delta = get_ocean_drift(event_type)
    ocean = sim.profile["ocean"]
    ocean["neuroticism"] = round(min(1.0, ocean["neuroticism"] + n_delta), 2)
    ocean["openness"]    = round(max(0.0, ocean["openness"]    + o_delta), 2)
    sim.profile["ocean"] = ocean
    if not hasattr(sim, "trauma_events"):
        sim.trauma_events = []
    sim.trauma_events.append(event_type)


def sample_trauma_texture(trauma_type: str) -> str | None:
    index = load_trauma_index()
    pool = index.get(trauma_type, []) or [t for v in index.values() for t in v]
    return random.choice(pool) if pool else None


def format_trauma_context(sim) -> str:
    traumas = getattr(sim, "trauma_events", [])
    if not traumas:
        return ""
    return (
        f"Trauma history: {', '.join(traumas[-3:])}. "
        f"Neuroticism has permanently shifted to {sim.ocean.get('neuroticism', 0.5):.2f}. "
        f"Apply trauma-aware sensitivity in adjudication."
    )
