"""
datasets/hippocorpus.py — allenai/hippocorpus narrative texture dataset.

6,854 short diary-like stories: recalled, imagined, and retold.
Used by StoryRunner to choose narrative style matching memory valence:
  - High valence / positive → "recalled" style (linear, clear, vivid)
  - Low valence / traumatic  → "retold" style (fragmented, hedged, compressed)
  - Imagined futures         → "imagined" style (conditional, aspirational)

Each entry also carries author openness-to-experience score.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "hippocorpus_index"
_HF_ID     = "allenai/hippocorpus"
_MAX_LOAD  = 2000


def load_hippocorpus() -> dict[str, list[dict]]:
    """Returns {recalled: [...], imagined: [...], retold: [...]}."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[dict]] = {"recalled": [], "imagined": [], "retold": []}
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= _MAX_LOAD:
                break
            story    = (row.get("story") or row.get("text") or "").strip()
            memtype  = (row.get("memType") or row.get("type") or "recalled").lower()
            openness = float(row.get("openness") or row.get("author_openness") or 0.5)

            if not story or len(story) < 50:
                continue

            # Normalise type
            if "retold" in memtype or "summary" in memtype:
                key = "retold"
            elif "imagin" in memtype:
                key = "imagined"
            else:
                key = "recalled"

            index[key].append({
                "story":    story[:600],
                "openness": round(openness, 2),
            })
            count += 1
        cache_save(_CACHE_KEY, index)
    except Exception:
        pass
    return index


def get_narrative_style(valence: float) -> str:
    """Choose narrative style based on interaction valence."""
    if valence >= 0.6:
        return "recalled"
    if valence <= -0.4:
        return "retold"
    return "imagined"


def sample_narrative_scaffold(valence: float, sim_openness: float = 0.5) -> str | None:
    """
    Return a short narrative excerpt to use as stylistic scaffolding.
    Picks entries closest in openness to the sim, with style matched to valence.
    """
    index = load_hippocorpus()
    style = get_narrative_style(valence)
    pool  = index.get(style, [])
    if not pool:
        return None
    # Sort by openness proximity, sample top 10
    pool_sorted = sorted(pool, key=lambda e: abs(e["openness"] - sim_openness))
    candidates  = pool_sorted[:10]
    entry = random.choice(candidates)
    return entry["story"]


def get_memory_drift_note(valence: float) -> str:
    """Return a storytelling instruction reflecting how memory type affects narration."""
    style = get_narrative_style(valence)
    if style == "recalled":
        return "Narrate as a vivid, clear, present-tense memory with specific sensory detail."
    if style == "retold":
        return "Narrate as a fragmented, slightly hedged recollection — emotions clearer than facts."
    return "Narrate as an imagined or hoped-for scenario, conditional and aspirational in tone."
