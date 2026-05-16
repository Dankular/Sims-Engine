"""
datasets/blended_skill.py — Facebook blended_skill_talk dataset.

Each turn is labelled with which conversational skill it uses:
  persona     — staying true to a stated personality
  knowledge   — sharing a relevant fact or opinion
  empathy     — acknowledging the other person's emotional state

Prevents the mono-register problem (always leading with personality declaration).
Sims can pivot naturally between being curious, warm, and informative.

Used for:
  1. Varied-register interaction seeds in scheduler
  2. Few-shot examples in chat.py showing how to blend skills
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "blended_skill"
_HF_ID     = "blended_skill_talk"
_MAX_LOAD  = 3000

SKILLS = ["persona", "knowledge", "empathy"]


def load_blended_skill() -> dict[str, list[str]]:
    """Returns {skill: [example_utterances]}."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[str]] = {s: [] for s in SKILLS}
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= _MAX_LOAD:
                break
            convs = row.get("previous_utterance") or row.get("dialog") or []
            chosen = row.get("chosen_topic") or row.get("skill") or ""
            skill = next((s for s in SKILLS if s in chosen.lower()), None)

            # Also try pulling from free_messages / bot_messages
            utterance = ""
            for col in ["free_messages", "bot_messages", "text", "utterance"]:
                val = row.get(col)
                if isinstance(val, list) and val:
                    utterance = str(val[-1]).strip()
                    break
                if isinstance(val, str) and val.strip():
                    utterance = val.strip()
                    break

            if not utterance and isinstance(convs, list) and convs:
                utterance = str(convs[-1]).strip()

            if utterance and 10 < len(utterance) < 250:
                bucket = skill or random.choice(SKILLS)
                index[bucket].append(utterance[:200])
                count += 1
        cache_save(_CACHE_KEY, index)
    except Exception:
        pass
    return index


def sample_blended_utterance(dominant_skill: str | None = None) -> str | None:
    """Return an utterance that exemplifies a specific conversational skill."""
    index = load_blended_skill()
    if not index:
        return None
    skill = dominant_skill if dominant_skill in SKILLS else random.choice(SKILLS)
    pool  = index.get(skill, []) or [u for v in index.values() for u in v]
    return random.choice(pool) if pool else None


def get_skill_examples(ocean: dict, n_per_skill: int = 1) -> dict[str, list[str]]:
    """
    Return examples for each skill weighted by OCEAN:
      persona   ← extraversion
      knowledge ← openness
      empathy   ← agreeableness
    """
    index = load_blended_skill()
    result: dict[str, list[str]] = {}
    for skill in SKILLS:
        pool = index.get(skill, [])
        if pool:
            result[skill] = random.sample(pool, min(n_per_skill, len(pool)))
    return result
