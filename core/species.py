from __future__ import annotations


SPECIES_CAPABILITY_MATRIX: dict[str, dict] = {
    "human": {
        "allowed_interactions": {"*"},
        "temperature_immunity": {"cold": False, "heat": False},
        "reproduction": {"human", "alien", "plant_based", "supernatural"},
    },
    "ghost": {
        "allowed_interactions": {"chat", "haunt", "confide", "argue", "flirt"},
        "temperature_immunity": {"cold": True, "heat": True},
        "reproduction": set(),
    },
    "robot": {
        "allowed_interactions": {"chat", "debate", "teach logic", "repair expertly"},
        "temperature_immunity": {"cold": True, "heat": False},
        "reproduction": set(),
    },
    "plant_based": {
        "allowed_interactions": {"chat", "garden", "share story", "confide"},
        "temperature_immunity": {"cold": False, "heat": False},
        "reproduction": {"plant_based", "human"},
    },
    "alien": {
        "allowed_interactions": {"*"},
        "temperature_immunity": {"cold": False, "heat": False},
        "reproduction": {"alien", "human"},
    },
    "supernatural": {
        "allowed_interactions": {"*"},
        "temperature_immunity": {"cold": False, "heat": False},
        "reproduction": {"supernatural", "human"},
    },
}


def species_of(sim) -> str:
    if getattr(sim, "is_ghost", False) or getattr(sim, "occult_type", "") == "ghost":
        return "ghost"
    occ = str(getattr(sim, "occult_type", "none") or "none")
    if occ in {"robot", "plant_based", "alien"}:
        return occ
    if occ not in {"none", "human"}:
        return "supernatural"
    return "human"


def can_perform_interaction(sim, interaction: str) -> bool:
    species = species_of(sim)
    rules = SPECIES_CAPABILITY_MATRIX.get(species, SPECIES_CAPABILITY_MATRIX["human"])
    allowed = rules.get("allowed_interactions", {"*"})
    if "*" in allowed:
        return True
    lowered = interaction.lower()
    return any(token in lowered for token in allowed)


def temperature_immunity(sim) -> tuple[bool, bool]:
    species = species_of(sim)
    rules = SPECIES_CAPABILITY_MATRIX.get(species, SPECIES_CAPABILITY_MATRIX["human"])
    imm = rules.get("temperature_immunity", {})
    return bool(imm.get("cold", False)), bool(imm.get("heat", False))
