"""
datasets/character_voices.py — Rowan/character-dialogues.

Character-specific voice patterns across many fictional personalities.
Used to sample "how does this OCEAN/MBTI profile actually phrase things"
as few-shot examples in chat.py and the adjudicator.

Keyed by personality tags (trait words) so we can find voices close to
a sim's actual traits.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "character_voices"
_HF_ID     = "Rowan/character-dialogues"
_MAX_LOAD  = 3000

# Trait/MBTI words mapped to personality descriptor tags we might find
_TRAIT_ALIASES: dict[str, list[str]] = {
    "bookworm":       ["intellectual", "scholar", "reader", "academic"],
    "outgoing":       ["extrovert", "social", "friendly", "bubbly"],
    "loner":          ["introvert", "quiet", "reserved", "solitary"],
    "hot-headed":     ["fiery", "passionate", "impulsive", "volatile"],
    "romantic":       ["romantic", "dreamy", "hopeful", "tender"],
    "ambitious":      ["ambitious", "driven", "goal-oriented", "determined"],
    "creative":       ["creative", "artistic", "imaginative", "expressive"],
    "gloomy":         ["melancholy", "brooding", "pessimistic", "somber"],
    "cheerful":       ["cheerful", "optimistic", "bright", "sunny"],
    "evil":           ["cunning", "manipulative", "scheming", "dark"],
    "family-oriented": ["nurturing", "caring", "protective", "devoted"],
    "geek":           ["nerdy", "geeky", "technical", "analytical"],
    "foodie":         ["foodie", "culinary", "gastronomic", "epicurean"],
}


def load_character_voices() -> dict[str, list[dict]]:
    """Returns {personality_tag: [{character, line}]}."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[dict]] = {}
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= _MAX_LOAD:
                break
            character   = (row.get("character") or row.get("speaker") or "").strip()
            line        = (row.get("line") or row.get("utterance") or
                           row.get("text") or "").strip()
            personality = (row.get("personality") or row.get("description") or
                           character).lower()

            if not line or len(line) < 10 or len(line) > 250:
                continue

            # Index by matching trait aliases
            matched = False
            for trait, aliases in _TRAIT_ALIASES.items():
                if any(a in personality for a in aliases):
                    index.setdefault(trait, []).append({
                        "character": character, "line": line[:200]
                    })
                    matched = True
            if not matched:
                index.setdefault("general", []).append({
                    "character": character, "line": line[:200]
                })
            count += 1
        cache_save(_CACHE_KEY, index)
    except Exception:
        pass
    return index


def get_voice_examples(traits: list[str], n: int = 3) -> list[str]:
    """
    Return dialogue lines from characters with matching personality traits.
    Used as few-shot examples in chat.py to show natural voice patterns.
    """
    index = load_character_voices()
    if not index:
        return []
    candidates: list[dict] = []
    for trait in traits:
        candidates.extend(index.get(trait, []))
    if not candidates:
        candidates = [e for v in index.values() for e in v]
    picks = random.sample(candidates, min(n, len(candidates)))
    return [f"\"{p['line']}\"" for p in picks]
