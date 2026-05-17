from __future__ import annotations

from dataclasses import dataclass
import random


@dataclass
class Recipe:
    recipe_id: str
    ingredients: list[str]
    appliances: list[str]
    skill_requirement: float
    prep_time: float
    cook_time: float
    base_restore: float
    emotional_effect: str | None = None


RECIPES: list[Recipe] = [
    Recipe("quick_snack", ["snack"], ["prep_surface"], 0, 0.5, 0.0, 12.0, None),
    Recipe(
        "grilled_cheese", ["bread", "cheese"], ["stove"], 1, 1.0, 1.0, 24.0, "comfort"
    ),
    Recipe("garden_salad", ["vegetable"], ["prep_surface"], 2, 1.0, 0.0, 18.0, "focus"),
    Recipe("pasta", ["pasta", "sauce"], ["stove"], 3, 1.2, 1.8, 30.0, "optimism"),
    Recipe(
        "baked_dessert", ["flour", "sugar"], ["oven"], 4, 1.5, 2.0, 16.0, "playfulness"
    ),
    Recipe(
        "gourmet_plate",
        ["protein", "vegetable", "spice"],
        ["stove", "oven"],
        6,
        2.0,
        2.2,
        34.0,
        "inspiration",
    ),
]


class CookingSystem:
    def __init__(self) -> None:
        self.last_meal_quality: dict[str, str] = {}

    def tick(self, engine) -> None:
        for sim in engine.sims:
            if sim.needs.hunger > 65:
                continue
            self._autonomous_cook(sim, engine)

    def _autonomous_cook(self, sim, engine) -> None:
        cooking_skill = sim.skills.levels.get("cooking", 0.0)
        available = [r for r in RECIPES if cooking_skill >= r.skill_requirement]
        if not available:
            return
        recipe = random.choice(available)
        quality = self._determine_quality(sim, recipe, engine)
        hunger_restore = recipe.base_restore * self._quality_hunger_modifier(quality)
        sim.needs.hunger = min(100.0, sim.needs.hunger + hunger_restore)
        sim.skills.gain_xp("cooking", 0.12)
        if "baked" in recipe.recipe_id:
            sim.skills.gain_xp("baking", 0.08)
        if recipe.skill_requirement >= 5:
            sim.skills.gain_xp("gourmet_cooking", 0.08)
        if recipe.emotional_effect:
            sim.emotion.add(
                recipe.emotional_effect,
                0.25,
                duration=3,
                source=f"meal:{recipe.recipe_id}",
            )
        self.last_meal_quality[sim.sim_id] = quality
        self._handle_failure_risk(sim, recipe)

    def _determine_quality(self, sim, recipe: Recipe, engine) -> str:
        skill = sim.skills.levels.get("cooking", 0.0)
        gourmet = sim.skills.levels.get("gourmet_cooking", 0.0)
        mood = sim.emotion.dominant
        cleanliness_bonus = 0.0
        room_id = sim.household_id or "public"
        room_map = (
            {r["room_id"]: r for r in engine.cleanliness.room_state()}
            if hasattr(engine, "cleanliness")
            else {}
        )
        room = room_map.get(room_id)
        if room:
            cleanliness_bonus += (room.get("cleanliness_score", 50.0) - 50.0) / 150.0
        score = skill * 0.7 + gourmet * 0.5 + cleanliness_bonus * 10.0
        if mood in {"inspiration", "focus", "optimism"}:
            score += 1.2
        elif mood in {"anger", "discomfort", "sadness"}:
            score -= 1.0
        score += random.uniform(-1.0, 1.4)
        if score < 1.2:
            return "terrible"
        if score < 2.5:
            return "poor"
        if score < 4.8:
            return "average"
        if score < 6.6:
            return "good"
        if score < 8.5:
            return "excellent"
        return "masterpiece"

    @staticmethod
    def _quality_hunger_modifier(quality: str) -> float:
        return {
            "terrible": 0.55,
            "poor": 0.75,
            "average": 1.0,
            "good": 1.15,
            "excellent": 1.3,
            "masterpiece": 1.5,
        }.get(quality, 1.0)

    def _handle_failure_risk(self, sim, recipe: Recipe) -> None:
        skill = sim.skills.levels.get("cooking", 0.0)
        instability = 0.0 if sim.emotion.dominant_valence > 0.4 else 0.12
        fire_risk = max(
            0.01, 0.18 + recipe.skill_requirement * 0.03 - skill * 0.02 + instability
        )
        if random.random() < fire_risk * 0.06:
            sim.hazard_flags["fire"] = min(
                1.0, sim.hazard_flags.get("fire", 0.0) + 0.25
            )
            sim.emotion.add("fear", 0.5, duration=3, source="kitchen_fire_risk")
