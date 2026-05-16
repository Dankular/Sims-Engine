"""
datasets/jokes.py — Comedy skill-gated joke content.

Sources:
  Fraser/short-jokes     — 231,657 jokes (10-200 chars): puns, wordplay, dark, absurdist
  shuttie/reddit-dadjokes — 147,753 dad jokes with setup + punchline

Comedy skill gates quality:
  Lvl 1-3:  short simple puns / weak jokes  (len < 80)
  Lvl 4-6:  moderate one-liners             (len 60-140)
  Lvl 7-10: sharp, complex jokes            (len > 80, filtered for quality markers)

Dad jokes specifically used for "tell great joke" interaction:
  Setup fed to adjudicator, punchline revealed, reaction scored vs target humor type.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY_JOKES    = "jokes_by_tier"
_CACHE_KEY_DADJOKES = "dadjokes"
_MAX_JOKES    = 8000
_MAX_DADJOKES = 3000


def load_jokes() -> dict[str, list[str]]:
    """Returns {"low": [...], "mid": [...], "high": [...]} tiered by complexity."""
    cached = cache_load(_CACHE_KEY_JOKES)
    if cached:
        return cached

    tiers: dict[str, list[str]] = {"low": [], "mid": [], "high": []}
    try:
        from datasets import load_dataset
        ds = load_dataset("Fraser/short-jokes", split="train",
                          streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= _MAX_JOKES:
                break
            joke = (row.get("Joke") or row.get("text") or row.get("joke") or "").strip()
            if not joke or len(joke) < 10:
                continue
            n = len(joke)
            if n < 80:
                tiers["low"].append(joke)
            elif n < 140:
                tiers["mid"].append(joke)
            else:
                tiers["high"].append(joke)
            count += 1
        cache_save(_CACHE_KEY_JOKES, tiers)
    except Exception:
        pass
    return tiers


def load_dadjokes() -> list[dict]:
    """Returns list of {setup, punchline} dicts."""
    cached = cache_load(_CACHE_KEY_DADJOKES)
    if cached:
        return cached

    jokes: list[dict] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("shuttie/reddit-dadjokes", split="train",
                          streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= _MAX_DADJOKES:
                break
            setup     = (row.get("setup") or row.get("question") or "").strip()
            punchline = (row.get("punchline") or row.get("answer") or "").strip()
            if setup and punchline:
                jokes.append({"setup": setup, "punchline": punchline})
                count += 1
        cache_save(_CACHE_KEY_DADJOKES, jokes)
    except Exception:
        pass
    return jokes


def sample_joke_for_skill(comedy_skill: float) -> str | None:
    """Return a joke appropriate for the sim's comedy skill level."""
    tiers = load_jokes()
    if comedy_skill >= 7:
        pool = tiers.get("high", []) or tiers.get("mid", [])
    elif comedy_skill >= 4:
        pool = tiers.get("mid", []) or tiers.get("low", [])
    else:
        pool = tiers.get("low", [])
    return random.choice(pool) if pool else None


def sample_dadjoke() -> dict | None:
    """Return a random dad joke {setup, punchline}."""
    jokes = load_dadjokes()
    return random.choice(jokes) if jokes else None


def format_joke_interaction(joke: str, comedy_skill: float) -> str:
    """Format a joke as an interaction string for the adjudicator."""
    quality = "sharp, well-timed" if comedy_skill >= 7 else \
              "decent" if comedy_skill >= 4 else "mediocre"
    return f"[JOKE — {quality} delivery] \"{joke}\""


def format_dadjoke_interaction(dadjoke: dict) -> str:
    return (
        f"[DAD JOKE] Setup: \"{dadjoke['setup']}\" "
        f"Punchline: \"{dadjoke['punchline']}\""
    )
