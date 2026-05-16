"""
datasets/intimacy.py — Partner-state attachment-aware interaction seeds.

Source: AI-companionship/INTIMA
"""

from __future__ import annotations

import random

from datasets.cache import cache_load, cache_save

_CACHE_KEY = "intima_codes"


def load_intima() -> dict[str, list[str]]:
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    buckets: dict[str, list[str]] = {
        "secure": [],
        "anxious": [],
        "avoidant": [],
        "general": [],
    }
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "AI-companionship/INTIMA",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if sum(len(v) for v in buckets.values()) >= 900:
                break
            text = " ".join(
                str(row.get(k, "")).strip()
                for k in ["prompt", "text", "utterance", "example", "instruction"]
            ).strip()
            if len(text) < 20:
                continue
            code = str(
                row.get("code") or row.get("label") or row.get("category") or ""
            ).lower()
            if any(x in code for x in ["secure", "trust", "stability"]):
                buckets["secure"].append(text[:320])
            elif any(x in code for x in ["anx", "jealous", "longing", "dependency"]):
                buckets["anxious"].append(text[:320])
            elif any(x in code for x in ["avoid", "distance", "withdraw"]):
                buckets["avoidant"].append(text[:320])
            else:
                buckets["general"].append(text[:320])
    except Exception:
        pass

    if any(buckets.values()):
        cache_save(_CACHE_KEY, buckets)
    return buckets


def sample_intima(attachment: str) -> str | None:
    idx = load_intima()
    key = attachment.lower().strip()
    pool = idx.get(key, []) or idx.get("general", [])
    return random.choice(pool) if pool else None
