"""
identity/mbti.py — MBTI personality type inference.

Primary:  deterministic OCEAN → MBTI mapping (always available, instant)
Optional: theta/MBTI-ckiplab-bert text-based inference (loaded on demand)

OCEAN → MBTI mapping (established correlations):
  E/I  ← extraversion    > 0.5 → E, else I
  N/S  ← openness        > 0.5 → N, else S
  T/F  ← agreeableness   > 0.5 → F, else T   (high agreeableness = Feeling)
  J/P  ← conscientiousness > 0.5 → J, else P

Each type gets a descriptor injected into the adjudicator prompt so the LLM
can reason about systematic compatibility / friction between types.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Type descriptors for adjudicator prompt ────────────────────────────────────
MBTI_DESCRIPTORS: dict[str, str] = {
    "INTJ": "analytical, private, strategic, strong opinions, values competence",
    "INTP": "logical, curious, reserved, theoretical, detached from emotions",
    "ENTJ": "decisive, dominant, goal-driven, direct, natural leader",
    "ENTP": "innovative, argumentative, quick-witted, idea-focused",
    "INFJ": "empathetic, idealistic, private, deeply values meaning",
    "INFP": "idealistic, sensitive, loyal, creative, values authenticity",
    "ENFJ": "charismatic, empathetic, people-focused, inspiring",
    "ENFP": "enthusiastic, creative, social, values connection and possibility",
    "ISTJ": "responsible, detail-oriented, traditional, reliable",
    "ISFJ": "loyal, caring, practical, tradition-focused, dislikes conflict",
    "ESTJ": "organised, rule-following, decisive, takes charge",
    "ESFJ": "warm, social, duty-bound, harmony-seeking",
    "ISTP": "pragmatic, reserved, logical, hands-on problem solver",
    "ISFP": "gentle, artistic, present-focused, avoids confrontation",
    "ESTP": "action-oriented, bold, direct, lives in the moment",
    "ESFP": "spontaneous, fun-loving, social, emotionally expressive",
}

# Classic compatibility pairs — used to compute friction/affinity bonuses
NATURAL_PAIRS = {
    ("INTJ", "ENFP"), ("INTP", "ENTJ"), ("INFJ", "ENTP"), ("INFP", "ENTJ"),
    ("ISTJ", "ESFP"), ("ISFJ", "ESTP"), ("ESTJ", "INFP"), ("ESFJ", "ISTP"),
}


def ocean_to_mbti(ocean: dict) -> str:
    """Convert OCEAN scores to MBTI type using established correlations."""
    e_or_i = "E" if ocean.get("extraversion", 0.5) >= 0.5 else "I"
    n_or_s = "N" if ocean.get("openness", 0.5) >= 0.5 else "S"
    t_or_f = "F" if ocean.get("agreeableness", 0.5) >= 0.5 else "T"
    j_or_p = "J" if ocean.get("conscientiousness", 0.5) >= 0.5 else "P"
    return f"{e_or_i}{n_or_s}{t_or_f}{j_or_p}"


def mbti_descriptor(mbti: str) -> str:
    return MBTI_DESCRIPTORS.get(mbti, "")


def mbti_compatibility(type_a: str, type_b: str) -> float:
    """
    Return a float 0-1 representing natural compatibility.
    1.0 = classic pair, 0.5 = neutral, 0.2 = potential friction.
    """
    pair = tuple(sorted([type_a, type_b]))
    if any(set(pair) == set(p) for p in NATURAL_PAIRS):
        return 1.0
    # Same on 3 dimensions = good
    matches = sum(a == b for a, b in zip(type_a, type_b))
    return round(0.2 + matches * 0.2, 1)


# ── Optional: text-based MBTI inference ───────────────────────────────────────
_mbti_pipeline = None
_mbti_load_attempted = False


def infer_from_text(text: str) -> str | None:
    """
    Attempt to infer MBTI from text using theta/MBTI-ckiplab-bert.
    Returns None if model unavailable.
    """
    global _mbti_pipeline, _mbti_load_attempted
    if _mbti_load_attempted:
        if _mbti_pipeline is None:
            return None
    else:
        _mbti_load_attempted = True
        import os
        import huggingface_hub.constants as _hf_const
        _offline_vars = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
        _saved_env = {k: os.environ.pop(k, None) for k in _offline_vars}
        _saved_flag = _hf_const.HF_HUB_OFFLINE
        _hf_const.HF_HUB_OFFLINE = False
        try:
            from transformers import pipeline as _hf_pipeline
            from config import HF_MBTI_MODEL
            _mbti_pipeline = _hf_pipeline(
                "text-classification", model=HF_MBTI_MODEL, truncation=True, max_length=512
            )
            logger.info("MBTI model loaded: %s", HF_MBTI_MODEL)
        except Exception as exc:
            logger.debug("MBTI model unavailable (%s) — using OCEAN→MBTI mapping", exc)
            _mbti_pipeline = None
        finally:
            _hf_const.HF_HUB_OFFLINE = _saved_flag
            for k, v in _saved_env.items():
                if v is not None:
                    os.environ[k] = v

    if _mbti_pipeline is None:
        return None
    try:
        result = _mbti_pipeline(text[:512])
        label = result[0]["label"] if result else None
        if label and len(label) == 4 and label.upper() in MBTI_DESCRIPTORS:
            return label.upper()
    except Exception:
        pass
    return None


def get_mbti(ocean: dict, summary: str | None = None) -> str:
    """
    Get MBTI for a sim. Tries text inference first, falls back to OCEAN mapping.
    """
    if summary:
        inferred = infer_from_text(summary)
        if inferred:
            return inferred
    return ocean_to_mbti(ocean)
