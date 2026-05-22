from __future__ import annotations

from typing import Any


def record_consequence(
    sim_a: Any,
    sim_b: Any,
    relationship: Any,
    interaction: str,
    valence: float,
) -> dict[str, float | str]:
    tag = "neutral"
    if valence >= 0.45:
        tag = "positive"
    elif valence <= -0.45:
        tag = "negative"

    if tag == "positive":
        sim_a.social_strain = max(
            0.0, float(getattr(sim_a, "social_strain", 0.0)) - 1.0
        )
        sim_b.social_strain = max(
            0.0, float(getattr(sim_b, "social_strain", 0.0)) - 0.6
        )
    elif tag == "negative":
        sim_a.social_strain = min(
            100.0, float(getattr(sim_a, "social_strain", 0.0)) + 1.5
        )

    return {
        "tag": tag,
        "friendship": float(getattr(relationship, "friendship", 0.0) or 0.0),
        "romance": float(getattr(relationship, "romance", 0.0) or 0.0),
        "valence": float(valence),
        "interaction": interaction,
    }
