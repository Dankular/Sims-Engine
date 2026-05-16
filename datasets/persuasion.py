"""
datasets/persuasion.py — Anthropic/persuasion dataset for conviction mechanics.

Claims + persuasive arguments + before/after opinion deltas.
Powers the "convince" interaction type and Charisma-based social influence.

Persuasiveness modifier on outcome:
  base_delta × (1 + charisma_modifier) × target_receptiveness
  target_receptiveness = agreeableness - (neuroticism * 0.3)
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "persuasion_args"
_HF_ID     = "Anthropic/persuasion"
_MAX_LOAD  = 1500


def load_persuasion() -> list[dict]:
    """Returns list of {claim, argument, delta} dicts."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    entries: list[dict] = []
    try:
        from datasets import load_dataset
        # Try multiple splits — dataset may use different names
        for split in ["train", "validation", "test"]:
            try:
                ds = load_dataset(_HF_ID, split=split,
                                  streaming=True, trust_remote_code=True)
                for row in ds:
                    if len(entries) >= _MAX_LOAD:
                        break
                    # Column names vary — try several
                    claim    = (row.get("claim") or row.get("topic") or
                                row.get("question") or "").strip()
                    argument = (row.get("argument") or row.get("persuasive_text") or
                                row.get("text") or "").strip()
                    delta    = float(row.get("persuasion_score") or
                                     row.get("opinion_delta") or
                                     row.get("score") or 0)
                    if claim and argument:
                        entries.append({
                            "claim":    claim[:200],
                            "argument": argument[:400],
                            "delta":    round(delta, 2),
                        })
                if entries:
                    break
            except Exception:
                continue
        if entries:
            cache_save(_CACHE_KEY, entries)
    except Exception:
        pass
    return entries


def sample_argument(topic_hint: str | None = None) -> dict | None:
    entries = load_persuasion()
    if not entries:
        return None
    if topic_hint:
        hint_lower = topic_hint.lower()
        matches = [e for e in entries if hint_lower in e["claim"].lower()]
        if matches:
            return random.choice(matches[:20])
    return random.choice(entries)


def compute_persuasion_modifier(
    charisma_skill: float,
    target_agreeableness: float,
    target_neuroticism: float,
    argument_delta: float,
) -> float:
    """
    Return a multiplier applied to friendship_delta for convince interactions.
    Range roughly 0.5 – 2.5.
    """
    charisma_mod     = charisma_skill / 10.0              # 0–1
    receptiveness    = target_agreeableness - target_neuroticism * 0.3
    receptiveness    = max(0.1, min(1.0, receptiveness))
    base_delta_norm  = max(0.0, min(1.0, argument_delta / 10.0)) if argument_delta > 0 else 0.5
    modifier = (1.0 + charisma_mod * 0.8) * receptiveness * (0.5 + base_delta_norm)
    return round(max(0.2, min(2.5, modifier)), 2)


def format_convince_interaction(argument: dict) -> str:
    return (
        f"[CONVINCE] Claim: \"{argument['claim']}\"\n"
        f"Sim A's argument: \"{argument['argument'][:200]}\"\n"
        f"Adjudicate whether Sim B is persuaded based on their personality."
    )
