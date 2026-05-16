"""
datasets/social_bias.py — allenai/social_bias_frames.

~150k statements annotated for implied bias, stereotyping, offensiveness.
Used to detect when an interaction crosses a social norm line and escalate
to a conflict event. Also seeds in-group preference dynamics in clique formation.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "social_bias_norms"
_HF_ID     = "allenai/social_bias_frames"
_MAX_LOAD  = 2000


def load_social_bias_norms() -> list[dict]:
    """
    Returns a list of {statement, offensiveness, stereotype, target_group} dicts.
    Filtered to only include entries with clear offensiveness or stereotype signals.
    """
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        entries: list[dict] = []
        for row in ds:
            if len(entries) >= _MAX_LOAD:
                break
            stmt    = (row.get("post") or row.get("statement") or "").strip()
            off     = float(row.get("offensiveYN") or row.get("offensiveness") or 0)
            stereo  = float(row.get("hasBiasedImplication") or row.get("stereotype") or 0)
            target  = (row.get("targetMinority") or row.get("target_group") or "").strip()
            # Only keep entries with meaningful signal
            if stmt and (off > 0.5 or stereo > 0.5):
                entries.append({
                    "statement":      stmt,
                    "offensiveness":  round(off, 2),
                    "stereotype":     round(stereo, 2),
                    "target_group":   target,
                })
        cache_save(_CACHE_KEY, entries)
        return entries
    except Exception:
        return []


def is_potentially_offensive(text: str, threshold: float = 0.6) -> bool:
    """
    Heuristic check: does the interaction text match patterns from high-offensiveness entries?
    Used by the adjudicator to detect norm violations and escalate to conflict.
    """
    norms = load_social_bias_norms()
    if not norms:
        return False
    text_lower = text.lower()
    offensive = [e for e in norms if e["offensiveness"] >= threshold]
    sample = random.sample(offensive, min(20, len(offensive)))
    for entry in sample:
        # Simple word overlap heuristic
        stmt_words = set(entry["statement"].lower().split())
        text_words = set(text_lower.split())
        overlap = len(stmt_words & text_words) / max(1, len(stmt_words))
        if overlap > 0.4:
            return True
    return False


def sample_conflict_escalation_context() -> str | None:
    """Return a social norm framing to inject when a conflict interaction is detected."""
    norms = load_social_bias_norms()
    if not norms:
        return None
    high_off = [e for e in norms if e["offensiveness"] > 0.7]
    if not high_off:
        return None
    pick = random.choice(high_off)
    return (
        f"Social norm alert: This interaction touches on sensitive territory "
        f"(offensiveness={pick['offensiveness']:.1f}). "
        f"Apply heightened scrutiny to the social consequences."
    )
