"""
datasets/health.py — Health scare life event grounding.

Source: gretelai/symptom_to_diagnosis
        Symptoms in natural patient language → likely conditions.

health_scare life event triggers when energy < 20 for 5+ consecutive ticks.
Sim describes symptoms to closest friend (friendship > 50).
Friend's agreeableness determines response quality:
  High: comfort + pushes sim to seek help → fear resolution
  Low:  dismissal → "fear of illness" acquisition
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "health_symptoms"
_HF_ID     = "gretelai/symptom_to_diagnosis"
_MAX_LOAD  = 1000

# Energy < this for N ticks triggers health_scare
HEALTH_SCARE_ENERGY_THRESHOLD = 20
HEALTH_SCARE_TICK_COUNT       = 5


def load_health_symptoms() -> list[dict]:
    """Returns list of {symptom_text, condition} dicts."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    symptoms: list[dict] = []
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(symptoms) >= _MAX_LOAD:
                break
            text = (row.get("symptoms") or row.get("text") or row.get("input") or "").strip()
            condition = (row.get("diagnosis") or row.get("label") or row.get("output") or "").strip()
            if text and 15 < len(text) < 400:
                symptoms.append({"text": text[:300], "condition": condition[:80]})
        cache_save(_CACHE_KEY, symptoms)
    except Exception:
        pass
    return symptoms


def sample_symptom(sim_energy: float) -> dict | None:
    symptoms = load_health_symptoms()
    if not symptoms:
        return None
    # Lower energy → more severe-sounding symptoms
    if sim_energy < 10:
        pool = [s for s in symptoms if any(
            w in s["text"].lower()
            for w in ["exhausted", "severe", "constant", "unable", "worst"]
        )] or symptoms
    else:
        pool = symptoms
    return random.choice(pool)


def format_health_scare_interaction(symptom: dict, sim_name: str,
                                     friend_agreeableness: float) -> str:
    response_quality = "supportive and caring" if friend_agreeableness >= 0.6 else "dismissive"
    return (
        f"[HEALTH SCARE] {sim_name} confides to their friend:\n"
        f"\"{symptom['text']}\"\n"
        f"Possible cause: {symptom['condition'] or 'unknown'}.\n"
        f"Sim B's agreeableness is {friend_agreeableness:.2f} — they tend to be {response_quality}. "
        f"High agreeableness → comfort and urging to seek help (resolves fear). "
        f"Low agreeableness → dismissal (may trigger fear of illness)."
    )


def health_scare_context(sim) -> str:
    symptom = sample_symptom(sim.needs.energy)
    if not symptom:
        return f"{sim.name} has been chronically exhausted for days."
    return (
        f"{sim.name} describes: \"{symptom['text']}\". "
        f"Possible: {symptom['condition'] or 'unknown'}. "
        f"Energy at {sim.needs.energy:.0f}/100 for several ticks."
    )
