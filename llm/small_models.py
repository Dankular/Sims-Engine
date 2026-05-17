"""
llm/small_models.py — Central registry for all small inference models.

Each model is lazy-loaded on first call, CPU-optimised, and returns None
on failure so every caller can fall back to the existing hardcoded logic.
Thread-safe singletons with local-first model loading.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)
_lock = threading.Lock()

# ── Singletons ────────────────────────────────────────────────────────────────
_ZERO_SHOT: Any = None  # cross-encoder/nli-deberta-v3-small
_GOAL_NLI: Any = None  # typeform/distilbert-base-uncased-mnli
_SENTIMENT: Any = None  # cardiffnlp/twitter-roberta-base-sentiment-latest
_EKMAN: Any = None  # j-hartmann/emotion-english-distilroberta-base
_CROSS_ENCODER: Any = None  # cross-encoder/ms-marco-MiniLM-L-6-v2
_COMET: Any = None  # allenai/comet-distil  (tuple: model, tokenizer)
_REWARD: Any = None  # OpenAssistant/reward-model-deberta-v3-large-v2

_SENTINEL = object()  # distinguishes "not loaded" from None result


def _load_pipeline(model_id: str, task: str, **kw) -> Any:
    """Load a HuggingFace pipeline: local first, then download, CPU only."""
    from transformers import pipeline as _p

    for local in (True, False):
        try:
            return _p(
                task,
                model=model_id,
                local_files_only=local,
                device=-1,
                token=None,
                **kw,
            )
        except Exception:
            if local:
                continue
    logger.warning("[SmallModel] Could not load %s", model_id)
    return None


# ── Public getters ────────────────────────────────────────────────────────────


def get_zero_shot():
    """
    Zero-shot NLI classifier (cross-encoder/nli-deberta-v3-small).
    Used for: scheduler routing + arc (burnout/loneliness) detection.
    """
    global _ZERO_SHOT
    if _ZERO_SHOT is not None:
        return _ZERO_SHOT
    with _lock:
        if _ZERO_SHOT is None:
            try:
                from config import NLI_SMALL_MODEL

                _ZERO_SHOT = (
                    _load_pipeline(NLI_SMALL_MODEL, "zero-shot-classification")
                    or _SENTINEL
                )
            except Exception as exc:
                logger.debug("zero-shot load failed: %s", exc)
                _ZERO_SHOT = _SENTINEL
    return None if _ZERO_SHOT is _SENTINEL else _ZERO_SHOT


def get_goal_nli():
    """
    Zero-shot NLI for goal inference (typeform/distilbert-base-uncased-mnli).
    """
    global _GOAL_NLI
    if _GOAL_NLI is not None:
        return _GOAL_NLI
    with _lock:
        if _GOAL_NLI is None:
            try:
                from config import GOAL_NLI_MODEL

                _GOAL_NLI = (
                    _load_pipeline(GOAL_NLI_MODEL, "zero-shot-classification")
                    or _SENTINEL
                )
            except Exception as exc:
                logger.debug("goal-nli load failed: %s", exc)
                _GOAL_NLI = _SENTINEL
    return None if _GOAL_NLI is _SENTINEL else _GOAL_NLI


def get_sentiment():
    """
    Sentiment classifier (cardiffnlp/twitter-roberta-base-sentiment-latest).
    Returns pipeline that maps text → POSITIVE/NEGATIVE/NEUTRAL with score.
    """
    global _SENTIMENT
    if _SENTIMENT is not None:
        return _SENTIMENT
    with _lock:
        if _SENTIMENT is None:
            try:
                from config import SENTIMENT_MODEL

                _SENTIMENT = (
                    _load_pipeline(SENTIMENT_MODEL, "text-classification") or _SENTINEL
                )
            except Exception as exc:
                logger.debug("sentiment load failed: %s", exc)
                _SENTIMENT = _SENTINEL
    return None if _SENTIMENT is _SENTINEL else _SENTIMENT


def get_ekman():
    """
    Ekman-7 emotion classifier (j-hartmann/emotion-english-distilroberta-base).
    Returns pipeline that produces all-label scores (top_k=None).
    """
    global _EKMAN
    if _EKMAN is not None:
        return _EKMAN
    with _lock:
        if _EKMAN is None:
            try:
                from config import EKMAN_MODEL

                _EKMAN = (
                    _load_pipeline(EKMAN_MODEL, "text-classification", top_k=None)
                    or _SENTINEL
                )
            except Exception as exc:
                logger.debug("ekman load failed: %s", exc)
                _EKMAN = _SENTINEL
    return None if _EKMAN is _SENTINEL else _EKMAN


class _TransformersCrossEncoder:
    """
    Thin wrapper around AutoModelForSequenceClassification for cross-encoder
    scoring.  Uses pure transformers — avoids the sentence_transformers /
    local-datasets namespace collision on this project.
    """

    def __init__(self, model, tokenizer):
        self._model = model
        self._tok = tokenizer

    def predict(
        self, pairs: list[tuple[str, str]], show_progress_bar: bool = False
    ) -> list[float]:
        import torch

        queries = [p[0] for p in pairs]
        passages = [p[1] for p in pairs]
        enc = self._tok(
            queries,
            passages,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = self._model(**enc).logits.squeeze(-1)
        scores = logits.tolist()
        return scores if isinstance(scores, list) else [scores]


def get_cross_encoder():
    """
    CrossEncoder for memory reranking (cross-encoder/ms-marco-MiniLM-L-6-v2).
    Uses transformers directly to avoid the local datasets/ namespace clash.
    """
    global _CROSS_ENCODER
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    with _lock:
        if _CROSS_ENCODER is None:
            try:
                from transformers import (
                    AutoTokenizer,
                    AutoModelForSequenceClassification,
                )
                from config import CROSS_ENCODER_MODEL

                for local in (True, False):
                    try:
                        tok = AutoTokenizer.from_pretrained(
                            CROSS_ENCODER_MODEL, local_files_only=local
                        )
                        mdl = AutoModelForSequenceClassification.from_pretrained(
                            CROSS_ENCODER_MODEL, local_files_only=local
                        )
                        mdl.eval()
                        _CROSS_ENCODER = _TransformersCrossEncoder(mdl, tok)
                        logger.info(
                            "[SmallModel] CrossEncoder loaded: %s", CROSS_ENCODER_MODEL
                        )
                        break
                    except Exception:
                        if local:
                            continue
                if _CROSS_ENCODER is None:
                    _CROSS_ENCODER = _SENTINEL
            except Exception as exc:
                logger.debug("cross-encoder load failed: %s", exc)
                _CROSS_ENCODER = _SENTINEL
    return None if _CROSS_ENCODER is _SENTINEL else _CROSS_ENCODER


def get_comet() -> tuple[Any, Any] | None:
    """
    ATOMIC causal inference via google/flan-t5-small.
    flan-t5-small is instruction-tuned and handles ATOMIC-style prompts
    (xReact / xWant / oReact) without a task-specific fine-tune.
    Returns (model, tokenizer) tuple or None.
    """
    global _COMET
    if _COMET is not None:
        return _COMET
    with _lock:
        if _COMET is None:
            try:
                from transformers import T5ForConditionalGeneration, T5Tokenizer
                from config import COMET_MODEL

                for local in (True, False):
                    try:
                        tok = T5Tokenizer.from_pretrained(
                            COMET_MODEL, local_files_only=local, legacy=False
                        )
                        mdl = T5ForConditionalGeneration.from_pretrained(
                            COMET_MODEL, local_files_only=local
                        )
                        mdl.eval()
                        _COMET = (mdl, tok)
                        logger.info("[SmallModel] COMET(flan-t5-small) loaded")
                        break
                    except Exception:
                        if local:
                            continue
                if _COMET is None:
                    _COMET = _SENTINEL
            except Exception as exc:
                logger.debug("comet(flan-t5) load failed: %s", exc)
                _COMET = _SENTINEL
    return None if _COMET is _SENTINEL else _COMET


def get_reward():
    """
    Reward model for conformity pressure scoring
    (OpenAssistant/reward-model-deberta-v3-large-v2).
    """
    global _REWARD
    if _REWARD is not None:
        return _REWARD
    with _lock:
        if _REWARD is None:
            try:
                from config import REWARD_MODEL

                _REWARD = (
                    _load_pipeline(REWARD_MODEL, "text-classification") or _SENTINEL
                )
            except Exception as exc:
                logger.debug("reward-model load failed: %s", exc)
                _REWARD = _SENTINEL
    return None if _REWARD is _SENTINEL else _REWARD


# ── Helper utilities ──────────────────────────────────────────────────────────


def sentiment_to_modifier(pipeline_result: list[dict]) -> float:
    """
    Convert sentiment pipeline output → delta multiplier (0.5–1.5).
    Positive sentiment boosts deltas; negative softens them.
    """
    if not pipeline_result:
        return 1.0
    label = pipeline_result[0].get("label", "neutral").lower()
    score = float(pipeline_result[0].get("score", 0.5))
    if "positive" in label:
        return 0.8 + score * 0.7  # 0.80–1.50
    if "negative" in label:
        return 0.5 + (1.0 - score) * 0.4  # 0.50–0.90
    return 1.0  # neutral


# flan-t5 instruction prompts for each ATOMIC relation
_COMET_PROMPTS: dict[str, str] = {
    "xReact": "Answer in a few words: If someone {event}, how do they feel emotionally?",
    "xWant": "Answer in a few words: If someone {event}, what would they want to do next?",
    "oReact": "Answer in a few words: If someone {event}, how would nearby people react?",
}


def comet_infer(event_text: str, relations: list[str] | None = None) -> dict[str, str]:
    """
    Run ATOMIC causal inference via flan-t5-small.
    Returns {relation: completion} or {} on failure.
    """
    pair = get_comet()
    if pair is None:
        return {}
    model, tokenizer = pair
    if relations is None:
        relations = ["xReact", "xWant", "oReact"]
    results: dict[str, str] = {}
    for rel in relations:
        prompt_template = _COMET_PROMPTS.get(rel)
        if not prompt_template:
            continue
        try:
            import torch

            prompt = prompt_template.format(event=event_text[:120])
            enc = tokenizer(
                prompt, return_tensors="pt", truncation=True, max_length=128
            )
            with torch.no_grad():
                out = model.generate(
                    **enc, max_new_tokens=20, num_beams=2, early_stopping=True
                )
            text = tokenizer.decode(out[0], skip_special_tokens=True).strip()
            if text and text.lower() not in ("none", "i don't know", ""):
                results[rel] = text
        except Exception:
            pass
    return results


def zero_shot_classify(
    text: str, labels: list[str], pipeline=None, threshold: float = 0.35
) -> tuple[str, float] | None:
    """
    Run zero-shot classification; returns (best_label, score) or None.
    Falls back gracefully if model unavailable.
    """
    clf = pipeline or get_zero_shot()
    if clf is None:
        return None
    try:
        r = clf(text[:512], labels, multi_label=False)
        label, score = r["labels"][0], r["scores"][0]
        if score >= threshold:
            return label, score
    except Exception:
        pass
    return None
