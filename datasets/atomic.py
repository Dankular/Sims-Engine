"""
datasets/atomic.py — ATOMIC commonsense inference.

Primary path (System 7): allenai/comet-distil generates causal completions
from free text — "PersonX goes to work [sep] xReact" → "PersonX feels tired".
Fallback: keyword index lookup from the pre-loaded ATOMIC dataset cache.
"""
from __future__ import annotations


def load_atomic_index() -> dict:
    from datasets.cache import cache_load
    return cache_load("atomic_index") or {}


def _atomic_index_lookup(interaction: str) -> str | None:
    """Legacy keyword-index fallback."""
    import random
    index = load_atomic_index()
    if not index:
        return None
    candidates = [
        entry
        for w in interaction.lower().split()
        if w in index
        for entry in index[w][:3]
    ]
    if not candidates:
        return None
    item = random.choice(candidates)
    return item.get("event") if isinstance(item, dict) else None


def query_atomic(interaction: str) -> str | None:
    """
    Return a commonsense inference string for the given interaction.
    Tries COMET generation first; falls back to index lookup.
    """
    # System 7: COMET causal inference
    try:
        from llm.small_models import comet_infer
        results = comet_infer(interaction, ["xReact", "xWant", "oReact"])
        if results:
            parts = []
            if "xReact" in results:
                parts.append(f"A feels: {results['xReact']}")
            if "xWant" in results:
                parts.append(f"A wants: {results['xWant']}")
            if "oReact" in results:
                parts.append(f"B reacts: {results['oReact']}")
            if parts:
                return "; ".join(parts)
    except Exception:
        pass

    # Fallback: keyword index lookup
    return _atomic_index_lookup(interaction)
