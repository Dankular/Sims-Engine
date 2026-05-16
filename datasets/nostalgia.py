"""
datasets/nostalgia.py — Reminisce interaction type grounding.

Sources:
  stanfordnlp/mutual_friends — 11K human-human dialogues discovering shared connections
  michellejieli/friends_dataset — TV scenes with long-term friendship callbacks

Reminisce unlocks when: friendship >= 65 AND shared memory count >= 5.
Two close friends look back at highest-valence shared memories.
Successful: highest friendship delta in engine (nostalgia bonding).
Failed (misremember): "that's not how it happened" → rivals risk.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "nostalgia_templates"
_MAX_LOAD  = 800

REMINISCE_FRIENDSHIP_MIN = 65
REMINISCE_MEMORY_MIN     = 5


def load_nostalgia_templates() -> list[str]:
    """Returns list of reminiscing/mutual-friend discovery utterances."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    templates: list[str] = []

    def _ingest_mutual_friends() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("stanfordnlp/mutual_friends", split="train",
                              streaming=True, trust_remote_code=True)
            for row in ds:
                if len(templates) >= _MAX_LOAD:
                    break
                for col in ["text", "utterance", "dialogue", "conversation"]:
                    val = row.get(col)
                    if isinstance(val, list):
                        for turn in val:
                            t = (turn.get("text", turn) if isinstance(turn, dict)
                                 else str(turn)).strip()
                            if 15 < len(t) < 200:
                                templates.append(t)
                    elif val and isinstance(val, str) and 15 < len(val) < 200:
                        templates.append(val.strip())
                    break
        except Exception:
            pass

    _ingest_mutual_friends()

    if templates:
        cache_save(_CACHE_KEY, templates)
    return templates


def sample_reminisce_template() -> str | None:
    templates = load_nostalgia_templates()
    return random.choice(templates) if templates else None


def format_reminisce_interaction(
    template: str | None,
    shared_memories: list[dict],
) -> str:
    top_memories = sorted(shared_memories, key=lambda m: abs(m.get("valence", 0)),
                          reverse=True)[:3]
    mem_text = "\n".join(
        f"  • \"{m['tag']}\" (valence={m.get('valence', 0):+.2f})"
        for m in top_memories
    )
    template_note = f"Tone reference: \"{template[:150]}\"\n" if template else ""
    return (
        f"[REMINISCE — long-term friendship callback]\n"
        f"{template_note}"
        f"Shared memories being recalled:\n{mem_text}\n"
        f"Successful reminiscing → highest friendship delta (nostalgia bonding). "
        f"If one sim misremembers a negative memory ('that's not how it happened') "
        f"→ rupture risk, rivals tier possible."
    )
