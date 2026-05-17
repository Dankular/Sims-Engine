import random
import logging

from config import LOD_ACTIVE_LIMIT, LOD_BACKGROUND_LIMIT
from sim_types.enums import LODTier

logger = logging.getLogger(__name__)


def assign_lod_tiers(sims: list["Sim"]) -> None:
    for index, sim in enumerate(sims):
        if index < LOD_ACTIVE_LIMIT:
            sim.lod_tier = LODTier.ACTIVE
        elif index < LOD_BACKGROUND_LIMIT:
            sim.lod_tier = LODTier.BACKGROUND
        else:
            sim.lod_tier = LODTier.DORMANT


def heuristic_background_interaction(
    sim_a: "Sim",
    sim_b: "Sim",
    relationships: "RelationshipGraph",
    bg_llm: "LLMBackend | None" = None,
) -> None:
    """
    Background tier interaction. Uses lightweight LLM if available, otherwise
    falls back to the agreeableness-based heuristic.
    """
    relationship = relationships.get(sim_a.sim_id, sim_b.sim_id)

    if bg_llm is not None:
        _llm_background_interaction(sim_a, sim_b, relationship, bg_llm)
    else:
        _heuristic_fallback(sim_a, sim_b, relationship)


def _heuristic_fallback(sim_a, sim_b, relationship) -> None:
    compatibility = (sim_a.ocean["agreeableness"] + sim_b.ocean["agreeableness"]) / 2
    valence = round(random.uniform(-0.2, 0.5) + compatibility * 0.4, 2)
    friendship_delta = round(valence * random.uniform(1, 4), 1)
    relationship.apply_deltas(friendship_delta, 0)
    sim_a.needs.restore("social", random.uniform(2, 8) * max(0, valence))
    sim_b.needs.restore("social", random.uniform(2, 6) * max(0, valence))


def _llm_background_interaction(sim_a, sim_b, relationship, bg_llm) -> None:
    """Single-shot compact LLM adjudication for BACKGROUND tier sims."""
    prompt = (
        f"Two sims interact. Natural dialogue and light narration are allowed. Return JSON only.\n"
        f"Sim A: {sim_a.name} | traits={sim_a.profile['traits']} | "
        f"emotion={sim_a.emotion.dominant} | O={sim_a.ocean['openness']:.1f} "
        f"A={sim_a.ocean['agreeableness']:.1f}\n"
        f"Sim B: {sim_b.name} | traits={sim_b.profile['traits']} | "
        f"emotion={sim_b.emotion.dominant}\n"
        f"Relationship: {relationship.state_label()} (F={relationship.friendship:.0f})\n"
        f'Return: {{"friendship_delta": <-5 to 5>, "valence": <-1.0 to 1.0>, '
        f'"memory": "<short narrative memory>"}}'
    )
    system = "You are a sim interaction adjudicator. Respond with valid JSON only."
    try:
        import json, re

        raw = bg_llm.chat(system=system, user=prompt, max_tokens=80, temperature=0.7)
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        fd = float(data.get("friendship_delta", 0))
        valence = float(data.get("valence", 0))
        relationship.apply_deltas(fd, 0)
        sim_a.needs.restore("social", random.uniform(2, 8) * max(0, valence))
        sim_b.needs.restore("social", random.uniform(2, 6) * max(0, valence))
        if data.get("memory"):
            relationship.add_memory(data["memory"], valence)
        logger.debug(
            "[BG] %s↔%s fd=%+.1f val=%.2f", sim_a.name, sim_b.name, fd, valence
        )
    except Exception as exc:
        logger.debug("BG LLM failed (%s) — heuristic fallback", exc)
        _heuristic_fallback(sim_a, sim_b, relationship)
