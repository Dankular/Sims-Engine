from __future__ import annotations


def zone_from_temp(temp: float) -> str:
    if temp <= -70:
        return "very_cold"
    if temp <= -30:
        return "cold"
    if temp <= 30:
        return "comfortable"
    if temp <= 70:
        return "hot"
    return "very_hot"


def clothing_thermal_profile(outfit: str) -> tuple[float, float]:
    mapping = {
        "outerwear": (0.35, 0.15),
        "formalwear": (0.15, 0.05),
        "swimwear": (-0.25, -0.25),
        "underwear": (-0.2, -0.2),
        "light": (-0.1, -0.05),
    }
    return mapping.get(outfit, (0.0, 0.0))


def update_internal_temp(sim, outdoor_temp: float, indoor: bool) -> None:
    from core.species import temperature_immunity

    env = outdoor_temp
    if indoor:
        env = 0.0
    insulation, heat_retention = clothing_thermal_profile(
        sim.profile.get("outfit", "formalwear")
    )
    species_cold, species_heat = temperature_immunity(sim)
    cold_immune = (
        species_cold
        or "coldproof" in sim.profile.get("traits", [])
        or "immune_cold" in sim.profile.get("traits", [])
    )
    heat_immune = (
        species_heat
        or "heatproof" in sim.profile.get("traits", [])
        or "immune_heat" in sim.profile.get("traits", [])
    )
    delta = (env - sim.internal_temperature) * (0.08 - insulation * 0.04)
    delta += heat_retention * 0.8
    if cold_immune and delta < 0:
        delta *= 0.25
    if heat_immune and delta > 0:
        delta *= 0.25
    sim.internal_temperature = max(-100.0, min(100.0, sim.internal_temperature + delta))
