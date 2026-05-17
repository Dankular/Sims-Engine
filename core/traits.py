from __future__ import annotations

from dataclasses import dataclass, field
import random


@dataclass(frozen=True)
class TraitDef:
    trait_id: str
    category: str
    hidden: bool = False
    discoverable_by: tuple[str, ...] = ("social", "observation")
    conflicts_with: tuple[str, ...] = ()
    age_gate: tuple[int, int] | None = None
    temporary: bool = False
    source: str = "personality"
    emotion_mods: dict[str, float] = field(default_factory=dict)
    need_decay_mods: dict[str, float] = field(default_factory=dict)
    skill_gain_mods: dict[str, float] = field(default_factory=dict)
    career_mods: dict[str, float] = field(default_factory=dict)
    relationship_mods: dict[str, float] = field(default_factory=dict)
    interaction_unlocks: tuple[str, ...] = ()
    interaction_blocks: tuple[str, ...] = ()
    autonomy_weights: dict[str, float] = field(default_factory=dict)


DEFAULT_AUTONOMY = {
    "social": 0.0,
    "solitude": 0.0,
    "career_focus": 0.0,
    "leisure": 0.0,
    "conflict": 0.0,
    "harmony": 0.0,
    "romance": 0.0,
    "outdoors": 0.0,
    "shopping": 0.0,
    "learning": 0.0,
}


TRAIT_DEFS: dict[str, TraitDef] = {
    "ambitious": TraitDef(
        "ambitious",
        "personality",
        autonomy_weights={"career_focus": 0.6, "learning": 0.3},
        career_mods={"performance": 0.1},
        skill_gain_mods={"all": 0.08},
    ),
    "lazy": TraitDef(
        "lazy",
        "personality",
        conflicts_with=("active", "overachiever"),
        autonomy_weights={"leisure": 0.5, "career_focus": -0.5, "learning": -0.25},
        need_decay_mods={"energy": 0.85},
    ),
    "active": TraitDef(
        "active",
        "lifestyle",
        conflicts_with=("lazy",),
        autonomy_weights={"outdoors": 0.4, "learning": 0.15},
        need_decay_mods={"energy": 1.1},
    ),
    "romantic": TraitDef(
        "romantic",
        "emotional",
        conflicts_with=("unflirty",),
        autonomy_weights={"romance": 0.7, "social": 0.2},
        relationship_mods={"romance_gain": 0.15},
        interaction_unlocks=("hold hands", "express love"),
    ),
    "unflirty": TraitDef(
        "unflirty",
        "emotional",
        conflicts_with=("romantic",),
        autonomy_weights={"romance": -0.8, "solitude": 0.2},
        relationship_mods={"romance_gain": -0.2},
        interaction_blocks=("flirt", "compliment appearance"),
    ),
    "cheerful": TraitDef(
        "cheerful",
        "emotional",
        emotion_mods={"joy": 0.2},
        autonomy_weights={"social": 0.3, "harmony": 0.2},
    ),
    "gloomy": TraitDef(
        "gloomy",
        "emotional",
        emotion_mods={"sadness": 0.2},
        autonomy_weights={"solitude": 0.25, "social": -0.2},
    ),
    "hot-headed": TraitDef(
        "hot-headed",
        "emotional",
        autonomy_weights={"conflict": 0.6, "harmony": -0.4},
        relationship_mods={"friendship_gain": -0.1},
    ),
    "good": TraitDef(
        "good",
        "social",
        conflicts_with=("evil", "mean"),
        autonomy_weights={"harmony": 0.5, "conflict": -0.35},
        relationship_mods={"friendship_gain": 0.12},
    ),
    "evil": TraitDef(
        "evil",
        "social",
        conflicts_with=("good", "generous"),
        hidden=True,
        discoverable_by=("social", "observation", "analysis"),
        autonomy_weights={"conflict": 0.5, "harmony": -0.4},
    ),
    "mean": TraitDef(
        "mean",
        "social",
        conflicts_with=("good", "generous"),
        autonomy_weights={"conflict": 0.45, "social": 0.1},
        relationship_mods={"friendship_gain": -0.15},
    ),
    "family-oriented": TraitDef(
        "family-oriented",
        "social",
        autonomy_weights={"social": 0.35, "harmony": 0.25},
        relationship_mods={"friendship_gain": 0.1, "family_bond": 0.2},
    ),
    "loner": TraitDef(
        "loner", "social", autonomy_weights={"solitude": 0.6, "social": -0.45}
    ),
    "creative": TraitDef(
        "creative",
        "aspirational",
        autonomy_weights={"learning": 0.35, "leisure": 0.2},
        skill_gain_mods={"creativity": 0.2},
    ),
    "bookworm": TraitDef(
        "bookworm",
        "aspirational",
        autonomy_weights={"learning": 0.45, "solitude": 0.15},
        skill_gain_mods={"logic": 0.15, "writing": 0.15},
    ),
    "geek": TraitDef(
        "geek",
        "aspirational",
        autonomy_weights={"learning": 0.35, "social": -0.1},
        skill_gain_mods={"logic": 0.12, "programming": 0.2},
    ),
    "materialistic": TraitDef(
        "materialistic",
        "lifestyle",
        autonomy_weights={"shopping": 0.6, "career_focus": 0.25},
    ),
    "neat": TraitDef(
        "neat",
        "lifestyle",
        conflicts_with=("slob",),
        autonomy_weights={"harmony": 0.1},
        need_decay_mods={"hygiene": 0.9},
    ),
    "slob": TraitDef(
        "slob",
        "lifestyle",
        conflicts_with=("neat",),
        autonomy_weights={"leisure": 0.15},
        need_decay_mods={"hygiene": 1.2},
    ),
}


