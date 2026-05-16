"""
datasets/mental_chat.py — ShenLab/MentalChat16K + Amod/mental_health_counseling_conversations.

9,775 counselor-client conversations across 33 mental health topics.
Used to seed deep_support interactions when a Sim has active fears
and a trusted friend (friendship > 65) initiates support.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY  = "mental_chat_index"
_HF_PRIMARY = "ShenLab/MentalChat16K"
_HF_BACKUP  = "Amod/mental_health_counseling_conversations"
_MAX_LOAD   = 2000

# Fear labels → mental health topics for targeted sampling
FEAR_TOPIC_MAP: dict[str, list[str]] = {
    "fear of rejection":     ["relationships", "loneliness", "self-worth"],
    "fear of abandonment":   ["relationships", "anxiety", "family conflict"],
    "fear of humiliation":   ["self-worth", "anxiety", "anger management"],
    "fear of commitment":    ["relationships", "intimacy"],
    "fear of crowds":        ["anxiety", "depression"],
}


def load_mental_chat() -> dict[str, list[str]]:
    """Returns topic → [counselor opening lines] index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[str]] = {}

    def _try_load(hf_id: str) -> bool:
        try:
            from datasets import load_dataset
            ds = load_dataset(hf_id, split="train", streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= _MAX_LOAD:
                    break
                # Try common column names
                topic    = (row.get("topic") or row.get("category") or "general").lower().strip()
                # Get counselor's first response
                conv     = row.get("conversations") or row.get("dialog") or []
                response = ""
                if isinstance(conv, list):
                    for turn in conv:
                        if isinstance(turn, dict):
                            role = turn.get("role", turn.get("from", "")).lower()
                            if "counselor" in role or "assistant" in role or "therapist" in role:
                                response = turn.get("content", turn.get("value", "")).strip()
                                break
                        elif isinstance(turn, str) and len(turn) > 20:
                            response = turn.strip()
                            break
                if response and 20 < len(response) < 300:
                    index.setdefault(topic, []).append(response)
                    count += 1
            return count > 0
        except Exception:
            return False

    if not _try_load(_HF_PRIMARY):
        _try_load(_HF_BACKUP)

    if index:
        cache_save(_CACHE_KEY, index)
    return index


def sample_support_line(fears: list[str], topic: str | None = None) -> str | None:
    """Sample a counselor opening line relevant to a Sim's active fears."""
    index = load_mental_chat()
    if not index:
        return None

    # Build candidate topics from fear labels
    candidate_topics: list[str] = []
    for fear in fears:
        candidate_topics.extend(FEAR_TOPIC_MAP.get(fear.lower(), []))
    if topic:
        candidate_topics.append(topic.lower())

    # Try specific topics first, fall back to any
    for t in candidate_topics:
        for key, lines in index.items():
            if t in key and lines:
                return random.choice(lines)

    # Fallback: any line
    all_lines = [line for lines in index.values() for line in lines]
    return random.choice(all_lines) if all_lines else None
