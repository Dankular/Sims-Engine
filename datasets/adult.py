"""Adult-flag gated datasets for intimate interactions and norms."""

from __future__ import annotations

import os
import random

from datasets.cache import cache_load, cache_save


def adult_enabled() -> bool:
    return os.environ.get("SIM_V2_ADULT", "0") == "1"


def load_sensual_speech_patterns() -> list[str]:
    key = "sensual_speech_patterns"
    cached = cache_load(key)
    if cached:
        return cached
    lines: list[str] = []
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "traltyaziking/SensualSpeechPatterns",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if len(lines) >= 800:
                break
            text = str(
                row.get("text") or row.get("utterance") or row.get("prompt") or ""
            ).strip()
            if 15 <= len(text) <= 280:
                lines.append(text)
    except Exception:
        pass
    if lines:
        cache_save(key, lines)
    return lines


def load_adult_norms() -> list[str]:
    key = "prosocial_nsfw_norms"
    cached = cache_load(key)
    if cached:
        return cached
    norms: list[str] = []
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "shahules786/prosocial-nsfw",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if len(norms) >= 400:
                break
            text = str(
                row.get("text") or row.get("norm") or row.get("response") or ""
            ).strip()
            if len(text) > 20:
                norms.append(text[:220])
    except Exception:
        pass
    if norms:
        cache_save(key, norms)
    return norms


def load_literotica_snippets() -> list[str]:
    key = "literotica_snippets"
    cached = cache_load(key)
    if cached:
        return cached
    out: list[str] = []
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "mpasila/Literotica-stories-short",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if len(out) >= 600:
                break
            text = str(
                row.get("text") or row.get("story") or row.get("content") or ""
            ).strip()
            if 80 <= len(text) <= 700:
                out.append(text[:500])
    except Exception:
        pass
    if out:
        cache_save(key, out)
    return out


def sample_sensual_line() -> str | None:
    pool = load_sensual_speech_patterns()
    return random.choice(pool) if pool else None


def sample_literotica_snippet() -> str | None:
    pool = load_literotica_snippets()
    return random.choice(pool) if pool else None
