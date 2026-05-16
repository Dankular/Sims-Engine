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
