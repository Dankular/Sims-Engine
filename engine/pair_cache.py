"""
engine/pair_cache.py — Pair feature cache for interaction scheduling.

Caches expensive pair-level scores (attraction, social risk, NLI weights)
so pick_interaction_pair() doesn't recompute on every tick.

Invalidation strategy:
  - Per-pair invalidation on relationship delta (bump_pair).
  - Per-sim invalidation on trait/skill change (bump_sim).
  - Version-counter global invalidation (bump_version) for mass resets.
  - TTL fallback so stale entries don't persist indefinitely.
"""
from __future__ import annotations

import time
from typing import Any

_DEFAULT_TTL = 8.0  # seconds


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


class PairFeatureCache:
    """
    Version-stamped, TTL-backed cache for pair scoring results.

    Usage in scheduler::
        score = cache.get(a_id, b_id)
        if score is None:
            score = expensive_compute(a, b)
            cache.set(a_id, b_id, score)
    """

    def __init__(self, ttl: float = _DEFAULT_TTL) -> None:
        self.ttl = ttl
        # key → (value, timestamp, version_at_write)
        self._store: dict[tuple[str, str], tuple[Any, float, int]] = {}
        self._version: int = 0

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, a_id: str, b_id: str) -> Any | None:
        entry = self._store.get(_key(a_id, b_id))
        if entry is None:
            return None
        value, ts, ver = entry
        if ver != self._version:
            return None
        if time.monotonic() - ts > self.ttl:
            return None
        return value

    # ── Write ─────────────────────────────────────────────────────────────────

    def set(self, a_id: str, b_id: str, value: Any) -> None:
        self._store[_key(a_id, b_id)] = (value, time.monotonic(), self._version)

    # ── Invalidation ──────────────────────────────────────────────────────────

    def bump_pair(self, a_id: str, b_id: str) -> None:
        """Invalidate a specific pair (call after relationship delta)."""
        self._store.pop(_key(a_id, b_id), None)

    def bump_sim(self, sim_id: str) -> None:
        """Invalidate all pairs involving sim_id (call on trait/skill change)."""
        stale = [k for k in self._store if sim_id in k]
        for k in stale:
            del self._store[k]

    def bump_version(self) -> None:
        """Global invalidation — increments version counter, all entries expire."""
        self._version += 1

    def clear(self) -> None:
        self._store.clear()

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._store)

    def stats(self) -> dict[str, int | float]:
        return {"entries": self.size, "version": self._version, "ttl": self.ttl}
