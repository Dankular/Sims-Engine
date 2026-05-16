"""
datasets/persona_chat.py — nazlicanto/persona-based-chat.

64,258 persona-consistent multi-turn conversations.
Used as few-shot examples in the adjudicator system prompt to anchor
the LLM to a Sim's voice across many interactions.
Sampled by OCEAN profile similarity to find matching persona examples.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "persona_chat"
_HF_ID     = "nazlicanto/persona-based-chat"
_MAX_LOAD  = 800

# OCEAN trait → persona keywords to match against
_OCEAN_PERSONA_HINTS: dict[str, list[str]] = {
    "high_openness":        ["creative", "curious", "art", "ideas", "explore"],
    "high_conscientiousness": ["organized", "responsible", "plan", "work", "goal"],
    "high_extraversion":    ["social", "party", "friends", "talkative", "outgoing"],
    "high_agreeableness":   ["kind", "help", "care", "friendly", "warm"],
    "high_neuroticism":     ["anxious", "worry", "stress", "nervous", "sensitive"],
}


def load_persona_chat() -> list[dict]:
    """Returns list of {persona: str, sample_exchange: str} dicts."""
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
            # Column names vary — try common patterns
            persona = (
                row.get("persona") or row.get("personality") or
                row.get("speaker_persona") or ""
            )
            if isinstance(persona, list):
                persona = " ".join(str(p) for p in persona)
            persona = str(persona).strip()

            conv = row.get("conversation") or row.get("dialog") or row.get("utterances") or []
            sample = ""
            if isinstance(conv, list) and len(conv) >= 2:
                a = conv[0] if isinstance(conv[0], str) else conv[0].get("text", "")
                b = conv[1] if isinstance(conv[1], str) else conv[1].get("text", "")
                if a and b:
                    sample = f'"{a.strip()}" / "{b.strip()}"'

            if persona and sample:
                entries.append({"persona": persona, "sample": sample})

        cache_save(_CACHE_KEY, entries)
        return entries
    except Exception:
        return []


def get_persona_examples(ocean: dict, n: int = 2) -> list[str]:
    """
    Return n persona-consistent sample exchanges that match a Sim's OCEAN profile.
    Used as few-shot context in the adjudicator system prompt.
    """
    entries = load_persona_chat()
    if not entries:
        return []

    # Build search keywords from dominant OCEAN traits
    keywords: list[str] = []
    if ocean.get("openness", 0) > 0.65:
        keywords.extend(_OCEAN_PERSONA_HINTS["high_openness"])
    if ocean.get("conscientiousness", 0) > 0.65:
        keywords.extend(_OCEAN_PERSONA_HINTS["high_conscientiousness"])
    if ocean.get("extraversion", 0) > 0.65:
        keywords.extend(_OCEAN_PERSONA_HINTS["high_extraversion"])
    if ocean.get("agreeableness", 0) > 0.65:
        keywords.extend(_OCEAN_PERSONA_HINTS["high_agreeableness"])
    if ocean.get("neuroticism", 0) > 0.65:
        keywords.extend(_OCEAN_PERSONA_HINTS["high_neuroticism"])

    if keywords:
        kw_lower = [k.lower() for k in keywords]
        scored = [
            (sum(kw in e["persona"].lower() for kw in kw_lower), e)
            for e in entries
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        pool = [e for _, e in scored[:100]]
    else:
        pool = entries

    picks = random.sample(pool, min(n, len(pool)))
    return [f'Example exchange: {p["sample"]}' for p in picks]
