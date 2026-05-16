"""
datasets/reconciliation.py — Post-toxic-cycle repair interactions.

Source: nbertagnolli/counsel-chat — high-quality counselor-patient exchanges,
        empathetic repair-oriented responses to relationship damage.

Reconciliation unlocks when:
  in_toxic_cycle == False
  AND relationship was previously in toxic cycle (toxic_cycle_phase != "none" stored)
  AND friendship 20-50 (damaged but not destroyed)

Outcomes:
  Success: removes toxic cycle flag, partial friendship restore, "resilient" trait flag
  Failure: drops to rivals tier permanently
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "counsel_chat"
_HF_ID     = "nbertagnolli/counsel-chat"
_MAX_LOAD  = 1500

RECONCILIATION_FRIENDSHIP_MIN = 20
RECONCILIATION_FRIENDSHIP_MAX = 50


def load_counsel_chat() -> list[dict]:
    """Returns list of {question, response} repair-oriented exchanges."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    entries: list[dict] = []
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(entries) >= _MAX_LOAD:
                break
            question = (row.get("questionText") or row.get("question") or
                        row.get("input") or "").strip()
            response = (row.get("answerText") or row.get("answer") or
                        row.get("response") or "").strip()
            if question and response and len(response) > 30:
                entries.append({
                    "question": question[:200],
                    "response": response[:400],
                })
        cache_save(_CACHE_KEY, entries)
    except Exception:
        pass
    return entries


def sample_counsel_exchange() -> dict | None:
    entries = load_counsel_chat()
    return random.choice(entries) if entries else None


def format_reconciliation_interaction(exchange: dict | None, sim_a_name: str,
                                       sim_b_name: str, friendship: float) -> str:
    counsel_note = ""
    if exchange:
        counsel_note = (
            f"Therapeutic reference:\n"
            f"  Context: \"{exchange['question'][:150]}\"\n"
            f"  Repair approach: \"{exchange['response'][:200]}\"\n"
        )
    return (
        f"[RECONCILIATION — post-toxic relationship repair]\n"
        f"{sim_a_name} attempts to repair their damaged relationship with {sim_b_name}. "
        f"Current friendship: {friendship:.0f}. Both carry wounds from past manipulation.\n"
        f"{counsel_note}"
        f"Successful reconciliation: partial friendship restore, toxic_cycle_phase cleared, "
        f"target Sim gains 'resilient' trait marker. "
        f"Failed: friendship drops to rivals tier (-45) permanently."
    )


def apply_reconciliation_outcome(rel, sim_b, valence: float) -> str:
    """Apply outcome of a reconciliation attempt. Returns outcome label."""
    if valence >= 0.5:
        rel.in_toxic_cycle = False
        rel.toxic_cycle_phase = "none"
        rel.apply_deltas(15.0, 0)
        # Grant resilient marker
        if "resilient" not in sim_b.profile.get("traits", []):
            sim_b.profile.setdefault("traits", []).append("resilient")
        return "success"
    else:
        # Drop to rivals
        rel.friendship = max(rel.friendship, -50.0)
        rel.in_toxic_cycle = False
        rel.toxic_cycle_phase = "none"
        return "failure"
