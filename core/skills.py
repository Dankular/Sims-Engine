"""core/skills.py — Per-sim skill tracking."""
import random
from config import SKILL_DEFINITIONS

# Keyword → (skill, xp_amount) mappings for interaction-based skill gain
INTERACTION_SKILL_MAP: list[tuple[list[str], str, float]] = [
    (["cook", "meal", "recipe", "kitchen", "food", "eat"], "cooking", 0.15),
    (["gourmet", "elegant dining", "fine dining", "fancy meal"], "gourmet_cooking", 0.15),
    (["bake", "cake", "pastry", "dessert", "bread"], "baking", 0.15),
    (["joke", "comedy", "roast", "funny", "laugh", "humour", "punchline"], "comedy", 0.15),
    (["charisma", "inspire", "enchant", "convince", "schmooze", "persuade"], "charisma", 0.10),
    (["story", "riveting", "captivate"], "charisma", 0.08),
    (["guitar", "serenade", "strum", "chord"], "guitar", 0.20),
    (["piano", "keys", "ivory", "keyboard"], "piano", 0.20),
    (["violin", "fiddle", "strings", "bow"], "violin", 0.20),
    (["sing", "vocal", "karaoke", "song", "lyrics"], "singing", 0.20),
    (["dj", "mix", "beat", "scratch", "drop"], "dj_mixing", 0.20),
    (["danc", "groove", "move to the music"], "dancing", 0.15),
    (["paint", "easel", "canvas", "artwork", "mural", "brush"], "painting", 0.15),
    (["write", "novel", "author", "essay", "poem", "story", "manuscript"], "writing", 0.15),
    (["photo", "camera", "picture", "snapshot", "portrait"], "photography", 0.15),
    (["chess", "debate", "puzzle", "analyse", "strategic"], "logic", 0.10),
    (["code", "program", "hack", "algorithm", "app", "script"], "programming", 0.15),
    (["rocket", "orbit", "launch", "spacecraft"], "rocket_science", 0.15),
    (["video game", "gaming", "speedrun", "stream", "esport"], "video_gaming", 0.10),
    (["workout", "exercise", "run", "gym", "fitness", "lift", "train"], "fitness", 0.15),
    (["meditat", "yoga", "wellness", "mindful", "breathe", "relax"], "wellness", 0.15),
    (["repair", "fix", "upgrade", "build", "craft", "handiness", "tool", "wrench"], "handiness", 0.15),
    (["garden", "plant", "grow", "seed", "harvest", "prune", "water"], "gardening", 0.15),
    (["fish", "catch", "bait", "lure", "cast", "angl"], "fishing", 0.15),
    (["cocktail", "bartend", "mix drink", "pour", "bartend"], "mixology", 0.15),
    (["prank", "tease", "mischief", "trick", "voodoo", "mischiev"], "mischief", 0.10),
    (["parent", "child", "discipline", "nurture", "raise", "bedtime story"], "parenting", 0.10),
]


