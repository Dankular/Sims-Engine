"""
datasets/emotional_intelligence.py — llm-council/emotional_application

200 interpersonal conflict scenarios rated by 20 LLMs + human judges for
emotional intelligence quality of response.

Used as life event triggers focused on EI rather than ethics.
A sim's response is adjudicated against "emotionally intelligent" benchmarks.
High agreeableness + low neuroticism → better EI outcome; high neuroticism → overreaction.

Also tracks ei_reputation on sim: float -50..50.
  Emotionally intelligent response:  +3 ei_reputation
  Overreaction / dismissive:         -5 ei_reputation
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "ei_scenarios"
_HF_ID     = "llm-council/emotional_application"
_MAX_LOAD  = 200


def load_ei_scenarios() -> list[dict]:
    """Returns list of {scenario, context, ideal_response_hint} dicts."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    scenarios: list[dict] = []
    try:
        from datasets import load_dataset
        for split in ["train", "test", "validation"]:
            try:
                ds = load_dataset(_HF_ID, split=split,
                                  streaming=True, trust_remote_code=True)
                for row in ds:
                    if len(scenarios) >= _MAX_LOAD:
                        break
                    # Try multiple column name patterns
                    scenario = (
                        row.get("scenario") or row.get("situation") or
                        row.get("context") or row.get("input") or
                        row.get("question") or ""
                    ).strip()
                    ideal = (
                        row.get("ideal_response") or row.get("best_response") or
                        row.get("reference") or row.get("answer") or ""
                    ).strip()
                    category = (
                        row.get("category") or row.get("topic") or "interpersonal"
                    ).strip()

                    if scenario and len(scenario) > 20:
                        scenarios.append({
                            "scenario": scenario[:400],
                            "ideal":    ideal[:300] if ideal else "",
                            "category": category,
                        })
                if scenarios:
                    break
            except Exception:
                continue
        if scenarios:
            cache_save(_CACHE_KEY, scenarios)
    except Exception:
        pass
    return scenarios


def sample_ei_scenario() -> dict | None:
    scenarios = load_ei_scenarios()
    return random.choice(scenarios) if scenarios else None


def format_ei_interaction(scenario: dict) -> str:
    ideal_hint = f"\nEmotionally intelligent approach: \"{scenario['ideal'][:200]}\"" \
                 if scenario.get("ideal") else ""
    return (
        f"[EMOTIONAL INTELLIGENCE SCENARIO]\n"
        f"Situation: {scenario['scenario']}\n"
        f"{ideal_hint}\n"
        f"Based on Sim A's OCEAN (especially agreeableness and neuroticism), "
        f"adjudicate how they respond. High EI response boosts ei_reputation; "
        f"overreaction or dismissiveness lowers it."
    )


def ei_reputation_delta(agreeableness: float, neuroticism: float, valence: float) -> float:
    """
    Estimate ei_reputation change from an EI scenario outcome.
    High agree + low neuro + positive valence → strong EI response.
    """
    if valence >= 0.5:
        base = 3.0 + agreeableness * 2 - neuroticism * 1.5
    else:
        base = -5.0 - neuroticism * 2 + agreeableness * 1.0
    return round(max(-10.0, min(5.0, base)), 1)
