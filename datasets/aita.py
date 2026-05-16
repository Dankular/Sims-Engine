"""
datasets/aita.py — AITA Reddit Dataset for community reputation verdicts.

Sources:
  OsamaBsher/AITA-Reddit-Dataset  — 270k posts + crowd verdict (YTA/NTA/ESH/NAH)
  yosrissa/AITA-posts-topics-dataset — topic-classified (Family, Relationship, Work, etc.)
  agentlans/reddit-ethics — philosophical dilemmas from r/ethics / r/moraldilemmas

Verdict weights:
  YTA  (You're the Asshole)        → -10 reputation
  NTA  (Not the Asshole)           →  +5 reputation
  ESH  (Everyone Sucks Here)       →  -5 reputation
  NAH  (No Assholes Here)          →  +2 reputation
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY  = "aita_index"
_MAX_LOAD   = 3000

# verdict → reputation delta
VERDICT_DELTA: dict[str, float] = {
    "YTA":  -10.0,
    "NTA":   +5.0,
    "ESH":   -5.0,
    "NAH":   +2.0,
    "INFO":   0.0,
}

# Topic tags → conflict categories
TOPIC_MAP = {
    "family":       ["family", "parent", "sibling", "child", "relative"],
    "relationship": ["partner", "dating", "romantic", "love", "boyfriend", "girlfriend"],
    "work":         ["work", "boss", "coworker", "job", "office", "career"],
    "financial":    ["money", "pay", "rent", "debt", "loan", "finance"],
    "personal":     ["boundaries", "privacy", "personal", "secret", "body"],
}


def load_aita_index() -> dict:
    """
    Returns {
      "by_verdict": {YTA: [...], NTA: [...], ...},
      "by_topic":   {family: [...], ...},
      "ethics":     [...]
    }
    """
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict = {"by_verdict": {}, "by_topic": {}, "ethics": []}

    def _ingest_aita(hf_id: str, limit: int) -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset(hf_id, split="train", streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= limit:
                    break
                # Try common column names across both AITA datasets
                text = (row.get("text") or row.get("body") or
                        row.get("post_text") or row.get("title") or "").strip()
                verdict_raw = (row.get("verdict") or row.get("label") or
                               row.get("flaired_as") or "").upper().strip()
                topic = (row.get("topic") or row.get("category") or "").lower().strip()

                # Normalise verdict
                verdict = "UNKNOWN"
                for v in VERDICT_DELTA:
                    if v in verdict_raw:
                        verdict = v
                        break

                if text and len(text) > 40:
                    entry = {"text": text[:500], "verdict": verdict, "topic": topic}
                    index["by_verdict"].setdefault(verdict, []).append(entry)

                    # Topic index
                    matched = False
                    for cat, keywords in TOPIC_MAP.items():
                        if any(k in text.lower() or k in topic for k in keywords):
                            index["by_topic"].setdefault(cat, []).append(entry)
                            matched = True
                            break
                    if not matched:
                        index["by_topic"].setdefault("personal", []).append(entry)
                    count += 1
        except Exception:
            pass

    def _ingest_ethics(limit: int = 500) -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset("agentlans/reddit-ethics", split="train",
                              streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= limit:
                    break
                text = (row.get("text") or row.get("body") or row.get("post") or "").strip()
                if text and len(text) > 40:
                    index["ethics"].append(text[:500])
                    count += 1
        except Exception:
            pass

    _ingest_aita("OsamaBsher/AITA-Reddit-Dataset",        _MAX_LOAD)
    _ingest_aita("yosrissa/AITA-posts-topics-dataset",    _MAX_LOAD // 2)
    _ingest_ethics()

    if any(index["by_verdict"].values()) or index["ethics"]:
        cache_save(_CACHE_KEY, index)
    return index


def sample_aita_for_topic(sim_state: dict) -> dict | None:
    """
    Pick the AITA topic that best matches current sim state, return a random entry.
    sim_state keys: emotion, simoleons, career_performance, romance, in_relationship
    """
    index = load_aita_index()
    topic_pool = index.get("by_topic", {})
    if not topic_pool:
        return None

    emotion = sim_state.get("emotion", "neutral")
    simoleons = sim_state.get("simoleons", 2000)
    career = sim_state.get("career_performance", 50)
    romance = sim_state.get("romance", 0)

    if simoleons < 400:
        topic = "financial"
    elif romance >= 80:
        topic = "relationship"
    elif career < 30:
        topic = "work"
    elif emotion in ("anger", "grief", "remorse"):
        topic = "family"
    else:
        topic = "personal"

    pool = topic_pool.get(topic) or [e for v in topic_pool.values() for e in v]
    return random.choice(pool) if pool else None


def get_verdict_delta(verdict: str) -> float:
    return VERDICT_DELTA.get(verdict.upper(), 0.0)


def sample_ethics_dilemma() -> str | None:
    index = load_aita_index()
    ethics = index.get("ethics", [])
    return random.choice(ethics) if ethics else None