class SkillsSystem:
    def __init__(self, profile: dict):
        self.levels: dict[str, float] = {}
        ocean = profile.get("ocean", {})
        interests = profile.get("interests", [])

        open_ = ocean.get("openness", 0.5)
        extra = ocean.get("extraversion", 0.5)
        consc = ocean.get("conscientiousness", 0.5)
        agree = ocean.get("agreeableness", 0.5)

        self.levels["charisma"]       = round(extra * 4 + random.uniform(0, 2), 1)
        self.levels["comedy"]         = round(extra * 2 + random.uniform(0, 2), 1)
        self.levels["mischief"]       = round((1 - agree) * 3 + random.uniform(0, 1), 1)
        self.levels["parenting"]      = round(consc * 2 + random.uniform(0, 1), 1)
        self.levels["mixology"]       = round(extra * 1.0 + random.uniform(0, 2), 1)
        self.levels["cooking"]        = round(("cooking" in interests) * 3 + random.uniform(0, 2), 1)
        self.levels["gourmet_cooking"]= round(("cooking" in interests) * 2 + random.uniform(0, 1), 1)
        self.levels["baking"]         = round(("cooking" in interests) * 1.5 + random.uniform(0, 1), 1)
        self.levels["writing"]        = round(open_ * 2 + ("writing" in interests) * 2 + random.uniform(0, 1), 1)
        self.levels["painting"]       = round(open_ * 2 + ("art" in interests) * 2 + random.uniform(0, 1), 1)
        self.levels["photography"]    = round(open_ * 1 + ("photography" in interests) * 2 + random.uniform(0, 1), 1)
        self.levels["guitar"]         = round(("music" in interests) * 3 + random.uniform(0, 2), 1)
        self.levels["piano"]          = round(("music" in interests) * 2 + random.uniform(0, 1), 1)
        self.levels["violin"]         = round(("music" in interests) * 2 + random.uniform(0, 1), 1)
        self.levels["singing"]        = round(("music" in interests) * 2 + extra + random.uniform(0, 1), 1)
        self.levels["dj_mixing"]      = round(("music" in interests) * 1.5 + extra + random.uniform(0, 1), 1)
        self.levels["dancing"]        = round(extra * 1.5 + ("dancing" in interests) * 2 + random.uniform(0, 1), 1)
        self.levels["logic"]          = round(open_ * 3 + random.uniform(0, 2), 1)
        self.levels["programming"]    = round(open_ * 2 + ("technology" in interests) * 3 + random.uniform(0, 1), 1)
        self.levels["rocket_science"] = round(open_ * 1.5 + random.uniform(0, 1), 1)
        self.levels["video_gaming"]   = round(("gaming" in interests) * 3 + random.uniform(0, 2), 1)
        self.levels["fitness"]        = round(("fitness" in interests) * 3 + random.uniform(0, 2), 1)
        self.levels["wellness"]       = round(("fitness" in interests) * 1.5 + random.uniform(0, 2), 1)
        self.levels["handiness"]      = round(consc * 2 + random.uniform(0, 2), 1)
        self.levels["gardening"]      = round(("nature" in interests) * 3 + random.uniform(0, 2), 1)
        self.levels["fishing"]        = round(("outdoors" in interests) * 2 + random.uniform(0, 2), 1)
        self.levels["creativity"]     = round(open_ * 3 + random.uniform(0, 1), 1)

        # Clamp to valid range
        for skill, defn in SKILL_DEFINITIONS.items():
            if skill in self.levels:
                self.levels[skill] = min(defn["max"], max(0.0, round(self.levels[skill], 1)))

    def gain_xp(self, skill: str, amount: float = 0.25) -> bool:
        """Returns True if this gain caused a level-up."""
        defn = SKILL_DEFINITIONS.get(skill)
        if defn is None:
            return False
        old_level = int(self.levels.get(skill, 0.0))
        new_val = min(defn["max"], self.levels.get(skill, 0.0) + amount)
        self.levels[skill] = round(new_val, 2)
        return int(new_val) > old_level

    def modifier(self, skill: str) -> float:
        max_level = SKILL_DEFINITIONS.get(skill, {}).get("max", 10)
        return self.levels.get(skill, 0.0) / max_level

    def unlocked_interactions(self) -> list[str]:
        unlocked = []
        for skill, defn in SKILL_DEFINITIONS.items():
            level = int(self.levels.get(skill, 0))
            for threshold, interaction in defn["unlocks"].items():
                if level >= threshold:
                    unlocked.append(interaction)
        return unlocked

    def gains_from_interaction(self, interaction: str) -> list[tuple[str, float]]:
        """Returns list of (skill, amount) pairs that apply based on interaction text."""
        low = interaction.lower()
        gains: list[tuple[str, float]] = []
        seen: set[str] = set()
        for keywords, skill, amount in INTERACTION_SKILL_MAP:
            if skill not in seen and any(kw in low for kw in keywords):
                if skill in SKILL_DEFINITIONS:
                    gains.append((skill, amount))
                    seen.add(skill)
        return gains

    def level(self, skill: str) -> float:
        """Return current level for a skill (0.0 if not tracked)."""
        return self.levels.get(skill, 0.0)

    def to_dict(self) -> dict:
        return {k: round(v, 2) for k, v in self.levels.items() if v > 0}