TRAIT_COMPATIBILITY: dict[tuple[str, str], float] = {
    ("romantic", "romantic"): 0.08,
    ("good", "good"): 0.05,
    ("loner", "loner"): 0.03,
    ("hot-headed", "hot-headed"): -0.06,
    ("good", "mean"): -0.08,
    ("good", "evil"): -0.1,
    ("romantic", "unflirty"): -0.14,
}


def active_traits(sim) -> set[str]:
    base = set(sim.profile.get("traits", []))
    return (
        base
        | set(getattr(sim, "reward_traits", set()))
        | set(getattr(sim, "temporary_traits", set()))
        | set(getattr(sim, "death_traits", set()))
    )


def apply_trait_conflicts(traits: set[str]) -> set[str]:
    resolved = set(traits)
    for tid in list(resolved):
        tdef = TRAIT_DEFS.get(tid)
        if not tdef:
            continue
        for conflict in tdef.conflicts_with:
            if conflict in resolved:
                if tid <= conflict:
                    resolved.discard(conflict)
                else:
                    resolved.discard(tid)
    return resolved


def derive_autonomy_profile(sim) -> dict[str, float]:
    profile = dict(DEFAULT_AUTONOMY)
    for tid in apply_trait_conflicts(active_traits(sim)):
        tdef = TRAIT_DEFS.get(tid)
        if not tdef:
            continue
        for key, delta in tdef.autonomy_weights.items():
            profile[key] = profile.get(key, 0.0) + float(delta)
    for key, value in list(profile.items()):
        profile[key] = max(-1.0, min(1.0, round(value, 3)))
    return profile


def interaction_weight_modifier(sim, interaction: str) -> float:
    inter = interaction.lower()
    profile = getattr(sim, "autonomy_profile", DEFAULT_AUTONOMY)
    mod = 1.0
    if any(word in inter for word in ("flirt", "date", "love", "kiss", "hands")):
        mod += profile.get("romance", 0.0) * 0.35
    if any(word in inter for word in ("argue", "insult", "mock", "confront")):
        mod += profile.get("conflict", 0.0) * 0.35
    if any(word in inter for word in ("chat", "story", "confide", "talk")):
        mod += profile.get("social", 0.0) * 0.2
        mod -= profile.get("solitude", 0.0) * 0.2
    return max(0.35, min(2.2, mod))


def trait_blocks_interaction(sim, interaction: str) -> bool:
    inter = interaction.lower()
    for tid in active_traits(sim):
        tdef = TRAIT_DEFS.get(tid)
        if tdef and any(block in inter for block in tdef.interaction_blocks):
            return True
    return False


def trait_unlocks(sim) -> list[str]:
    unlocked: list[str] = []
    for tid in active_traits(sim):
        tdef = TRAIT_DEFS.get(tid)
        if tdef:
            unlocked.extend(tdef.interaction_unlocks)
    return unlocked


def skill_gain_multiplier(sim, skill: str) -> float:
    mult = 1.0
    for tid in active_traits(sim):
        tdef = TRAIT_DEFS.get(tid)
        if not tdef:
            continue
        mult += float(tdef.skill_gain_mods.get("all", 0.0))
        mult += float(tdef.skill_gain_mods.get(skill, 0.0))
    return max(0.4, min(2.0, mult))


def career_performance_multiplier(sim) -> float:
    mult = 1.0
    for tid in active_traits(sim):
        tdef = TRAIT_DEFS.get(tid)
        if tdef:
            mult += float(tdef.career_mods.get("performance", 0.0))
    return max(0.7, min(1.6, mult))


def relationship_growth_multiplier(sim, channel: str = "friendship_gain") -> float:
    mult = 1.0
    for tid in active_traits(sim):
        tdef = TRAIT_DEFS.get(tid)
        if tdef:
            mult += float(tdef.relationship_mods.get(channel, 0.0))
    return max(0.5, min(1.8, mult))


def trait_compatibility_bonus(sim_a, sim_b) -> float:
    bonus = 0.0
    a_traits = active_traits(sim_a)
    b_traits = active_traits(sim_b)
    for a in a_traits:
        for b in b_traits:
            bonus += TRAIT_COMPATIBILITY.get(
                (a, b), TRAIT_COMPATIBILITY.get((b, a), 0.0)
            )
    return max(-0.25, min(0.25, bonus))


def discover_traits(
    observer,
    target,
    relationship_strength: float,
    social_reveal: bool,
    observation_bias: float = 0.0,
) -> list[str]:
    known_map = getattr(observer, "trait_knowledge", {})
    known = set(known_map.get(target.sim_id, {}).get("known_traits", []))
    target_traits = list(active_traits(target))
    newly: list[str] = []
    rel_bonus = max(0.0, min(0.25, relationship_strength / 400.0))
    base = 0.08 + rel_bonus + observation_bias
    if social_reveal:
        base += 0.18
    if "wise" in active_traits(observer) or "nosy" in active_traits(observer):
        base += 0.06
    for tid in target_traits:
        if tid in known:
            continue
        tdef = TRAIT_DEFS.get(tid)
        reveal_chance = base * (0.6 if tdef and tdef.hidden else 1.0)
        if random.random() < min(0.85, reveal_chance):
            known.add(tid)
            newly.append(tid)
    if newly:
        payload = known_map.setdefault(
            target.sim_id,
            {"known_traits": [], "suspected_traits": {}, "confidence": {}},
        )
        payload["known_traits"] = sorted(known)
        for tid in newly:
            payload["confidence"][tid] = 1.0
    observer.trait_knowledge = known_map
    return newly
