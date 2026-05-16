import random

from config import LOD_ACTIVE_LIMIT, LOD_BACKGROUND_LIMIT
from sim_types.enums import LODTier


def assign_lod_tiers(sims: list["Sim"]) -> None:
    for index, sim in enumerate(sims):
        if index < LOD_ACTIVE_LIMIT:
            sim.lod_tier = LODTier.ACTIVE
        elif index < LOD_BACKGROUND_LIMIT:
            sim.lod_tier = LODTier.BACKGROUND
        else:
            sim.lod_tier = LODTier.DORMANT


def heuristic_background_interaction(
    sim_a: "Sim", sim_b: "Sim", relationships: "RelationshipGraph"
) -> None:
    relationship = relationships.get(sim_a.sim_id, sim_b.sim_id)
    compatibility = (sim_a.ocean["agreeableness"] + sim_b.ocean["agreeableness"]) / 2
    valence = round(random.uniform(-0.2, 0.5) + compatibility * 0.4, 2)
    friendship_delta = round(valence * random.uniform(1, 4), 1)
    relationship.apply_deltas(friendship_delta, 0)
