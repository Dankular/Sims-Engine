"""
datasets/romance.py — Romance-tiered interaction seeds.

Sources:
  shirshatzman/flirtflip-dataset  -> gentle/playful/bold flirt transforms
  the-rizz/the-rizz-corpus        -> high-chemistry opening lines
"""

from __future__ import annotations

import random

from datasets.cache import cache_load, cache_save

_FLIRTFIP_CACHE_KEY = "flirtflip_index"
_RIZZ_CACHE_KEY = "rizz_corpus"


def romance_tier(romance: float) -> str:
    if romance < 30:
        return "gentle"
    if romance < 55:
        return "playful"
    return "bold"


def load_flirtflip() -> dict[str, list[str]]:
    cached = cache_load(_FLIRTFIP_CACHE_KEY)
    if cached:
        return cached

    tiers: dict[str, list[str]] = {"gentle": [], "playful": [], "bold": []}
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "shirshatzman/flirtflip-dataset",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if sum(len(v) for v in tiers.values()) >= 1400:
                break
            text = " ".join(
                str(row.get(k, "")).strip()
                for k in ["output", "response", "flirt", "text", "transformation"]
            ).strip()
            if len(text) < 12:
                continue
            style = str(
                row.get("style") or row.get("tier") or row.get("label") or ""
            ).lower()
            if "gent" in style:
                tiers["gentle"].append(text[:260])
            elif "play" in style:
                tiers["playful"].append(text[:260])
            elif "bold" in style:
                tiers["bold"].append(text[:260])
            else:
                t = text.lower()
                if any(x in t for x in ["coffee", "smile", "nice", "sweet"]):
                    tiers["gentle"].append(text[:260])
                elif any(x in t for x in ["tease", "wink", "banter", "playful"]):
                    tiers["playful"].append(text[:260])
                else:
                    tiers["bold"].append(text[:260])
    except Exception:
        pass

    if any(tiers.values()):
        cache_save(_FLIRTFIP_CACHE_KEY, tiers)
    return tiers


def sample_flirt_line(romance: float) -> tuple[str | None, str]:
    tiers = load_flirtflip()
    tier = romance_tier(romance)
    pool = tiers.get(tier, [])
    if not pool and tier == "bold":
        pool = tiers.get("playful", [])
    if not pool and tier == "playful":
        pool = tiers.get("gentle", [])
    return (random.choice(pool) if pool else None, tier)


def load_rizz_corpus() -> list[str]:
    cached = cache_load(_RIZZ_CACHE_KEY)
    if cached:
        return cached
    lines: list[str] = []
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "the-rizz/the-rizz-corpus",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if len(lines) >= 1200:
                break
            text = " ".join(
                str(row.get(k, "")).strip()
                for k in ["text", "line", "opening", "response", "rizz"]
            ).strip()
            if 10 <= len(text) <= 220:
                lines.append(text)
    except Exception:
        pass
    if lines:
        cache_save(_RIZZ_CACHE_KEY, lines)
    return lines


def sample_rizz_intro() -> str | None:
    corpus = load_rizz_corpus()
    return random.choice(corpus) if corpus else None
