"""
tts/voice_catalog.py — OmniVoice voice slot catalog.

Replaces the old Supertonic/ElevenLabs catalog.  Voices are identified
by slot ID (M1-M5, F1-F5) and synthesised via OmniVoice instruct= mode
— no reference audio files are needed.
"""
from __future__ import annotations

from tts.engine import VOICE_INSTRUCT


def load_voice_catalog() -> list[dict]:
    """Return the built-in OmniVoice voice slot list."""
    return [
        {
            "id": slot,
            "description": instruct,
            "gender": "male" if slot.startswith("M") else "female",
            "category": "built-in",
        }
        for slot, instruct in VOICE_INSTRUCT.items()
    ]


def find_voice_by_id(voice_id: str, voices: list[dict]) -> dict | None:
    for v in voices:
        if str(v.get("id", "")).strip() == voice_id.strip():
            return v
    return None


def list_voices(
    voices: list[dict],
    gender: str | None = None,
    category: str | None = None,
    limit: int = 25,
) -> list[dict]:
    out: list[dict] = []
    for v in voices:
        if gender and v.get("gender", "").lower() != gender.lower():
            continue
        if category and v.get("category", "").lower() != category.lower():
            continue
        out.append(v)
        if len(out) >= limit:
            break
    return out
