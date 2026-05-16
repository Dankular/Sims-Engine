"""
datasets/social_conformity.py — Peer pressure & social conformity.

Source: stanfordnlp/SHP (Stanford Human Preferences)
  Reddit preference data showing "herding effect" — social votes influence
  alignment. High-score posts demonstrate conformity pressure in action.

Mechanic: In group events with 3+ Sims, if ≥70% share a trait/behavior,
unaligned Sims feel conformity_pressure proportional to (1 - openness).
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "shp_conformity"
_HF_ID     = "stanfordnlp/SHP"
_MAX_LOAD  = 1500


def load_conformity_examples() -> list[dict]:
    """
    Returns list of {context, preferred_response, score_ratio}
    where score_ratio shows how much the crowd preferred one response.
    """
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    examples: list[dict] = []
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(examples) >= _MAX_LOAD:
                break
            hist    = (row.get("history") or row.get("context") or "").strip()
            resp_a  = (row.get("human_ref_A") or row.get("response_a") or "").strip()
            resp_b  = (row.get("human_ref_B") or row.get("response_b") or "").strip()
            score_a = float(row.get("score_A") or row.get("scores_A") or 1)
            score_b = float(row.get("score_B") or row.get("scores_B") or 1)
            label   = row.get("labels") or row.get("label") or 1
            if not hist or not resp_a:
                continue
            preferred = resp_a if (label == 1 or score_a >= score_b) else resp_b
            ratio = max(score_a, score_b) / max(1, min(score_a, score_b))
            examples.append({
                "context":   hist[:200],
                "preferred": preferred[:200],
                "ratio":     round(ratio, 1),
            })
        cache_save(_CACHE_KEY, examples)
    except Exception:
        pass
    return examples


def compute_conformity_pressure(sims: list, trait: str | None = None) -> dict[str, float]:
    """
    Given a list of Sim objects in a group event, compute conformity pressure
    for each sim. Returns {sim_id: pressure 0.0-1.0}.
    """
    if not sims or len(sims) < 3:
        return {}

    # Find most common trait in group
    if trait is None:
        trait_counts: dict[str, int] = {}
        for s in sims:
            for t in s.profile.get("traits", []):
                trait_counts[t] = trait_counts.get(t, 0) + 1
        if not trait_counts:
            return {}
        trait = max(trait_counts, key=trait_counts.get)
        trait_count = trait_counts[trait]
    else:
        trait_count = sum(1 for s in sims if trait in s.profile.get("traits", []))

    majority_ratio = trait_count / len(sims)
    if majority_ratio < 0.7:
        return {}  # no dominant trait

    pressures: dict[str, float] = {}
    for sim in sims:
        if trait not in sim.profile.get("traits", []):
            # Non-conforming Sim: pressure inversely proportional to openness
            openness = sim.ocean.get("openness", 0.5)
            pressure = majority_ratio * (1.0 - openness) * 0.8
            pressures[sim.sim_id] = round(pressure, 2)
    return pressures


def sample_herding_seed() -> str | None:
    examples = load_conformity_examples()
    if not examples:
        return None
    # Pick high-ratio examples (strong herd preference)
    high = [e for e in examples if e["ratio"] >= 3]
    pool = high or examples
    pick = random.choice(pool)
    return f"[PEER PRESSURE] Group consensus: \"{pick['preferred'][:180]}\""
