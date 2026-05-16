"""
datasets/manipulation.py — Toxic relationship dynamics.

Sources:
  audreyeleven/MentalManip — 4k dialogues labelled for manipulation techniques:
    gaslighting, guilt-tripping, intimidation, love bombing, DARVO, dismissiveness
  Maxwe11y/gaslighting — paired gaslighting / anti-gaslighting examples

Toxic cycle mechanics:
  Phase 1: love_bombing  — sudden +15 friendship spike, positive interactions
  Phase 2: devaluation   — manipulation tactics → friendship -8 but not enough to break
  Phase 3: repair        — small positive to keep target hooked
  → cycle repeats

Trigger conditions:
  Initiator: high neuroticism (>0.65) + low agreeableness (<0.4) + Arrogant-Calculating orientation
  Target:    high neuroticism (>0.60) + anxious attachment (already in profile)
  Relationship: friendship 40-70 (established but not fully committed)
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "manipulation_index"
_MAX_LOAD  = 2000

MANIPULATION_TECHNIQUES = [
    "gaslighting", "guilt_tripping", "intimidation",
    "love_bombing", "DARVO", "dismissiveness",
]

# Technique → fear that target may acquire
TECHNIQUE_FEAR: dict[str, str] = {
    "gaslighting":    "fear of losing grip on reality",
    "guilt_tripping": "fear of being a burden",
    "intimidation":   "fear of confrontation",
    "love_bombing":   "fear of abandonment",
    "DARVO":          "fear of humiliation",
    "dismissiveness": "fear of rejection",
}

# Toxic cycle phases and their friendship deltas
CYCLE_PHASES = {
    "love_bombing": +15,
    "devaluation":  -8,
    "repair":       +5,
}
PHASE_SEQUENCE = ["love_bombing", "devaluation", "repair"]


def load_manipulation_index() -> dict[str, list[str]]:
    """Returns {technique: [example_utterances]} index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[str]] = {}

    def _ingest(hf_id: str, limit: int) -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset(hf_id, split="train", streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= limit:
                    break
                # Try common column patterns
                technique = (
                    row.get("manipulation_technique") or row.get("label") or
                    row.get("type") or row.get("category") or ""
                ).lower().replace("-", "_").replace(" ", "_")
                # Normalise to our enum
                matched = next((t for t in MANIPULATION_TECHNIQUES
                                if t.lower() in technique), None)
                if not matched and "gaslight" in technique:
                    matched = "gaslighting"
                if not matched:
                    matched = random.choice(MANIPULATION_TECHNIQUES)

                text = ""
                for col in ["utterance", "manipulative_text", "text", "dialog",
                            "sentence", "response"]:
                    val = row.get(col)
                    if val:
                        text = str(val).strip()[:300]
                        break

                if text and len(text) > 10:
                    index.setdefault(matched, []).append(text)
                    count += 1
        except Exception:
            pass

    _ingest("audreyeleven/MentalManip", _MAX_LOAD)
    _ingest("Maxwe11y/gaslighting", _MAX_LOAD // 2)

    if index:
        cache_save(_CACHE_KEY, index)
    return index


def sample_manipulation(technique: str | None = None) -> dict | None:
    index = load_manipulation_index()
    if not index:
        return None
    if technique and technique in index and index[technique]:
        pool = index[technique]
    else:
        technique = random.choice(list(index.keys()))
        pool = index.get(technique, [])
    if not pool:
        return None
    return {"technique": technique, "utterance": random.choice(pool)}


def is_toxic_initiator(sim) -> bool:
    """Check if a sim has the profile for initiating a toxic cycle."""
    return (
        sim.ocean.get("neuroticism", 0) > 0.65
        and sim.ocean.get("agreeableness", 0) < 0.40
        and getattr(sim, "social_orientation", "") == "Arrogant-Calculating"
    )


def is_toxic_target(sim) -> bool:
    """Check if a sim is vulnerable to a toxic cycle."""
    return (
        sim.ocean.get("neuroticism", 0) > 0.60
        and sim.profile.get("attachment") in ("anxious", "avoidant")
    )


def next_toxic_phase(current_phase: str) -> str:
    idx = PHASE_SEQUENCE.index(current_phase) if current_phase in PHASE_SEQUENCE else -1
    return PHASE_SEQUENCE[(idx + 1) % len(PHASE_SEQUENCE)]


def format_manipulation_interaction(manip: dict) -> str:
    tech = manip["technique"].replace("_", " ").title()
    return (
        f"[MANIPULATION — {tech}]\n"
        f"Sim A says: \"{manip['utterance']}\"\n"
        f"This is a {tech} tactic. Adjudicate the psychological impact on Sim B. "
        f"High-neuroticism targets with anxious attachment are more susceptible. "
        f"The interaction may cause fear acquisition in Sim B even if valence appears neutral."
    )
