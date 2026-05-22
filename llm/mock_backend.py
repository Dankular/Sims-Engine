"""
llm/mock_backend.py — Fast deterministic mock adjudicator for headless observation runs.

Returns plausible-looking responses without calling a real LLM.
Valence and deltas are drawn from per-category Beta distributions so the
pattern miner sees statistically meaningful spread across action types.
"""
from __future__ import annotations

import json
import random
import re

# Per-category (lo, hi) valence range — shapes the Beta draw
_CATEGORY_VALENCE: dict[str, tuple[float, float]] = {
    "friendly":     (0.30, 0.75),
    "funny":        (0.40, 0.90),
    "mean":         (-0.75, 0.10),
    "romantic":     (0.40, 0.90),
    "intimate":     (0.50, 0.90),
    "deep":         (0.20, 0.80),
    "support":      (0.40, 0.80),
    "intellectual": (0.30, 0.80),
    "activity":     (0.40, 0.80),
    "nostalgic":    (0.50, 0.90),
    "repair":       (0.25, 0.70),
    "toxic":        (-0.85, -0.10),
    "practical":    (0.25, 0.60),
    "discovery":    (0.35, 0.70),
}

_EMOTION_BANDS: list[tuple[float, list[str]]] = [
    (0.70, ["joy", "excitement", "admiration", "gratitude", "pride"]),
    (0.35, ["content", "optimism", "caring", "amusement", "relief"]),
    (0.05, ["neutral", "surprise", "curiosity", "realization"]),
    (-0.35, ["disappointment", "annoyance", "nervousness", "confusion"]),
    (-1.0, ["anger", "disgust", "sadness", "fear", "remorse"]),
]

# Prefix tags → category
_TAG_CAT: dict[str, str] = {
    "[TEASE]":          "funny",
    "[ROMANCE":         "romantic",
    "[SELF-DISCLOSURE": "deep",
    "[INTIMATE":        "intimate",
    "[PARTNERS":        "intimate",
    "[NSFW":            "intimate",
    "[MEMORY":          "nostalgic",
    "[DISCOVERY]":      "discovery",
    "[HEALTH CONCERN]": "practical",
    "[DEEP SUPPORT]":   "support",
    "[COMMUNITY":       "deep",
    "[EMOTIONAL":       "support",
    "[CREATIVE":        "deep",
    "[RECONCILIATION":  "repair",
}


def _infer_category(interaction: str) -> str:
    upper = interaction.upper()
    for tag, cat in _TAG_CAT.items():
        if tag.upper() in upper:
            return cat
    try:
        from config import INTERACTION_TYPES
        for cat, actions in INTERACTION_TYPES.items():
            if interaction.strip().lower() in [a.lower() for a in actions]:
                return cat
    except Exception:
        pass
    return "friendly"


def _sample_valence(category: str) -> float:
    lo, hi = _CATEGORY_VALENCE.get(category, (0.20, 0.65))
    # Beta(2,2) gives a unimodal distribution, scaled to [lo, hi]
    raw = random.betavariate(2, 2)
    return round(lo + raw * (hi - lo), 3)


def _emotion_for(valence: float) -> str:
    for threshold, labels in _EMOTION_BANDS:
        if valence >= threshold:
            return random.choice(labels)
    return "sadness"


