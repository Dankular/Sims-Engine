import random

from config import SKILL_DEFINITIONS


class SkillsSystem:
    def __init__(self, profile: dict):
        self.levels: dict[str, float] = {}
        ocean = profile["ocean"]
        interests = profile["interests"]
        self.levels["charisma"] = round(
            ocean["extraversion"] * 4 + random.uniform(0, 2), 1
        )
        self.levels["cooking"] = round(
            ("cooking" in interests) * 3 + random.uniform(0, 2), 1
        )
        self.levels["fitness"] = round(
            ("fitness" in interests) * 3 + random.uniform(0, 2), 1
        )
        self.levels["logic"] = round(ocean["openness"] * 3 + random.uniform(0, 2), 1)
        self.levels["creativity"] = round(
            ocean["openness"] * 3 + random.uniform(0, 1), 1
        )
        self.levels["comedy"] = round(
            ocean["extraversion"] * 2 + random.uniform(0, 2), 1
        )
        for skill, definition in SKILL_DEFINITIONS.items():
            self.levels[skill] = min(definition["max"], self.levels.get(skill, 0))

    def gain_xp(self, skill: str, amount: float = 0.25) -> None:
        if skill in self.levels:
            max_level = SKILL_DEFINITIONS[skill]["max"]
            self.levels[skill] = round(min(max_level, self.levels[skill] + amount), 2)

    def modifier(self, skill: str) -> float:
        max_level = SKILL_DEFINITIONS.get(skill, {}).get("max", 10)
        return self.levels.get(skill, 0) / max_level

    def unlocked_interactions(self) -> list[str]:
        unlocked = []
        for skill, definition in SKILL_DEFINITIONS.items():
            level = int(self.levels.get(skill, 0))
            for threshold, interaction in definition["unlocks"].items():
                if level >= threshold:
                    unlocked.append(interaction)
        return unlocked
