"""
datasets/creative_works.py — Creativity skill social content.

Source: euclaise/writingprompts — r/WritingPrompts: prompts + human responses, varying quality

Creativity skill gates quality tier (proxy: response length + vote count if available):
  Lvl 1-2: short, rough pieces
  Lvl 3-4: mid-quality, structured
  Lvl 5+:  rich, resonant, emotionally grounded

Receiving sim reaction calibrated by:
  - Piece quality tier
  - Target's openness (high → appreciates experimental; low → prefers conventional)

creative_reputation on Sim: float 0-100, increases when "share artwork" generates
positive valence from high-openness target.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "creative_works_index"
_HF_ID     = "euclaise/writingprompts"
_MAX_LOAD  = 3000


def load_creative_works() -> dict[str, list[dict]]:
    """Returns {tier: [{prompt, story, quality_estimate}]} index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    tiers: dict[str, list[dict]] = {"low": [], "mid": [], "high": []}
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= _MAX_LOAD:
                break
            prompt = (row.get("prompt") or row.get("title") or "").strip()
            story  = (row.get("story") or row.get("text") or row.get("response") or "").strip()
            score  = float(row.get("score") or row.get("ups") or row.get("votes") or 0)

            if not story or len(story) < 50:
                continue

            # Quality tier by score and length
            length = len(story)
            if score > 500 or length > 1500:
                tier = "high"
            elif score > 50 or length > 500:
                tier = "mid"
            else:
                tier = "low"

            tiers[tier].append({
                "prompt":  prompt[:150],
                "excerpt": story[:600],
                "length":  length,
                "score":   int(score),
            })
            count += 1
        cache_save(_CACHE_KEY, tiers)
    except Exception:
        pass
    return tiers


def sample_creative_work(creativity_skill: float) -> dict | None:
    """Return a creative work appropriate for the sim's skill level."""
    tiers = load_creative_works()
    if creativity_skill >= 5:
        pool = tiers.get("high", []) or tiers.get("mid", [])
    elif creativity_skill >= 3:
        pool = tiers.get("mid", []) or tiers.get("low", [])
    else:
        pool = tiers.get("low", [])
    return random.choice(pool) if pool else None


def format_creative_interaction(work: dict, creativity_skill: float,
                                 interaction: str = "share artwork") -> str:
    quality = ("masterful" if creativity_skill >= 5
               else "competent" if creativity_skill >= 3 else "rough")
    verb = "performs" if "perform" in interaction else "shares"
    return (
        f"[CREATIVE WORK — {quality} {interaction.replace('_', ' ')}]\n"
        f"Inspired by: \"{work['prompt']}\"\n"
        f"Sim A {verb}: \"{work['excerpt'][:300]}\"\n"
        f"Adjudicate Sim B's reaction based on their openness score. "
        f"High openness → appreciates experimental work; low openness → prefers conventional forms. "
        f"Quality tier: {quality}."
    )


def creative_reputation_delta(valence: float, target_openness: float,
                               creativity_skill: float) -> float:
    """How much creative_reputation changes from this interaction."""
    if valence >= 0.6 and target_openness >= 0.55:
        return round(3.0 + creativity_skill * 0.5, 1)
    if valence >= 0.4:
        return 1.0
    if valence < 0.0:
        return -2.0
    return 0.0
