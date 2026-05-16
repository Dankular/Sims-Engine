"""
datasets/culture.py — Cultural identity and situational norms.

Sources:
  SALT-NLP/CultureBank — grounded cultural norm knowledge per cultural group
  SALT-NLP/NormBank    — role-aware situational norms (who can do what to whom)
  SALT-NLP/WORKBank    — workplace-specific cultural behaviors (office venue)

Each sim gets a cultural_background field at profile creation.
Cross-cultural interactions carry a friction modifier when norms conflict.
NormBank makes adjudication relationship-state-aware.
WORKBank grounds office venue / career event narration.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "culture_index"
_MAX_LOAD  = 2000

# Fallback cultural groups if dataset unavailable
_FALLBACK_GROUPS = [
    "American", "British", "Japanese", "Brazilian", "German",
    "Indian", "Nigerian", "French", "Korean", "Mexican",
    "Chinese", "Australian", "Russian", "Egyptian", "Canadian",
]

# Communication style clusters (cross-cultural friction)
_DIRECT_CULTURES    = {"American", "German", "Australian", "British", "Dutch"}
_HIGH_CONTEXT_CULTURES = {"Japanese", "Korean", "Chinese", "Indian", "Egyptian"}


def load_culture_index() -> dict:
    """
    Returns {
      "groups": [str],
      "norms": {group: [norm_text]},
      "situational": [{role_a, role_b, situation, norm}],
      "workplace": [norm_text]
    }
    """
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict = {"groups": list(_FALLBACK_GROUPS), "norms": {}, "situational": [], "workplace": []}

    def _ingest_culturebank() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("SALT-NLP/CultureBank", split="train",
                              streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= _MAX_LOAD:
                    break
                group = (row.get("cultural_group") or row.get("culture") or "").strip()
                norm  = (row.get("norm") or row.get("behavior") or row.get("text") or "").strip()
                if group and norm and len(norm) > 10:
                    if group not in index["groups"]:
                        index["groups"].append(group)
                    index["norms"].setdefault(group, []).append(norm[:200])
                    count += 1
        except Exception:
            pass

    def _ingest_normbank() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("SALT-NLP/NormBank", split="train",
                              streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= 1000:
                    break
                situation = (row.get("situation") or row.get("context") or "").strip()
                norm      = (row.get("norm") or row.get("behavior") or row.get("text") or "").strip()
                role_a    = (row.get("role_a") or row.get("actor") or "person").strip()
                role_b    = (row.get("role_b") or row.get("recipient") or "person").strip()
                if situation and norm:
                    index["situational"].append({
                        "role_a": role_a, "role_b": role_b,
                        "situation": situation[:200], "norm": norm[:200],
                    })
                    count += 1
        except Exception:
            pass

    def _ingest_workbank() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("SALT-NLP/WORKBank", split="train",
                              streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= 500:
                    break
                norm = (row.get("norm") or row.get("behavior") or row.get("text") or "").strip()
                if norm and len(norm) > 10:
                    index["workplace"].append(norm[:200])
                    count += 1
        except Exception:
            pass

    _ingest_culturebank()
    _ingest_normbank()
    _ingest_workbank()

    if index["groups"]:
        cache_save(_CACHE_KEY, index)
    return index


def sample_cultural_background() -> str:
    """Pick a cultural background for a new sim."""
    index = load_culture_index()
    groups = index.get("groups") or _FALLBACK_GROUPS
    return random.choice(groups)


def cross_cultural_friction(bg_a: str, bg_b: str) -> float:
    """
    Return a friction modifier 0.0-1.0 between two cultural backgrounds.
    0.0 = same culture / compatible; 1.0 = maximum friction.
    """
    if bg_a == bg_b:
        return 0.0
    a_direct = bg_a in _DIRECT_CULTURES
    b_direct = bg_b in _DIRECT_CULTURES
    a_high   = bg_a in _HIGH_CONTEXT_CULTURES
    b_high   = bg_b in _HIGH_CONTEXT_CULTURES
    if (a_direct and b_high) or (a_high and b_direct):
        return 0.3   # direct ↔ high-context: moderate friction
    return 0.1       # generic cross-cultural minor friction


def get_cultural_context(bg_a: str, bg_b: str, rel_state: str) -> str:
    """Build a cultural norm block for the adjudicator."""
    index = load_culture_index()
    parts: list[str] = []

    friction = cross_cultural_friction(bg_a, bg_b)
    if friction > 0:
        parts.append(
            f"Cultural context: {bg_a} ↔ {bg_b} "
            f"(friction={friction:.1f}; communication style mismatch possible)"
        )

    # Sample situational norm for this relationship state
    situational = index.get("situational", [])
    if situational:
        relevant = [s for s in situational if rel_state.lower() in s.get("situation", "").lower()]
        sample = random.choice(relevant or situational[:20])
        parts.append(f"Situational norm: {sample['norm']}")

    return "\n".join(parts) if parts else ""


def get_workplace_norm() -> str | None:
    index = load_culture_index()
    wp = index.get("workplace", [])
    return random.choice(wp) if wp else None
