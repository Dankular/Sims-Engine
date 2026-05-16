"""
datasets/fitness.py — Fitness skill grounded content.

Sources:
  its-myrto/fitness-question-answers       — 965 Q&A pairs, natural language
  chibbss/fitness-chat-prompt-completion   — conversational fitness chat (dialogue-native)

Fitness skill tiers:
  Lvl 1-3: beginner struggles (soreness, motivation, starting out)
  Lvl 4-6: intermediate training (plateau, consistency, technique)
  Lvl 7-10: advanced (race prep, peaking, marathon, elite habits)

Mentor dynamic: high-fitness sim talking to low-fitness target →
  adjudicator frames it as teaching/inspiring.
Interest match: target with "fitness" interest → enthusiasm;
  "foodie" or sedentary interests → disinterest.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY  = "fitness_content"
_MAX_QA     = 800
_MAX_CHAT   = 500

_BEGINNER_KEYWORDS  = ["start", "beginner", "sore", "motivat", "first time", "how do i", "new to"]
_ADVANCED_KEYWORDS  = ["marathon", "plateau", "peak", "elite", "race", "pr", "personal record", "advanced"]


def load_fitness_content() -> dict[str, list[dict]]:
    """Returns {tier: [{text, source}]} index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    tiers: dict[str, list[dict]] = {"beginner": [], "intermediate": [], "advanced": []}

    def _classify(text: str) -> str:
        t = text.lower()
        if any(k in t for k in _ADVANCED_KEYWORDS):
            return "advanced"
        if any(k in t for k in _BEGINNER_KEYWORDS):
            return "beginner"
        return "intermediate"

    def _ingest(hf_id: str, q_col: str, a_col: str, limit: int) -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset(hf_id, split="train", streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= limit:
                    break
                q = (row.get(q_col) or row.get("prompt") or row.get("input") or "").strip()
                a = (row.get(a_col) or row.get("completion") or row.get("output") or "").strip()
                if q and a and len(q) > 10:
                    text = f"Q: {q}\nA: {a[:300]}"
                    tier = _classify(q + " " + a)
                    tiers[tier].append({"text": text, "source": hf_id})
                    count += 1
        except Exception:
            pass

    _ingest("its-myrto/fitness-question-answers",
            "question", "answer", _MAX_QA)
    _ingest("chibbss/fitness-chat-prompt-completion-dataset",
            "prompt", "completion", _MAX_CHAT)

    if any(tiers.values()):
        cache_save(_CACHE_KEY, tiers)
    return tiers


def sample_fitness_content(fitness_skill: float) -> dict | None:
    content = load_fitness_content()
    if fitness_skill >= 7:
        pool = content.get("advanced", []) or content.get("intermediate", [])
    elif fitness_skill >= 4:
        pool = content.get("intermediate", []) or content.get("beginner", [])
    else:
        pool = content.get("beginner", [])
    return random.choice(pool) if pool else None


def format_fitness_interaction(item: dict, fitness_skill_a: float,
                                fitness_skill_b: float) -> str:
    skill_gap = fitness_skill_a - fitness_skill_b
    dynamic = ""
    if skill_gap >= 3:
        dynamic = " Sim A is significantly more experienced — frame as mentoring."
    elif skill_gap <= -3:
        dynamic = " Sim B is significantly more experienced — Sim A may feel inspired or intimidated."
    else:
        dynamic = " Similar skill levels — peer bonding dynamic."

    return (
        f"[FITNESS INTERACTION — skill {fitness_skill_a:.0f}/10]\n"
        f"{item['text'][:400]}\n"
        f"{dynamic}"
    )
