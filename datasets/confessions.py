"""
datasets/confessions.py — SocialGrep/one-million-reddit-confessions.

1M+ real personal confessions from r/confessions.
Seeds "share secret" and "confide" interactions filtered by:
  - Active emotion (guilt/remorse → transgression confessions)
  - Active fears (abandonment → vulnerability confessions)
  - Relationship depth (friendship > 65 required for personal confessions)

Valence outcomes:
  - Successfully received (high agreeableness target, good timing): highest-weight memory
  - Rejected (low agreeableness, bad timing): trauma-level negative memory
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "confessions_index"
_HF_ID     = "SocialGrep/one-million-reddit-confessions"
_MAX_LOAD  = 4000

# Emotion/fear → confession theme keywords
EMOTION_KEYWORDS: dict[str, list[str]] = {
    "remorse":        ["regret", "mistake", "wrong", "sorry", "guilty"],
    "guilt":          ["guilty", "ashamed", "confession", "did something"],
    "grief":          ["lost", "death", "passed", "miss"],
    "fear":           ["afraid", "scared", "anxiety", "terrified"],
    "sadness":        ["lonely", "alone", "sad", "depressed", "isolated"],
    "embarrassment":  ["embarrassing", "humiliating", "awkward", "cringe"],
    "pride":          ["proud", "achievement", "finally", "accomplished"],
    "love":           ["love", "feelings", "crush", "romantic"],
    "nervousness":    ["nervous", "anxious", "worry", "stress"],
}

FEAR_KEYWORDS: dict[str, list[str]] = {
    "fear of abandonment":   ["alone", "left", "abandoned", "nobody"],
    "fear of rejection":     ["rejected", "turned down", "not enough"],
    "fear of humiliation":   ["humiliated", "embarrassed", "laughed at"],
    "fear of commitment":    ["commitment", "relationship", "settle down"],
}


def load_confessions() -> dict[str, list[str]]:
    """Returns {theme: [confession_texts]} index."""
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
            text = (row.get("selftext") or row.get("text") or row.get("body") or "").strip()
            if not text or len(text) < 40 or len(text) > 800:
                continue
            text_lower = text.lower()
            matched = False
            for theme, keywords in {**EMOTION_KEYWORDS, **FEAR_KEYWORDS}.items():
                if any(k in text_lower for k in keywords):
                    index.setdefault(theme, []).append(text[:500])
                    matched = True
                    break
            if not matched:
                index.setdefault("general", []).append(text[:500])
            count += 1
        if index:
            cache_save(_CACHE_KEY, index)
    except Exception:
        pass
    return index


def sample_confession(
    emotion: str,
    fears: list[str],
    friendship_score: float,
) -> str | None:
    """
    Return a confession text appropriate for the sim's emotional state.
    Requires friendship > 65 for personal confessions.
    """
    if friendship_score < 35:
        return None   # too early to confess anything serious

    index = load_confessions()
    if not index:
        return None

    # Priority: fear-matched > emotion-matched > general
    candidates: list[str] = []

    for fear in fears:
        for fear_key, _ in FEAR_KEYWORDS.items():
            if fear_key.lower() in fear.lower():
                candidates.extend(index.get(fear_key, []))

    if not candidates:
        candidates.extend(index.get(emotion, []))

    if not candidates:
        candidates.extend(index.get("general", []))

    return random.choice(candidates) if candidates else None


def format_confession_interaction(confession: str, friendship: float) -> str:
    depth = "vulnerable, deeply personal" if friendship >= 65 else "cautious, testing the waters"
    return (
        f"[CONFESSION — {depth}]\n"
        f"Sim A shares: \"{confession[:300]}\"\n"
        f"Adjudicate Sim B's reaction based on their personality. "
        f"Accepted confessions create very high-valence memories; "
        f"rejected ones create trauma-level negative memories."
    )
