from __future__ import annotations

from dataclasses import dataclass
import random


TEEN_TITLES = {
    "platinum": "young_genius",
    "gold": "whiz_kid",
    "high_green": "smarty_pants",
    "low_green": "solid_student",
    "low_red": "addled_adolescent",
    "deep_red": "dense_dunce",
}

ADULT_TITLES = {
    "platinum": "savant_supreme",
    "gold": "impressive_intellect",
    "high_green": "free_thinker",
    "low_green": "brainstretcher",
    "low_red": "silly_goose",
    "deep_red": "incredible_ignoramus",
}

ELDER_TITLES = {
    "platinum": "senior_sage",
    "gold": "wizened_wise_one",
    "high_green": "mature_mastermind",
    "low_green": "well_read_whitehair",
    "low_red": "decaying_dullard",
    "deep_red": "senile_simpleton",
}

KNOWLEDGE_OCCULT_WANTS = [
    "investigate a supernatural rumor",
    "research alien contact patterns",
    "study an occult phenomenon",
]

KNOWLEDGE_MAJORS = ["mathematics", "physics"]

KNOWLEDGE_SCHOLARSHIPS = {
    "scholar_grant": 500.0,
    "athletics_award": 300.0,
    "engineering_award": 650.0,
    "culinary_award": 300.0,
    "visual_arts_award": 300.0,
    "genius_grant": 900.0,
    "undead_scholarship": 450.0,
    "alien_reparations_grant": 800.0,
}

KNOWLEDGE_LIFETIME_GOALS = {
    "chief_of_staff",
    "mad_scientist",
    "criminal_mastermind",
    "education_minister",
    "game_designer",
    "space_pirate",
    "media_magnate",
    "city_planner",
    "prestidigitator",
    "head_of_intelligence_agency",
    "game_president",
    "max_all_skills",
}


@dataclass
class KnowledgeAspirationState:
    curiosity: float = 0.5
    learning_drive: float = 0.5
    experimentation_bias: float = 0.45
    fearlessness: float = 0.45
    mastery_desire: float = 0.5
    academic_focus: float = 0.45
    fulfillment: float = 50.0
    unmet_desires: float = 0.0
    fear_penalties: float = 0.0
    aspiration_decay: float = 0.15
    title: str = "free_thinker"
    obsession: float = 0.0
    desperation: float = 0.0
    occult_curiosity: float = 0.4
    scholarships: list[str] = None
    major_preference: str = ""
    eureka_count: int = 0
    alien_contacts: int = 0


def bootstrap_knowledge_state(sim) -> None:
    if not hasattr(sim, "knowledge_aspiration"):
        sim.knowledge_aspiration = KnowledgeAspirationState()
    ks = sim.knowledge_aspiration
    if ks.scholarships is None:
        ks.scholarships = []
    if not ks.major_preference:
        ks.major_preference = random.choice(KNOWLEDGE_MAJORS)


def knowledge_wants(sim) -> list[tuple[str, str | None, float]]:
    bootstrap_knowledge_state(sim)
    ks = sim.knowledge_aspiration
    wants: list[tuple[str, str | None, float]] = []
    wants.append(("practice a core skill", "fun", 0.45 + ks.learning_drive * 0.2))
    wants.append(("study with focus", "fun", 0.4 + ks.academic_focus * 0.25))
    wants.append(
        ("experiment with a new technique", "fun", 0.35 + ks.experimentation_bias * 0.3)
    )

    logic = float(sim.skills.levels.get("logic", 0))
    if logic >= 3:
        wants.append(
            ("use telescope for observations", "fun", 0.35 + ks.curiosity * 0.25)
        )
    if logic >= 5 and ks.fearlessness >= 0.45:
        wants.append(
            (random.choice(KNOWLEDGE_OCCULT_WANTS), "fun", 0.4 + ks.curiosity * 0.25)
        )
    if max(sim.skills.levels.values() or [0]) >= 6:
        wants.append(
            (
                "mentor someone in a learned skill",
                "social",
                0.38 + ks.mastery_desire * 0.2,
            )
        )
    if ks.academic_focus > 0.6:
        wants.append(("finish assignments before leisure", "fun", 0.5))
    if ks.occult_curiosity > 0.55:
        wants.append(("document an occult transformation", "fun", 0.48))
    return wants


