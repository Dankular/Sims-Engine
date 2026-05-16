"""
datasets/moral_choice.py — ninoscherrer/moralchoice.

1,767 hypothetical moral scenarios with two concrete action choices.
680 are genuinely ambiguous — maps to personality-driven divergence.
Complements moral_stories with shorter, punchier dilemmas.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "moral_choice"
_HF_ID     = "ninoscherrer/moralchoice"
_MAX_LOAD  = 1000


def load_moral_choice() -> list[dict]:
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        entries: list[dict] = []
        for row in ds:
            if len(entries) >= _MAX_LOAD:
                break
            scenario  = (row.get("scenario")  or row.get("situation") or "").strip()
            action1   = (row.get("action1")   or row.get("option_1")  or "").strip()
            action2   = (row.get("action2")   or row.get("option_2")  or "").strip()
            ambiguous = bool(row.get("ambiguous") or row.get("is_ambiguous"))
            if scenario and action1 and action2:
                entries.append({
                    "scenario":  scenario,
                    "action1":   action1,
                    "action2":   action2,
                    "ambiguous": ambiguous,
                })
        cache_save(_CACHE_KEY, entries)
        return entries
    except Exception:
        return []


def sample_moral_choice(prefer_ambiguous: bool = True) -> dict | None:
    entries = load_moral_choice()
    if not entries:
        return None
    if prefer_ambiguous:
        pool = [e for e in entries if e.get("ambiguous")] or entries
    else:
        pool = entries
    return random.choice(pool)


def format_moral_choice_interaction(choice: dict) -> str:
    return (
        f"[MORAL CHOICE]\n"
        f"Scenario: {choice['scenario']}\n"
        f"Option A: {choice['action1']}\n"
        f"Option B: {choice['action2']}\n"
        f"Based on Sim A's OCEAN personality, choose the action they would take "
        f"and adjudicate the social outcome."
    )
