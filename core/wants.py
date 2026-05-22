from __future__ import annotations

import random
from typing import Optional, TYPE_CHECKING

from sim_types.sim_types import Fear, Want
from core.knowledge_aspiration import knowledge_fear_from_event, knowledge_wants

if TYPE_CHECKING:
    from core.sim import Sim


class WantsEngine:
    SOLO_WANTS = [
        ("improve my cooking skill", "fun", None),
        ("get some rest", "energy", None),
        ("eat something delicious", "hunger", None),
        ("exercise", "fun", None),
        ("have some alone time", "comfort", None),
        ("learn something new", "fun", None),
        ("clean up the place", "environment", None),
        ("earn more money", None, None),
    ]

    ASPIRATION_WANTS = {
        "Fortune": [
            ("get a raise or promotion", None, None),
            ("buy something expensive", None, None),
        ],
        "Family": [
            ("spend quality time with someone close", "social", None),
            ("host a dinner", "social", None),
            ("have a child with my partner", "social", None),
        ],
        "Popularity": [
            ("meet someone new", "social", None),
            ("throw a party", "social", None),
        ],
        "Knowledge": [
            ("read or learn something", "fun", None),
            ("have a deep conversation with someone", "social", None),
        ],
        "Romance": [
            ("flirt with someone", "social", None),
            ("go on a date", "social", None),
        ],
        "Creative": [
            ("make something artistic", "fun", None),
            ("share my creative work", "social", None),
        ],
    }

    def generate(self, sim, all_sim_ids: list[str]) -> list[Want]:
        wants: list[Want] = []
        pressures: dict[str, float] = sim.needs.pressure_vector()
        top_need = max(pressures, key=pressures.get)
        if pressures[top_need] > 0.5:
            wants.append(
                Want(
                    f"satisfy my {top_need} urgently",
                    None,
                    top_need,
                    pressures[top_need],
                )
            )

        aspiration_pool = self.ASPIRATION_WANTS.get(sim.profile["aspiration"], [])
        if aspiration_pool:
            description, need, _ = random.choice(aspiration_pool)
            target = None
            if need == "social" and all_sim_ids:
                candidates = [sim_id for sim_id in all_sim_ids if sim_id != sim.sim_id]
                target = random.choice(candidates) if candidates else None
            wants.append(
                Want(description, target, need, round(random.uniform(0.4, 0.75), 2))
            )

        if sim.profile.get("aspiration") == "Knowledge":
            for desc, need, prio in knowledge_wants(sim):
                target = None
                if need == "social" and all_sim_ids:
                    candidates = [sid for sid in all_sim_ids if sid != sim.sim_id]
                    target = random.choice(candidates) if candidates else None
                wants.append(Want(desc, target, need, round(float(prio), 2)))

        description, need, _ = random.choice(self.SOLO_WANTS)
        wants.append(Want(description, None, need, round(random.uniform(0.2, 0.5), 2)))
        return sorted(wants, key=lambda item: item.priority, reverse=True)

    def check_fear_acquisition(self, sim, event: str, valence: float) -> Optional[Fear]:
        if valence > -0.6:
            return None
        neuroticism = sim.profile["ocean"]["neuroticism"]
        if random.random() > neuroticism:
            return None
        fear_map = {
            "rejection": Fear("fear of rejection", neuroticism),
            "humiliation": Fear("fear of humiliation", neuroticism),
            "abandonment": Fear("fear of abandonment", neuroticism * 0.8),
            "commitment": Fear("fear of commitment", neuroticism * 0.6),
            "crowds": Fear("fear of crowds", neuroticism * 0.5),
        }
        for keyword, fear in fear_map.items():
            if keyword in event.lower():
                return fear
        if sim.profile.get("aspiration") == "Knowledge":
            return knowledge_fear_from_event(sim, event, valence)
        return None
