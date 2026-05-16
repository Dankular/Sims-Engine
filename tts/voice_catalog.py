from __future__ import annotations

import json
from pathlib import Path


def load_voice_catalog(path: str | Path = "el_voices.json") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def find_voice_by_id(voice_id: str, voices: list[dict]) -> dict | None:
    for v in voices:
        if str(v.get("id", "")).strip() == voice_id.strip():
            return v
    return None


def list_voices(
    voices: list[dict],
    language: str | None = None,
    category: str | None = None,
    limit: int = 25,
) -> list[dict]:
    out: list[dict] = []
    for v in voices:
        if language and str(v.get("language", "")).lower() != language.lower():
            continue
        if category and str(v.get("category", "")).lower() != category.lower():
            continue
        out.append(v)
        if len(out) >= limit:
            break
    return out
