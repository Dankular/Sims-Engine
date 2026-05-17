from __future__ import annotations

import random


DOMINANT_EYES = {"brown", "dark_blue", "alien"}


def pick_gene_pair(
    parent_a: tuple[str, str], parent_b: tuple[str, str]
) -> tuple[str, str]:
    return (random.choice(parent_a), random.choice(parent_b))


def express_eye_color(gene_pair: tuple[str, str]) -> str:
    a, b = gene_pair
    if a in DOMINANT_EYES:
        return a
    if b in DOMINANT_EYES:
        return b
    return random.choice([a, b])


def inherit_skin_tone(
    a_range: tuple[float, float], b_range: tuple[float, float]
) -> float:
    low = min(a_range[0], b_range[0])
    high = max(a_range[1], b_range[1])
    return round(random.uniform(low, high), 3)


def blend_personality_axis(a: float, b: float, mutation_scale: float = 0.8) -> float:
    base = (float(a) + float(b)) / 2.0
    return max(
        0.0, min(10.0, round(base + random.uniform(-mutation_scale, mutation_scale), 2))
    )


def inherit_hidden_traits(parent_a: dict, parent_b: dict) -> dict:
    out = {}
    for key in ("occult_lineage", "special_bloodline", "hidden_ability", "immunity"):
        vals = [parent_a.get(key), parent_b.get(key)]
        vals = [v for v in vals if v]
        if vals:
            out[key] = random.choice(vals)
    return out
