"""
datasets/moral_stories.py — demelin/moral_stories loader.

Each entry: situation, intention, moral_action, moral_consequence,
immoral_action, immoral_consequence, norm.

Used to inject moral dilemma events into the scheduler.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "moral_stories"
_HF_ID     = "demelin/moral_stories"
_MAX_LOAD  = 1200


def load_moral_stories() -> list[dict]:
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, "full", split="validation",
                          streaming=True, trust_remote_code=True)
        stories: list[dict] = []
        for row in ds:
            if len(stories) >= _MAX_LOAD:
                break
            sit  = (row.get("situation") or "").strip()
            norm = (row.get("norm") or "").strip()
            ma   = (row.get("moral_action") or "").strip()
            mc   = (row.get("moral_consequence") or "").strip()
            ia   = (row.get("immoral_action") or "").strip()
            ic   = (row.get("immoral_consequence") or "").strip()
            if sit and ma and ia:
                stories.append({
                    "norm": norm,
                    "situation": sit,
                    "moral_action": ma,
                    "moral_consequence": mc,
                    "immoral_action": ia,
                    "immoral_consequence": ic,
                })
        cache_save(_CACHE_KEY, stories)
        return stories
    except Exception:
        return []


def sample_dilemma() -> dict | None:
    stories = load_moral_stories()
    return random.choice(stories) if stories else None


def format_dilemma_interaction(dilemma: dict) -> str:
    """Format a moral dilemma as an interaction string for the adjudicator."""
    return (
        f"[MORAL DILEMMA]\n"
        f"Situation: {dilemma['situation']}\n"
        f"Norm at stake: {dilemma['norm']}\n"
        f"Option A (principled): {dilemma['moral_action']}\n"
        f"  → consequence: {dilemma['moral_consequence']}\n"
        f"Option B (self-serving): {dilemma['immoral_action']}\n"
        f"  → consequence: {dilemma['immoral_consequence']}\n"
        f"\nBased on Sim A's personality (OCEAN), decide which option they choose "
        f"and adjudicate the realistic social outcome."
    )
