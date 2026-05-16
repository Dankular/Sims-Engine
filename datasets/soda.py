"""
datasets/soda.py — allenai/soda Social Dialogue dataset.

1.5M multi-turn dialogues grounded in ATOMIC commonsense knowledge graph.
Each entry has: narrative (situation), speakers, dialogue turns.
Characters show personality through action and word choice — not declaration.

Used for:
  1. Naturalistic conversation seeds in scheduler (replaces flat ConvAI2 seeds)
  2. Few-shot dialogue texture in chat.py system prompt
  3. Show-don't-tell dialogue examples keyed by emotion/situation
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "soda_index"
_HF_ID     = "allenai/soda"
_MAX_LOAD  = 4000


def load_soda() -> dict[str, list[dict]]:
    """
    Returns {situation_tag: [{narrative, speakers, exchange}]} where
    exchange is the first 2-3 turns as a short dialogue sample.
    """
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
            narrative = (row.get("narrative") or row.get("context") or "").strip()
            dialogue  = row.get("dialogue") or row.get("turns") or []
            speakers  = row.get("speakers") or []

            if not dialogue or len(dialogue) < 2:
                continue

            # Build a short exchange (first 3 turns)
            lines: list[str] = []
            for i, turn in enumerate(dialogue[:3]):
                spk = speakers[i] if i < len(speakers) else f"Person{i+1}"
                text = (turn if isinstance(turn, str)
                        else turn.get("text", "")).strip()
                if text:
                    lines.append(f"{spk}: {text[:120]}")

            if not lines:
                continue

            # Tag by first word of narrative for loose indexing
            tag = narrative.lower().split()[0] if narrative else "general"
            index.setdefault(tag, []).append({
                "narrative": narrative[:200],
                "exchange":  "\n".join(lines),
            })
            count += 1
        cache_save(_CACHE_KEY, index)
    except Exception:
        pass
    return index


def sample_soda_seed(emotion: str | None = None) -> str | None:
    """Return a short naturalistic dialogue exchange as a conversation seed."""
    index = load_soda()
    if not index:
        return None
    all_entries = [e for entries in index.values() for e in entries]
    if not all_entries:
        return None
    entry = random.choice(all_entries)
    return entry["exchange"]


def sample_soda_example(n: int = 2) -> list[str]:
    """Return n short dialogue exchanges for few-shot injection."""
    index = load_soda()
    all_entries = [e for entries in index.values() for e in entries]
    picks = random.sample(all_entries, min(n, len(all_entries)))
    return [p["exchange"] for p in picks]
