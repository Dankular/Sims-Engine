"""Self-disclosure depth mapping for confession timing."""

from __future__ import annotations

import random

from datasets.cache import cache_load, cache_save

_CACHE_KEY = "self_disclosure_depth"


def load_self_disclosure() -> dict[str, list[str]]:
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached
    out: dict[str, list[str]] = {"surface": [], "mid": [], "deep": []}
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "douy/reddit-self-disclosure",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if sum(len(v) for v in out.values()) >= 2400:
                break
            text = str(
                row.get("text") or row.get("post") or row.get("body") or ""
            ).strip()
            depth = str(
                row.get("depth") or row.get("label") or row.get("tier") or ""
            ).lower()
            if len(text) < 20:
                continue
            if "surface" in depth:
                out["surface"].append(text[:350])
            elif depth in {"mid", "middle"} or "opinion" in depth or "feeling" in depth:
                out["mid"].append(text[:350])
            elif "deep" in depth or "secret" in depth or "vulnerab" in depth:
                out["deep"].append(text[:350])
    except Exception:
        pass
    if any(out.values()):
        cache_save(_CACHE_KEY, out)
    return out


def depth_for_friendship(friendship: float) -> str:
    if friendship >= 65:
        return "deep"
    if friendship >= 45:
        return "mid"
    return "surface"


def sample_by_depth(friendship: float) -> tuple[str | None, str]:
    idx = load_self_disclosure()
    depth = depth_for_friendship(friendship)
    pool = idx.get(depth, [])
    if not pool and depth == "deep":
        pool = idx.get("mid", [])
    if not pool and depth in {"deep", "mid"}:
        pool = idx.get("surface", [])
    return (random.choice(pool) if pool else None, depth)
