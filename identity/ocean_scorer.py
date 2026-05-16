from __future__ import annotations

import logging
import os
import random

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from config import HF_PERSONALITY_MODEL

logger = logging.getLogger(__name__)

_OCEAN_KEYS = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]

_tokenizer = None
_model = None
_load_attempted = False


def _load_model() -> bool:
    global _tokenizer, _model, _load_attempted
    if _load_attempted:
        return _model is not None
    _load_attempted = True
    try:
        import torch
        import transformers as _tf
        from transformers import RobertaForSequenceClassification, RobertaTokenizer
        _tf.logging.set_verbosity_error()

        _local = True
        try:
            _tokenizer = RobertaTokenizer.from_pretrained(
                HF_PERSONALITY_MODEL, local_files_only=True
            )
            _model = RobertaForSequenceClassification.from_pretrained(
                HF_PERSONALITY_MODEL,
                num_labels=5,
                ignore_mismatched_sizes=True,
                local_files_only=True,
            )
        except Exception:
            _local = False
            # Model not in local cache — must fetch; temporarily allow network
            _prev = os.environ.pop("TRANSFORMERS_OFFLINE", None)
            try:
                _tokenizer = RobertaTokenizer.from_pretrained(HF_PERSONALITY_MODEL)
                _model = RobertaForSequenceClassification.from_pretrained(
                    HF_PERSONALITY_MODEL,
                    num_labels=5,
                    ignore_mismatched_sizes=True,
                )
            finally:
                if _prev is not None:
                    os.environ["TRANSFORMERS_OFFLINE"] = _prev

        # Remap checkpoint keys:
        #   transformer.*   → roberta.*          (backbone prefix mismatch)
        #   hidden_layer.*  → classifier.dense.* (first FC layer of the head)
        #   output_layer.*  → classifier.out_proj.* (final projection layer)
        try:
            from huggingface_hub import hf_hub_download
            ckpt_path = hf_hub_download(
                repo_id=HF_PERSONALITY_MODEL,
                filename="pytorch_model.bin",
                local_files_only=_local,
            )
            raw_state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
            _HEAD_MAP = {
                "hidden_layer.weight": "classifier.dense.weight",
                "hidden_layer.bias":   "classifier.dense.bias",
                "output_layer.weight": "classifier.out_proj.weight",
                "output_layer.bias":   "classifier.out_proj.bias",
            }
            remapped = {}
            for k, v in raw_state.items():
                if k in _HEAD_MAP:
                    remapped[_HEAD_MAP[k]] = v
                elif k.startswith("transformer."):
                    remapped[k.replace("transformer.", "roberta.", 1)] = v
                # drop any remaining unknown keys
            _model.load_state_dict(remapped, strict=False)
        except Exception as remap_exc:
            logger.debug("Key remap skipped (%s) — using from_pretrained weights", remap_exc)

        _model.eval()
        logger.info("KevSun/Personality_LM loaded successfully")
        return True
    except Exception as exc:
        logger.warning(
            "KevSun/Personality_LM unavailable (%s) — OCEAN will use seeded random fallback", exc
        )
        _model = None
        _tokenizer = None
        return False


def ocean_from_text(text: str | None = None) -> dict:
    """Return OCEAN scores in [0, 1] inferred from text via KevSun/Personality_LM.

    Falls back to deterministic seeded random when the model is unavailable,
    ensuring reproducible profiles for the same input text.
    """
    if text and _load_model() and _model is not None and _tokenizer is not None:
        try:
            import torch
            inputs = _tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            with torch.no_grad():
                logits = _model(**inputs).logits[0]
            scores = torch.sigmoid(logits).tolist()
            return {k: round(float(v), 2) for k, v in zip(_OCEAN_KEYS, scores)}
        except Exception as exc:
            logger.warning("OCEAN inference failed (%s) — using seeded fallback", exc)

    rng = random.Random(text)
    return {k: round(rng.uniform(0.2, 0.9), 2) for k in _OCEAN_KEYS}


# ── Child / short-text OCEAN scorer ───────────────────────────────────────────
# Arash-Alborz/personality-trait-predictor — DistilBERT, handles short text
_child_model = None
_child_load_attempted = False


def ocean_from_short_text(text: str) -> dict | None:
    """
    Score a short text (e.g. child's self_summary) through a lighter DistilBERT model.
    Returns None if model is unavailable — caller should blend parent OCEAN instead.
    """
    global _child_model, _child_load_attempted
    if _child_load_attempted:
        if _child_model is None:
            return None
    else:
        _child_load_attempted = True
        import os
        import huggingface_hub.constants as _hf_const
        _offline_vars = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
        _saved_env = {k: os.environ.pop(k, None) for k in _offline_vars}
        _saved_flag = _hf_const.HF_HUB_OFFLINE
        _hf_const.HF_HUB_OFFLINE = False
        try:
            from transformers import pipeline as _hf_pipeline
            from config import HF_CHILD_OCEAN_MODEL
            _child_model = _hf_pipeline(
                "text-classification", model=HF_CHILD_OCEAN_MODEL,
                top_k=None, truncation=True, max_length=256,
            )
            logger.info("Child OCEAN model loaded: %s", HF_CHILD_OCEAN_MODEL)
        except Exception as exc:
            logger.debug("Child OCEAN model unavailable (%s)", exc)
            _child_model = None
        finally:
            _hf_const.HF_HUB_OFFLINE = _saved_flag
            for k, v in _saved_env.items():
                if v is not None:
                    os.environ[k] = v

    if _child_model is None:
        return None

    # Label mapping varies per model checkpoint — try common patterns
    _LABEL_ALIASES = {
        "O": "openness", "C": "conscientiousness", "E": "extraversion",
        "A": "agreeableness", "N": "neuroticism",
        "openness": "openness", "conscientiousness": "conscientiousness",
        "extraversion": "extraversion", "agreeableness": "agreeableness",
        "neuroticism": "neuroticism",
    }
    try:
        results = _child_model(text[:256])
        scores = results[0] if isinstance(results[0], list) else results
        ocean: dict[str, float] = {}
        for item in scores:
            key = _LABEL_ALIASES.get(item["label"].upper(),
                  _LABEL_ALIASES.get(item["label"].lower()))
            if key:
                ocean[key] = round(float(item["score"]), 2)
        if len(ocean) == 5:
            return ocean
    except Exception as exc:
        logger.debug("Child OCEAN inference failed: %s", exc)
    return None
