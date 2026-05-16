"""
datasets/event2mind.py — uwnlp/event2Mind loader.

Maps events → emotional reactions (xReact, oReact, xWant).
Emotion-first complement to ATOMIC 2020.
Used during life events to trigger secondary emotional cascades.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "event2mind_index"
_HF_ID     = "uwnlp/event2Mind"
_MAX_LOAD  = 6000


def load_event2mind() -> dict[str, list[dict]]:
    """Returns keyword → [event2mind rows] index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        index: dict[str, list[dict]] = {}
        count = 0
        for row in ds:
            if count >= _MAX_LOAD:
                break
            event = (row.get("event") or "").strip().lower()
            x_react = row.get("xReact") or []
            o_react = row.get("oReact") or []
            x_want  = row.get("xWant")  or []
            if not event:
                continue
            # Filter out "none" values
            entry = {
                "event":   event,
                "xReact":  [r for r in (x_react if isinstance(x_react, list) else [x_react]) if r and r != "none"],
                "oReact":  [r for r in (o_react if isinstance(o_react, list) else [o_react]) if r and r != "none"],
                "xWant":   [r for r in (x_want  if isinstance(x_want,  list) else [x_want])  if r and r != "none"],
            }
            if not any([entry["xReact"], entry["oReact"], entry["xWant"]]):
                continue
            # Index by keywords
            for word in event.replace("personx", "").replace("persony", "").split():
                word = word.strip(".,!?\"'")
                if len(word) > 3:
                    index.setdefault(word, []).append(entry)
            count += 1
        cache_save(_CACHE_KEY, index)
        return index
    except Exception:
        return {}


def query_event2mind(event_text: str) -> dict | None:
    """
    Query the index for an event description.
    Returns a random matching entry or None.
    """
    index = load_event2mind()
    if not index:
        return None
    candidates = [
        entry
        for word in event_text.lower().split()
        if word in index
        for entry in index[word][:3]
    ]
    return random.choice(candidates) if candidates else None


def emotional_cascade(event_text: str) -> dict:
    """
    Return secondary emotional context for a life event.
    {
      "xReact": ["shocked", "devastated"],
      "oReact": ["concerned"],
      "xWant":  ["to be alone", "support from friends"]
    }
    """
    entry = query_event2mind(event_text)
    if not entry:
        return {"xReact": [], "oReact": [], "xWant": []}
    return {
        "xReact": entry.get("xReact", [])[:3],
        "oReact": entry.get("oReact", [])[:3],
        "xWant":  entry.get("xWant", [])[:3],
    }
