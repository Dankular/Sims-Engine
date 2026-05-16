"""
datasets/loneliness.py — Loneliness arc dataset grounding.

Sources:
  FIG-Loneliness/FIG-Loneliness         — 5,633 Reddit posts, annotated with
    loneliness duration, context, relationship type (friends/family/romance/community)
  yael-katsman/Loneliness-Causes-and-Intensity — intensity 1-5 scale + causes

Maps _social_drought_ticks → intensity tier → seeded dialogue + emotion.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "loneliness_index"
_MAX_LOAD  = 2000

# drought ticks → intensity tier
def drought_to_intensity(ticks: int) -> int:
    if ticks >= 20: return 5
    if ticks >= 14: return 4
    if ticks >= 10: return 3
    if ticks >= 8:  return 2
    return 1


def load_loneliness_index() -> dict[str, list[str]]:
    """Returns {intensity_tier: [post_texts]} and {cause: [texts]}."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[str]] = {
        "1": [], "2": [], "3": [], "4": [], "5": [],
        "romantic": [], "friends": [], "family": [], "community": [],
    }

    def _ingest_fig() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("FIG-Loneliness/FIG-Loneliness", split="train",
                              streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= _MAX_LOAD:
                    break
                text     = (row.get("text") or row.get("selftext") or row.get("post") or "").strip()
                rel_type = (row.get("relationship_type") or row.get("context") or "").lower()
                duration = str(row.get("duration") or row.get("intensity") or "2")
                if not text or len(text) < 20:
                    continue
                tier = duration[:1] if duration[:1].isdigit() else "2"
                index.setdefault(tier, []).append(text[:300])
                for rt in ["romantic", "friends", "family", "community"]:
                    if rt in rel_type:
                        index[rt].append(text[:300])
                count += 1
        except Exception:
            pass

    def _ingest_causes() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("yael-katsman/Loneliness-Causes-and-Intensity", split="train",
                              streaming=True, trust_remote_code=True)
            for row in ds:
                text      = (row.get("text") or row.get("cause") or row.get("description") or "").strip()
                intensity = str(row.get("intensity") or row.get("label") or "2")
                if text and len(text) > 15:
                    tier = intensity[:1] if intensity[:1].isdigit() else "2"
                    index.setdefault(tier, []).append(text[:300])
        except Exception:
            pass

    _ingest_fig()
    _ingest_causes()

    if any(v for v in index.values()):
        cache_save(_CACHE_KEY, index)
    return index


def sample_loneliness_seed(drought_ticks: int, context: str = "") -> str | None:
    index = load_loneliness_index()
    if not index:
        return None
    intensity = str(drought_to_intensity(drought_ticks))
    pool = index.get(intensity, [])
    if not pool:
        pool = [t for v in index.values() for t in v]
    return random.choice(pool) if pool else None


def format_loneliness_interaction(seed: str, drought_ticks: int) -> str:
    intensity = drought_to_intensity(drought_ticks)
    level = ["", "mild", "noticeable", "significant", "severe", "profound"][intensity]
    return (
        f"[LONELINESS — {level}, {drought_ticks} ticks without social contact]\n"
        f"\"{seed[:280]}\"\n"
        f"This sim is experiencing social withdrawal. They are now reaching out. "
        f"Their need to connect is genuine and urgent — weight the social restoration higher than normal."
    )
