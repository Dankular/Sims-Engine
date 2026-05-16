"""
datasets/daily_dialog.py — agentlans/li2017dailydialog loader.

13,118 dialogues across 10 life topics. Used to seed venue-appropriate
interaction utterances in the scheduler.

Topic IDs:
  0=ordinary life  1=school  2=culture  3=relationships  4=politics
  5=finance  6=health  7=tourism  8=work  9=attitude & emotion
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "daily_dialog_index"
_HF_ID     = "agentlans/li2017dailydialog"
_MAX_LOAD  = 5000

# venue name → DailyDialog topic id
VENUE_TOPIC_MAP: dict[str, int] = {
    "house party":  9,   # attitude & emotion
    "coffee shop":  0,   # ordinary life
    "park":         0,   # ordinary life
    "nightclub":    9,   # attitude & emotion
    "office":       8,   # work
    "home (1:1)":   3,   # relationships
    "gym":          6,   # health
    "library":      2,   # culture
}

TOPIC_NAMES = [
    "ordinary life", "school", "culture", "relationships",
    "politics", "finance", "health", "tourism", "work", "attitude & emotion",
]


def load_daily_dialog() -> dict[int, list[str]]:
    """Returns topic_id → [utterances] index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        # Keys come back as strings from JSON — convert to int
        return {int(k): v for k, v in cached.items()}

    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        index: dict[int, list[str]] = {i: [] for i in range(10)}
        count = 0
        for row in ds:
            if count >= _MAX_LOAD:
                break
            dialog  = row.get("dialog") or []
            topics  = row.get("topic") or []
            if not dialog:
                continue
            # topic is a list of ints per turn, or a single int
            if isinstance(topics, list) and topics:
                topic_id = int(topics[0])
            elif isinstance(topics, int):
                topic_id = topics
            else:
                topic_id = 0
            topic_id = max(0, min(9, topic_id))
            # Sample first user utterance from the dialog
            for turn in dialog[:3]:
                text = turn.strip() if isinstance(turn, str) else ""
                if 10 < len(text) < 160:
                    index[topic_id].append(text)
                    break
            count += 1
        cache_save(_CACHE_KEY, {str(k): v for k, v in index.items()})
        return index
    except Exception:
        return {i: [] for i in range(10)}


def sample_for_venue(venue_name: str) -> str | None:
    """Return a dialogue seed matching the current venue's topic."""
    index = load_daily_dialog()
    if not index:
        return None
    topic_id = VENUE_TOPIC_MAP.get(venue_name.lower(), 0)
    pool = index.get(topic_id, []) or index.get(0, [])
    return random.choice(pool) if pool else None