def knowledge_fear_from_event(sim, event: str, valence: float):
    bootstrap_knowledge_state(sim)
    if valence > -0.45:
        return None
    low = event.lower()
    mapping = {
        "bad grade": "fear of academic failure",
        "expulsion": "fear of expulsion",
        "rejection": "fear of humiliation",
        "burn food": "fear of skill failure",
        "lost game": "fear of intellectual decline",
    }
    for key, label in mapping.items():
        if key in low:
            from sim_types.sim_types import Fear

            sev = max(0.2, min(1.0, 0.45 + sim.ocean.get("neuroticism", 0.5) * 0.4))
            return Fear(label, sev)
    return None


def apply_knowledge_tick(sim, engine, current_tick: int) -> None:
    bootstrap_knowledge_state(sim)
    ks = sim.knowledge_aspiration
    traits = set(str(t).lower() for t in sim.profile.get("traits", []))

    if "genius" in traits or "geek" in traits:
        ks.curiosity = min(1.0, ks.curiosity + 0.004)
        ks.learning_drive = min(1.0, ks.learning_drive + 0.004)
    if "coward" in traits:
        ks.fearlessness = max(0.0, ks.fearlessness - 0.004)
    if "adventurous" in traits:
        ks.fearlessness = min(1.0, ks.fearlessness + 0.005)

    recent_skill_growth = sum(float(v) for v in sim.skills.levels.values()) / 100.0
    novelty = 0.2 if random.random() < ks.curiosity * 0.15 else 0.0
    gain = 0.5 + recent_skill_growth + novelty
    penalty = ks.aspiration_decay + ks.unmet_desires + ks.fear_penalties
    ks.fulfillment = max(0.0, min(100.0, ks.fulfillment + gain - penalty))
    ks.unmet_desires = max(0.0, ks.unmet_desires - 0.02)
    ks.fear_penalties = max(0.0, ks.fear_penalties - 0.03)
    ks.obsession = max(
        0.0, min(1.0, ks.obsession + (0.01 if ks.fulfillment > 75 else -0.004))
    )
    ks.desperation = max(
        0.0, min(1.0, ks.desperation + (0.02 if ks.fulfillment < 25 else -0.01))
    )

    _apply_knowledge_perks(sim)
    _run_eureka_system(sim)
    _run_alien_contact(sim, engine, current_tick)
    _apply_desperation_effects(sim)

    _update_title(sim)
    prof = dict(getattr(sim, "autonomy_profile", {}))
    prof["learning"] = min(1.0, prof.get("learning", 0.0) + 0.06 * ks.learning_drive)
    prof["curiosity"] = min(1.0, prof.get("curiosity", 0.0) + 0.08 * ks.curiosity)
    sim.autonomy_profile = prof


def register_knowledge_failure(sim, severity: float = 0.3) -> None:
    bootstrap_knowledge_state(sim)
    ks = sim.knowledge_aspiration
    ks.fear_penalties = min(3.0, ks.fear_penalties + severity)
    ks.unmet_desires = min(3.0, ks.unmet_desires + severity * 0.7)


def apply_academic_progression(sim, hour: int) -> None:
    bootstrap_knowledge_state(sim)
    ks = sim.knowledge_aspiration
    if sim.profile.get("aspiration") != "Knowledge":
        return
    if 8 <= hour <= 14:
        sim.school_performance = min(
            100.0, sim.school_performance + 0.18 + ks.academic_focus * 0.22
        )
    if 16 <= hour <= 22 and random.random() < 0.35 + ks.learning_drive * 0.25:
        sim.homework_progress = min(100.0, sim.homework_progress + 6.0)
    if sim.school_performance >= 70 and "scholar_grant" not in ks.scholarships:
        ks.scholarships.append("scholar_grant")
    if sim.skills.levels.get("logic", 0) >= 7 and "genius_grant" not in ks.scholarships:
        ks.scholarships.append("genius_grant")


def scholarship_value(sim) -> float:
    bootstrap_knowledge_state(sim)
    ks = sim.knowledge_aspiration
    return round(sum(KNOWLEDGE_SCHOLARSHIPS.get(k, 0.0) for k in ks.scholarships), 2)


def choose_knowledge_major(sim, majors: list[str]) -> str:
    bootstrap_knowledge_state(sim)
    ks = sim.knowledge_aspiration
    if ks.major_preference in majors:
        return ks.major_preference
    if majors:
        return majors[0]
    return "general studies"


