"""
datasets/travel.py — Travel interest content.

Sources:
  soniawmeyer/travel-conversations-finetuning  — destination experiences, cultural obs
  bitext/Bitext-travel-llm-chatbot-training-dataset — 4M+ token backup pool

Seeds travel-themed interactions when sim has "travel" interest.
Two "travel" interest sims → strongest interest-match bonding conversation.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "travel_content"
_MAX_LOAD  = 2000


def load_travel_content() -> list[str]:
    """Returns list of travel conversation texts."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    texts: list[str] = []

    def _ingest(hf_id: str, limit: int) -> None:
        try:
            from datasets import load_dataset
            ds = load_dataset(hf_id, split="train", streaming=True, trust_remote_code=True)
            count = 0
            for row in ds:
                if count >= limit:
                    break
                for col in ["text", "conversation", "input", "prompt", "instruction", "question"]:
                    val = row.get(col, "")
                    if val and isinstance(val, str) and 20 < len(val) < 400:
                        texts.append(val.strip())
                        count += 1
                        break
        except Exception:
            pass

    _ingest("soniawmeyer/travel-conversations-finetuning", _MAX_LOAD)
    if len(texts) < 200:   # backup if primary is small
        _ingest("bitext/Bitext-travel-llm-chatbot-training-dataset", _MAX_LOAD // 2)

    if texts:
        cache_save(_CACHE_KEY, texts)
    return texts


def sample_travel_seed(both_have_travel: bool = False) -> str | None:
    texts = load_travel_content()
    if not texts:
        return None
    return random.choice(texts)


def format_travel_interaction(seed: str, both_interested: bool) -> str:
    bond = "both share a passion for travel — this is a high-affinity bonding topic." \
           if both_interested else "Sim A is passionate about travel."
    return (
        f"[TRAVEL CONVERSATION — {bond}]\n"
        f"Seed: \"{seed[:300]}\"\n"
        f"Adjudicate based on openness and shared interest. "
        f"High openness target → enthusiastic engagement; low openness → polite disinterest."
    )
