from __future__ import annotations

from typing import Any


ACTION_PREREQS: dict[str, dict[str, Any]] = {
    "repair appliance": {"skill": ("handiness", 2), "energy_min": 20},
    "deep clean room": {"skill": ("cleaning", 2), "energy_min": 18},
    "host dinner party": {"skill": ("cooking", 5), "hunger_min": 20},
    "mentor junior": {"skill": ("charisma", 3), "friendship_min": 20},
    "first aid": {"energy_min": 12},
    "call emergency": {"social_min": 5},
}


def prerequisites_met(sim: Any, relationship: Any, action: str) -> bool:
    req = ACTION_PREREQS.get((action or "").lower())
    if not req:
        return True

    needs = getattr(sim, "needs", None)
    energy = float(getattr(needs, "energy", 50.0) or 50.0)
    social = float(getattr(needs, "social", 50.0) or 50.0)
    hunger = float(getattr(needs, "hunger", 50.0) or 50.0)
    friendship = float(getattr(relationship, "friendship", 0.0) or 0.0)

    if energy < float(req.get("energy_min", 0)):
        return False
    if social < float(req.get("social_min", 0)):
        return False
    if hunger < float(req.get("hunger_min", 0)):
        return False
    if friendship < float(req.get("friendship_min", -100)):
        return False

    skill_req = req.get("skill")
    if skill_req:
        skill_name, min_level = skill_req
        level = float(
            getattr(getattr(sim, "skills", None), "levels", {}).get(skill_name, 0.0)
            or 0.0
        )
        if level < float(min_level):
            return False
    return True