def apply_occult_curiosity(sim) -> None:
    bootstrap_knowledge_state(sim)
    ks = sim.knowledge_aspiration
    if sim.profile.get("aspiration") != "Knowledge":
        return
    if sim.occult_type != "none":
        ks.occult_curiosity = min(1.0, ks.occult_curiosity + 0.015)
        ks.fear_penalties = max(0.0, ks.fear_penalties - 0.02)
        sim.emotion.add("curiosity", 0.4, duration=3, source="occult_study")


def knowledge_relationship_compatibility(sim_a, sim_b) -> float:
    a = sim_a.profile.get("aspiration") == "Knowledge"
    b = sim_b.profile.get("aspiration") == "Knowledge"
    if not (a or b):
        return 0.0
    traits_a = set(str(t).lower() for t in sim_a.profile.get("traits", []))
    traits_b = set(str(t).lower() for t in sim_b.profile.get("traits", []))
    strong = {"family_oriented", "ambitious", "disciplined"}
    medium = {"romantic", "adventurous"}
    weak = {"lazy", "impulsive", "anti_intellectual"}
    bonus = 0.0
    bonus += 0.05 * len((traits_a | traits_b) & strong)
    bonus += 0.02 * len((traits_a | traits_b) & medium)
    bonus -= 0.05 * len((traits_a | traits_b) & weak)
    return max(-0.15, min(0.15, bonus))


def _apply_knowledge_perks(sim) -> None:
    ks = sim.knowledge_aspiration
    if ks.fulfillment >= 60:
        sim.perks.add("knowledge_t1_decay_resistance")
    if ks.fulfillment >= 72:
        sim.perks.add("knowledge_t2_impart_knowledge")
    if ks.fulfillment >= 82:
        sim.perks.add("knowledge_t3_eureka")
    if ks.fulfillment >= 90 and sim.skills.levels.get("logic", 0) >= 8:
        sim.perks.add("knowledge_t4_summon_aliens")


def _run_eureka_system(sim) -> None:
    ks = sim.knowledge_aspiration
    if "knowledge_t3_eureka" not in sim.perks:
        return
    chance = 0.02 + ks.curiosity * 0.02 + ks.experimentation_bias * 0.02
    if random.random() < chance:
        best = max(sim.skills.levels, key=sim.skills.levels.get)
        sim.skills.gain_xp(best, 0.8)
        sim.emotion.add("inspired", 0.9, duration=5, source="eureka_moment")
        ks.eureka_count += 1


def _run_alien_contact(sim, engine, current_tick: int) -> None:
    ks = sim.knowledge_aspiration
    logic = sim.skills.levels.get("logic", 0)
    if logic < 6:
        return
    chance = 0.005 + ks.curiosity * 0.01
    if "knowledge_t4_summon_aliens" in sim.perks:
        chance += 0.01
    if random.random() < chance:
        sim.perks.add("alien_signal_detection")
        ks.alien_contacts += 1
        sim.emotion.add("anticipating", 0.6, duration=4, source="alien_contact")
        try:
            engine._bus.emit("alien_contact_hint", sim=sim, tick=current_tick)
        except Exception:
            pass


def _apply_desperation_effects(sim) -> None:
    ks = sim.knowledge_aspiration
    if ks.desperation < 0.75:
        return
    sim.emotion.add("anxious", 0.5, duration=3, source="aspiration_desperation")
    if random.random() < 0.08:
        sim.emotion.add("obsession", 0.6, duration=4, source="delusional_learning")


def _update_title(sim) -> None:
    ks = sim.knowledge_aspiration
    age = int(sim.profile.get("age", 25))
    bracket = ADULT_TITLES
    if age < 18:
        bracket = TEEN_TITLES
    elif age >= 60:
        bracket = ELDER_TITLES

    f = ks.fulfillment
    if f >= 85:
        ks.title = bracket["platinum"]
    elif f >= 70:
        ks.title = bracket["gold"]
    elif f >= 55:
        ks.title = bracket["high_green"]
    elif f >= 40:
        ks.title = bracket["low_green"]
    elif f >= 25:
        ks.title = bracket["low_red"]
    else:
        ks.title = bracket["deep_red"]
