from __future__ import annotations

from typing import Any, TYPE_CHECKING

from llm.adjudicator import ADJUDICATOR_SYSTEM_BASE
from datasets.atomic import query_atomic
from datasets.emotion_calib import build_emotion_calibration_block
from datasets.social_iqa import sample_social_iqa

if TYPE_CHECKING:
    from datasets.loader import DatasetRegistry


def get_interaction_context(
    interaction: str,
    sim_a: Any,
    sim_b: Any,
    datasets: "DatasetRegistry | None" = None,
) -> str:
    parts: list[str] = []

    # ATOMIC commonsense
    atomic = query_atomic(interaction)
    if atomic:
        parts.append(f"ATOMIC: {atomic}")

    # Social IQA reasoning
    iqa = sample_social_iqa(interaction)
    if iqa:
        parts.append(f"SOCIAL_IQA: {iqa}")

    # Social bias — escalate if interaction touches sensitive territory
    if (
        datasets
        and hasattr(datasets, "social_bias_norms")
        and datasets.social_bias_norms
    ):
        from datasets.social_bias import (
            is_potentially_offensive,
            sample_conflict_escalation_context,
        )

        if is_potentially_offensive(interaction):
            ctx = sample_conflict_escalation_context()
            if ctx:
                parts.append(ctx)

    # Vulnerable sim
    vulnerable = (
        sim_a.profile["ocean"]["neuroticism"] > 0.7
        or sim_b.profile["ocean"]["neuroticism"] > 0.7
        or bool(sim_a.fears)
        or bool(sim_b.fears)
    )
    if vulnerable:
        parts.append("Vulnerable sim present; apply empathetic reasoning.")

    # Persona consistency examples for sim_a
    if datasets and hasattr(datasets, "persona_chat") and datasets.persona_chat:
        from datasets.persona_chat import get_persona_examples

        examples = get_persona_examples(sim_a.ocean, n=2)
        if examples:
            parts.append("PERSONA EXAMPLES for Sim A's voice:\n" + "\n".join(examples))

    return "\n".join(parts)


def build_adjudicator_system(
    norms: list[str],
    datasets: "DatasetRegistry | None" = None,
    interaction: str = "",
) -> str:
    prompt = ADJUDICATOR_SYSTEM_BASE

    if norms:
        prompt += "\n\nSOCIAL NORMS:\n" + "\n".join(f"- {n}" for n in norms)

    prompt += "\n\n" + build_emotion_calibration_block()

    # Ethics calibration from hendrycks/ethics
    if datasets and hasattr(datasets, "ethics_norms") and datasets.ethics_norms:
        from datasets.ethics import get_ethics_calibration

        ethics_block = get_ethics_calibration(n_commonsense=2, n_virtue=2)
        if ethics_block:
            prompt += ethics_block

    if (
        datasets
        and hasattr(datasets, "prosocial_nsfw_norms")
        and datasets.prosocial_nsfw_norms
        and any(
            tag in interaction.upper() for tag in ["[INTIMATE", "INTIMATE_ENCOUNTER"]
        )
    ):
        sample = datasets.prosocial_nsfw_norms[:4]
        prompt += "\n\nADULT CONTEXT NORMS:\n" + "\n".join(f"- {n}" for n in sample)

    return prompt


def get_life_event_context(event_type: str, narrative: str) -> str:
    """Query event2Mind for emotional cascades after a life event fires."""
    try:
        from datasets.event2mind import emotional_cascade

        cascade = emotional_cascade(f"{event_type} {narrative}")
        parts: list[str] = []
        if cascade.get("xReact"):
            parts.append(f"Sim A likely feels: {', '.join(cascade['xReact'])}")
        if cascade.get("oReact"):
            parts.append(f"Others around them feel: {', '.join(cascade['oReact'])}")
        if cascade.get("xWant"):
            parts.append(f"Sim A now wants: {', '.join(cascade['xWant'])}")
        return "\n".join(parts)
    except Exception:
        return ""
