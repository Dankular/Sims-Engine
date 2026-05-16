"""
datasets/ccpe.py — google-research-datasets/ccpe-m
Coached Conversational Preference Elicitation (movies domain).

One person discovers the other's preferences organically through questions —
without interrogating them. Models natural curiosity and turn-taking.

Used in chat.py to teach the sim to ask follow-up questions rather than
monologuing. Seeds "discovery" conversation patterns in the scheduler.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "ccpe_turns"
_HF_ID     = "google-research-datasets/ccpe-m"
_MAX_LOAD  = 2000


def load_ccpe() -> list[dict]:
    """Returns list of {seeker_turn, assistant_turn} discovery exchanges."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    turns: list[dict] = []
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(turns) >= _MAX_LOAD:
                break
            utts = row.get("utterances") or []
            if not isinstance(utts, list) or len(utts) < 2:
                continue
            # Grab question/answer pairs from the conversation
            for i in range(0, len(utts) - 1, 2):
                q = utts[i]
                a = utts[i + 1]
                q_text = (q.get("text", q) if isinstance(q, dict) else str(q)).strip()
                a_text = (a.get("text", a) if isinstance(a, dict) else str(a)).strip()
                if q_text and a_text and 5 < len(q_text) < 150 and 5 < len(a_text) < 200:
                    turns.append({"question": q_text, "answer": a_text})
                    if len(turns) >= _MAX_LOAD:
                        break
        cache_save(_CACHE_KEY, turns)
    except Exception:
        pass
    return turns


def sample_discovery_exchange() -> dict | None:
    """Return a question/answer pair showing natural preference discovery."""
    turns = load_ccpe()
    return random.choice(turns) if turns else None


def get_discovery_examples(n: int = 2) -> list[str]:
    """
    Return n examples of natural discovery questions for few-shot injection.
    Shows the sim how to ask about the player rather than monologue.
    """
    turns = load_ccpe()
    if not turns:
        return []
    picks = random.sample(turns, min(n, len(turns)))
    return [f"Q: \"{p['question']}\" → A: \"{p['answer'][:100]}\"" for p in picks]
