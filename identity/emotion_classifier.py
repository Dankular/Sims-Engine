"""
identity/emotion_classifier.py — ModernBERT GoEmotions multi-label classifier.

Model: cirimus/modernbert-base-go-emotions
- Exactly the 27 labels in EMOTIONS_27 (multi-label output)
- ~140M params, CPU inference in milliseconds
- Fallback: AnasAlokla/multilingual_go_emotions

Use cases:
  1. Cross-check / augment LLM emotion assignment after adjudication
  2. Tag gossip spread text with emotions
  3. Tag life event narratives without a full LLM call
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Lazy-loaded pipeline ───────────────────────────────────────────────────────
_pipeline = None
_load_attempted = False


def _load() -> bool:
    global _pipeline, _load_attempted
    if _load_attempted:
        return _pipeline is not None
    _load_attempted = True

    # Temporarily unset HF offline flag (ocean_scorer sets it at import)
    import huggingface_hub.constants as _hf_const
    _offline_vars = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    _saved_env = {k: os.environ.pop(k, None) for k in _offline_vars}
    _saved_flag = _hf_const.HF_HUB_OFFLINE
    _hf_const.HF_HUB_OFFLINE = False

    try:
        from transformers import pipeline as _hf_pipeline
        from config import HF_EMOTION_CLASSIFIER, HF_EMOTION_CLASSIFIER_ML

        for model_id in [HF_EMOTION_CLASSIFIER, HF_EMOTION_CLASSIFIER_ML]:
            try:
                _pipeline = _hf_pipeline(
                    "text-classification",
                    model=model_id,
                    top_k=None,          # return all labels with scores
                    truncation=True,
                    max_length=512,
                )
                logger.info("Emotion classifier loaded: %s", model_id)
                return True
            except Exception as exc:
                logger.debug("Could not load %s: %s", model_id, exc)

        logger.warning("Emotion classifier unavailable — LLM-only emotion tagging active.")
        return False
    except ImportError:
        logger.warning("transformers not installed — emotion classifier disabled.")
        return False
    finally:
        _hf_const.HF_HUB_OFFLINE = _saved_flag
        for k, v in _saved_env.items():
            if v is not None:
                os.environ[k] = v


def classify(text: str, threshold: float = 0.3, top_k: int = 3) -> list[str]:
    """
    Classify text into GoEmotions labels.
    Returns list of label strings above threshold, sorted by confidence.
    Falls back to [] if model is unavailable.
    """
    if not text.strip():
        return []
    if not _load():
        return []

    from config import EMOTIONS_27
    try:
        results = _pipeline(text[:512])
        # results is list of list of {label, score} when top_k=None
        scores = results[0] if isinstance(results[0], list) else results
        valid = [
            r for r in scores
            if r["score"] >= threshold and r["label"].lower() in EMOTIONS_27
        ]
        valid.sort(key=lambda r: r["score"], reverse=True)
        return [r["label"].lower() for r in valid[:top_k]]
    except Exception as exc:
        logger.debug("Emotion classification failed: %s", exc)
        return []


def augment_emotions(
    llm_emotion: str,
    text: str,
    threshold: float = 0.35,
) -> list[str]:
    """
    Return additional emotions found in text that the LLM didn't assign.
    Safe to call even if the classifier isn't loaded.
    """
    classified = classify(text, threshold=threshold, top_k=4)
    seen = {llm_emotion.lower()}
    return [e for e in classified if e not in seen]
