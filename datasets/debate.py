"""
datasets/debate.py — Grounded debate content for Logic skill interactions.

Sources:
  ibm-research/argument_quality_ranking_30k  — 30k arguments, 71 topics, quality 0-1
  webis/args_me                               — 382k arguments, broad topic spread

Logic skill gates argument quality tier:
  Lvl 1-2: bottom quartile (quality < 0.35) — weak, meandering
  Lvl 3-5: mid tier (0.35-0.65)            — decent, structured
  Lvl 6+:  top quartile (quality > 0.65)   — sharp, persuasive
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "debate_index"
_MAX_IBM   = 5000
_MAX_ARGS  = 3000


def load_debate_index() -> dict[str, list[dict]]:
    """Returns {topic: [{argument, quality, stance}]} index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[dict]] = {}

    def _ingest_ibm() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("ibm-research/argument_quality_ranking_30k",
                              split="train", streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= _MAX_IBM:
                    break
                topic    = (row.get("topic") or row.get("motion") or "general").strip()
                argument = (row.get("argument") or row.get("text") or "").strip()
                quality  = float(row.get("WA") or row.get("quality") or row.get("score") or 0.5)
                stance   = (row.get("stance") or row.get("label") or "pro").lower()
                if argument and len(argument) > 20:
                    index.setdefault(topic, []).append({
                        "argument": argument[:400],
                        "quality":  round(quality, 2),
                        "stance":   stance,
                        "source":   "ibm",
                    })
                    count += 1
        except Exception:
            pass

    def _ingest_argsme() -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("webis/args_me", split="train",
                              streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= _MAX_ARGS:
                    break
                topic    = (row.get("topic") or row.get("conclusion") or "general").strip()
                argument = (row.get("premises") or row.get("argument") or row.get("text") or "")
                if isinstance(argument, list):
                    argument = " ".join(str(a) for a in argument)
                argument = str(argument).strip()
                stance   = (row.get("stance") or "pro").lower()
                if argument and len(argument) > 20:
                    index.setdefault(topic, []).append({
                        "argument": argument[:400],
                        "quality":  0.5,  # no quality score in args_me
                        "stance":   stance,
                        "source":   "argsme",
                    })
                    count += 1
        except Exception:
            pass

    _ingest_ibm()
    _ingest_argsme()

    if index:
        cache_save(_CACHE_KEY, index)
    return index


def sample_debate_argument(logic_skill: float, topic_hint: str | None = None) -> dict | None:
    """Return an argument appropriate for the sim's logic skill level."""
    index = load_debate_index()
    if not index:
        return None

    # Quality tier from skill
    if logic_skill >= 6:
        q_min, q_max = 0.65, 1.0
    elif logic_skill >= 3:
        q_min, q_max = 0.35, 0.65
    else:
        q_min, q_max = 0.0, 0.35

    # Pick topic pool
    all_args = [a for args in index.values() for a in args]
    if topic_hint:
        hint_lower = topic_hint.lower()
        topic_args = [a for t, args in index.items()
                      if any(w in t.lower() for w in hint_lower.split())
                      for a in args]
        pool = topic_args or all_args
    else:
        pool = all_args

    # Filter by quality tier
    tier = [a for a in pool if q_min <= a["quality"] <= q_max]
    pool = tier or pool   # fallback if tier empty
    return random.choice(pool) if pool else None


def format_debate_interaction(argument: dict, logic_skill: float) -> str:
    quality_label = ("sharp and persuasive" if logic_skill >= 6
                     else "reasonably structured" if logic_skill >= 3
                     else "weak and meandering")
    return (
        f"[DEBATE — {quality_label} argument]\n"
        f"Topic: {argument.get('topic', 'this issue')}\n"
        f"Sim A argues ({argument['stance']}): \"{argument['argument'][:300]}\"\n"
        f"Argument quality: {argument['quality']:.2f}/1.0. "
        f"Adjudicate the reaction based on Sim B's logic skill, agreeableness, and the argument quality."
    )
