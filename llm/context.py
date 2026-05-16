from __future__ import annotations

from typing import Any

from llm.adjudicator import ADJUDICATOR_SYSTEM_BASE
from datasets.atomic import query_atomic
from datasets.emotion_calib import build_emotion_calibration_block
from datasets.social_iqa import sample_social_iqa


def get_interaction_context(interaction: str, sim_a: Any, sim_b: Any) -> str:
    parts: list[str] = []
    atomic = query_atomic(interaction)
    if atomic:
        parts.append(f"ATOMIC: {atomic}")
    iqa = sample_social_iqa(interaction)
    if iqa:
        parts.append(f"SOCIAL_IQA: {iqa}")
    vulnerable = (
        sim_a.profile["ocean"]["neuroticism"] > 0.7
        or sim_b.profile["ocean"]["neuroticism"] > 0.7
        or bool(sim_a.fears)
        or bool(sim_b.fears)
    )
    if vulnerable:
        parts.append("Vulnerable sim present; apply empathetic reasoning.")
    return "\n".join(parts)


def build_adjudicator_system(norms: list[str]) -> str:
    if not norms:
        return ADJUDICATOR_SYSTEM_BASE + "\n\n" + build_emotion_calibration_block()
    return (
        ADJUDICATOR_SYSTEM_BASE
        + "\n\nSOCIAL NORMS:\n"
        + "\n".join(f"- {item}" for item in norms)
        + "\n\n"
        + build_emotion_calibration_block()
    )
