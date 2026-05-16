"""
datasets/ethics.py — hendrycks/ethics.

Commonsense, deontology, justice, utilitarianism, and virtue ethics scenarios.
Used to enrich the adjudicator system prompt with ethical reasoning calibration.
Virtue + commonsense sections are most useful for personality-driven outcomes.
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "ethics_norms"
_HF_ID     = "hendrycks/ethics"
_SUBSETS   = ["commonsense", "virtue"]
_MAX_PER   = 300


def load_ethics_norms() -> dict[str, list[dict]]:
    """Returns subset → [{scenario, label, is_ethical}] index."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    index: dict[str, list[dict]] = {}

    try:
        from datasets import load_dataset
        for subset in _SUBSETS:
            try:
                ds = load_dataset(_HF_ID, subset, split="train",
                                  streaming=True, trust_remote_code=True)
                entries: list[dict] = []
                for row in ds:
                    if len(entries) >= _MAX_PER:
                        break
                    # commonsense: {input, label}  virtue: {scenario, label}
                    text = (row.get("input") or row.get("scenario") or "").strip()
                    label = row.get("label")
                    if text and label is not None:
                        entries.append({
                            "text":       text,
                            "is_ethical": bool(int(label) == 1),
                        })
                index[subset] = entries
            except Exception:
                index[subset] = []
    except Exception:
        pass

    if any(index.values()):
        cache_save(_CACHE_KEY, index)
    return index


def get_ethics_calibration(n_commonsense: int = 2, n_virtue: int = 2) -> str:
    """
    Return a short ethics calibration block for the adjudicator system prompt.
    Samples ethical/unethical examples from commonsense and virtue subsets.
    """
    index = load_ethics_norms()
    lines: list[str] = []

    for subset, count in [("commonsense", n_commonsense), ("virtue", n_virtue)]:
        entries = index.get(subset, [])
        if not entries:
            continue
        ethical   = [e for e in entries if e["is_ethical"]]
        unethical = [e for e in entries if not e["is_ethical"]]
        for e in random.sample(ethical,   min(count // 2 + 1, len(ethical))):
            lines.append(f"  ✓ [{subset}] {e['text'][:120]}")
        for e in random.sample(unethical, min(count // 2,     len(unethical))):
            lines.append(f"  ✗ [{subset}] {e['text'][:120]}")

    if not lines:
        return ""
    return "\n\nETHICS CALIBRATION — examples of ethical (✓) vs unethical (✗) actions:\n" + "\n".join(lines)
