"""
datasets/finance.py — Financial stress behavior modifier.

Source: bilalRahib/fiqa-personal-finance-dataset
        Personal finance Q&A from r/personalfinance — real questions about
        debt, job loss, financial planning, money stress.

When sim simoleons < LOW_FUNDS_THRESHOLD (300):
  - Interaction pool shifts toward money-anxious seeds
  - Social/fun decay faster
  - Household financial stress spreads to other sims' interactions
  - New life event type: financial_crisis

financial_crisis life event: triggered when simoleons < 150 (severe)
  → LLM generates job-loss / debt / eviction narrative
  → simoleon_delta_a large negative; emotional cascade via event2mind
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "finance_questions"
_HF_ID     = "bilalRahib/fiqa-personal-finance-dataset"
_MAX_LOAD  = 2000


def load_finance_questions() -> list[str]:
    """Returns list of financial stress question texts."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    questions: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(questions) >= _MAX_LOAD:
                break
            # Try multiple column patterns
            text = (row.get("question") or row.get("text") or
                    row.get("query") or row.get("input") or "").strip()
            if text and 20 < len(text) < 400:
                questions.append(text)
        cache_save(_CACHE_KEY, questions)
    except Exception:
        pass
    return questions


def sample_financial_stress_seed(simoleons: float) -> str | None:
    """Return a financially-anxious interaction seed appropriate to stress level."""
    questions = load_finance_questions()
    if not questions:
        return None
    # Worse stress → pick more desperate-sounding questions
    if simoleons < 150:
        keywords = ["evict", "homeless", "debt", "bankrupt", "fired", "survive", "broke"]
        urgent = [q for q in questions if any(k in q.lower() for k in keywords)]
        pool = urgent or questions
    elif simoleons < 300:
        pool = questions
    else:
        return None   # not financially stressed
    return random.choice(pool) if pool else None


def format_financial_seed(question: str) -> str:
    return (
        f"[FINANCIAL STRESS] Sim A voices their anxiety: \"{question[:250]}\"\n"
        f"This sim is financially stressed (low simoleons). "
        f"Their interaction is colored by money anxiety — adjust tone and social need restoration accordingly."
    )


def financial_crisis_context(simoleons: float, job: str) -> str:
    """Build context string for a financial_crisis life event."""
    severity = "severe" if simoleons < 150 else "moderate"
    questions = load_finance_questions()
    seed = random.choice(questions) if questions else f"{job} facing financial hardship."
    return (
        f"Financial crisis ({severity}, §{simoleons:.0f} remaining). "
        f"Background: \"{seed[:200]}\". "
        f"Generate a realistic financial crisis event with large negative simoleon_delta."
    )