def mock_adjudicate(interaction: str) -> dict:
    """
    Standalone deterministic adjudication — same logic as MockLLMBackend.chat()
    but callable directly as a fallback without constructing a backend object.
    Used by call_adjudicator() when the LLM times out.
    """
    cat = _infer_category(interaction)
    v = _sample_valence(cat)
    fd = round(v * random.uniform(1.5, 4.0) + random.gauss(0, 0.3), 2)
    rd_scale = 2.5 if cat in ("romantic", "intimate") else 0.5
    rd = round(v * random.uniform(0.3, rd_scale) + random.gauss(0, 0.15), 2)
    emo_a = _emotion_for(v)
    emo_b = _emotion_for(v * random.uniform(0.6, 1.0))
    reaction_verbs = {
        "joy": "laughs warmly", "excitement": "lights up",
        "admiration": "smiles with appreciation", "content": "nods with a small smile",
        "neutral": "listens quietly", "disappointment": "looks down briefly",
        "annoyance": "sighs", "anger": "tenses visibly", "sadness": "looks away",
    }
    reaction = f"{reaction_verbs.get(emo_b, 'responds in kind')} and seems {emo_b}."
    return {
        "dialogue": f"[deterministic fallback — {cat}]",
        "sim_b_reaction": reaction,
        "friendship_delta": fd,
        "romance_delta": rd,
        "social_need_restore_a": round(max(0, v * 8 + random.gauss(0, 1)), 1),
        "social_need_restore_b": round(max(0, v * 6 + random.gauss(0, 1)), 1),
        "fun_restore_a": round(max(0, v * 5 + random.gauss(0, 1)), 1),
        "fun_restore_b": round(max(0, v * 4 + random.gauss(0, 1)), 1),
        "emotion_a": emo_a,
        "emotion_b": emo_b,
        "valence": v,
        "memory_tag": interaction[:60],
        "charisma_xp_a": round(max(0, random.uniform(0, 0.3)), 2),
        "comedy_xp_a": round(max(0, random.uniform(0, 0.2) if cat == "funny" else 0), 2),
        "reasoning": f"[deterministic] {cat} valence={v:.2f}",
    }


class MockLLMBackend:
    """Implements LLMBackend protocol without calling a real model."""

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.7,
        schema: dict | None = None,
    ) -> str:
        # Extract interaction from the user message heuristically
        interaction = ""
        for line in user.splitlines():
            if line.strip().startswith("Interaction:"):
                interaction = line.split(":", 1)[1].strip()
                break
        if not interaction:
            # Try last non-empty line
            lines = [l.strip() for l in user.splitlines() if l.strip()]
            interaction = lines[-1] if lines else "chat"

        cat = _infer_category(interaction)
        v = _sample_valence(cat)

        # Scale deltas from valence; add small noise
        fd = round(v * random.uniform(1.5, 4.0) + random.gauss(0, 0.3), 2)
        rd_scale = 2.5 if cat in ("romantic", "intimate") else 0.5
        rd = round(v * random.uniform(0.3, rd_scale) + random.gauss(0, 0.15), 2)

        emo_a = _emotion_for(v)
        emo_b = _emotion_for(v * random.uniform(0.6, 1.0))

        reaction_verbs = {
            "joy": "laughs warmly",
            "excitement": "lights up",
            "admiration": "smiles with appreciation",
            "content": "nods with a small smile",
            "neutral": "listens quietly",
            "disappointment": "looks down briefly",
            "annoyance": "sighs",
            "anger": "tenses visibly",
            "sadness": "looks away",
        }
        reaction = f"{reaction_verbs.get(emo_b, 'responds in kind')} and seems {emo_b}."

        payload = {
            "sim_b_reaction": reaction,
            "friendship_delta": fd,
            "romance_delta": rd,
            "social_need_restore_a": round(max(0, v * 8 + random.gauss(0, 1)), 1),
            "social_need_restore_b": round(max(0, v * 6 + random.gauss(0, 1)), 1),
            "fun_restore_a": round(max(0, v * 5 + random.gauss(0, 1)), 1),
            "fun_restore_b": round(max(0, v * 4 + random.gauss(0, 1)), 1),
            "emotion_a": emo_a,
            "emotion_b": emo_b,
            "valence": v,
            "memory_tag": interaction[:60],
            "charisma_xp_a": round(max(0, random.uniform(0, 0.3)), 2),
            "comedy_xp_a": round(max(0, random.uniform(0, 0.2) if cat == "funny" else 0), 2),
            "reasoning": f"[mock] {cat} interaction, valence={v:.2f}",
        }
        return json.dumps(payload)
