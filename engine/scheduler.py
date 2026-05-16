import random
from typing import TYPE_CHECKING, Optional

from config import INTERACTION_TYPES, REL_ACQUAINTANCE, REL_FRIEND

if TYPE_CHECKING:
    from core.sim import Sim
    from core.relationships import RelationshipGraph, RelationshipRecord
    from datasets.loader import DatasetRegistry


def choose_interaction(
    sim_a: "Sim",
    sim_b: "Sim",
    relationship: "RelationshipRecord",
    current_tick: int = 0,
    datasets: Optional["DatasetRegistry"] = None,
) -> str:
    friendship_score = relationship.friendship
    mood = sim_a.emotion.dominant_valence
    ocean = sim_a.ocean
    candidates: list[tuple[str, float]] = []

    for action in INTERACTION_TYPES["friendly"]:
        candidates.append((action, 1.0 + ocean["extraversion"] * 0.5))

    if (
        sim_a.skills.levels.get("comedy", 0) > 1
        or "cheerful" in sim_a.profile["traits"]
    ):
        for action in INTERACTION_TYPES["funny"]:
            candidates.append((action, 0.8))

    if mood < 0.35 or "hot-headed" in sim_a.profile["traits"]:
        for action in INTERACTION_TYPES["mean"]:
            candidates.append((action, (1 - mood) * 0.6))

    if friendship_score >= REL_FRIEND:
        for action in INTERACTION_TYPES["deep"]:
            candidates.append((action, 0.7 + ocean["openness"] * 0.3))

    if friendship_score >= REL_ACQUAINTANCE:
        is_romantic = (
            "romantic" in sim_a.profile["traits"]
            or sim_a.profile["aspiration"] == "Romance"
        )
        if is_romantic or friendship_score >= REL_FRIEND:
            for action in INTERACTION_TYPES["romantic"]:
                candidates.append(
                    (action, 0.5 * (ocean["extraversion"] + ocean["openness"]) / 2)
                )

    for special in sim_a.skills.unlocked_interactions():
        candidates.append((special, 1.2))

    # ── Dataset-enhanced seeds ────────────────────────────────────────────────
    if datasets is not None:
        # EmpathDialogues — emotion-state seed (20% chance)
        if random.random() < 0.20 and datasets.empath_index:
            from datasets.empathetic import sample_empathetic_utterance
            empath = sample_empathetic_utterance(sim_a.emotion.dominant, datasets.empath_index)
            if empath:
                candidates.append((empath, 0.95))

        # ConvAI2 — quality-filtered seed (8% chance)
        if random.random() < 0.08 and datasets.convai2_seeds:
            candidates.append((random.choice(datasets.convai2_seeds), 0.85))

        # DailyDialog — venue-topic seed (12% chance, replaces generic seeds)
        if random.random() < 0.12 and datasets.daily_dialog_index:
            from datasets.daily_dialog import sample_for_venue
            venue_name = getattr(sim_a, "_current_venue_name", "")
            dd_seed = sample_for_venue(venue_name)
            if dd_seed:
                candidates.append((dd_seed, 0.92))

        # Multi-character dialogue seed (8% chance, reduced since DailyDialog covers it)
        elif random.random() < 0.08 and datasets.dialogue_actions:
            candidates.append((random.choice(datasets.dialogue_actions), 0.90))

        # Moral dilemma — moral_stories (5% chance, high weight when triggered)
        if random.random() < 0.05 and datasets.moral_stories:
            from datasets.moral_stories import sample_dilemma, format_dilemma_interaction
            dilemma = sample_dilemma()
            if dilemma:
                candidates.append((format_dilemma_interaction(dilemma), 1.8))

        # Moral choice — ninoscherrer/moralchoice (3% chance, prefers ambiguous)
        elif random.random() < 0.03 and datasets.moral_choice:
            from datasets.moral_choice import sample_moral_choice, format_moral_choice_interaction
            choice = sample_moral_choice(prefer_ambiguous=True)
            if choice:
                candidates.append((format_moral_choice_interaction(choice), 1.6))

        # AITA / reddit-ethics — community judgment dilemma (3% chance)
        if random.random() < 0.03 and hasattr(datasets, "aita_index") and datasets.aita_index:
            from datasets.aita import sample_aita_for_topic
            sim_state = {
                "emotion": sim_a.emotion.dominant,
                "simoleons": sim_a.simoleons,
                "career_performance": sim_a.career_performance,
                "romance": getattr(sim_a, "_current_romance", 0),
            }
            entry = sample_aita_for_topic(sim_state)
            if entry:
                candidates.append((
                    f"[COMMUNITY DILEMMA] {entry['text'][:300]}\n"
                    f"The community judged: {entry['verdict']}. "
                    f"How does Sim A respond given their personality?",
                    1.7,
                ))

        # EI Scenario — emotional intelligence test (3% chance)
        if random.random() < 0.03 and hasattr(datasets, "ei_scenarios") and datasets.ei_scenarios:
            from datasets.emotional_intelligence import sample_ei_scenario, format_ei_interaction
            ei = sample_ei_scenario()
            if ei:
                candidates.append((format_ei_interaction(ei), 1.7))

        # Class 3: Jokes — Comedy skill-gated (15% when comedy skill > 0)
        comedy_skill = sim_a.skills.levels.get("comedy", 0)
        if random.random() < 0.15 and comedy_skill > 0.5:
            if comedy_skill >= 5 and hasattr(datasets, "dadjokes") and datasets.dadjokes:
                from datasets.jokes import sample_dadjoke, format_dadjoke_interaction
                dj = sample_dadjoke()
                if dj:
                    candidates.append((format_dadjoke_interaction(dj), 0.9 + comedy_skill * 0.1))
            elif hasattr(datasets, "jokes_by_tier") and datasets.jokes_by_tier:
                from datasets.jokes import sample_joke_for_skill, format_joke_interaction
                joke = sample_joke_for_skill(comedy_skill)
                if joke:
                    candidates.append((format_joke_interaction(joke, comedy_skill),
                                       0.8 + comedy_skill * 0.08))

        # Class 5: Convince — Charisma-gated persuasion (4% when charisma > 3)
        charisma = sim_a.skills.levels.get("charisma", 0)
        if (random.random() < 0.04 and charisma >= 3
                and hasattr(datasets, "persuasion_args") and datasets.persuasion_args):
            from datasets.persuasion import sample_argument, format_convince_interaction
            arg = sample_argument()
            if arg:
                candidates.append((format_convince_interaction(arg), 1.0 + charisma * 0.1))

        # Class 6: Confession — friendship-gated (6% when friendship > 35)
        if (random.random() < 0.06 and friendship_score >= 35
                and hasattr(datasets, "confessions_index") and datasets.confessions_index):
            from datasets.confessions import sample_confession, format_confession_interaction
            fear_labels = [f.label for f in sim_a.fears]
            confession = sample_confession(
                sim_a.emotion.dominant, fear_labels, friendship_score
            )
            if confession:
                candidates.append((
                    format_confession_interaction(confession, friendship_score),
                    1.5 if friendship_score >= 65 else 1.1,
                ))

    # Deep support — MentalChat (friendship > 65 + target has active fears)
    if (
        datasets is not None
        and hasattr(datasets, "mental_chat_index")
        and datasets.mental_chat_index
        and friendship_score >= 65
        and (bool(sim_b.fears) or sim_b.profile["ocean"].get("neuroticism", 0) > 0.7)
    ):
        from datasets.mental_chat import sample_support_line
        fear_labels = [f.label for f in sim_b.fears]
        support = sample_support_line(fear_labels)
        if support:
            candidates.append((f"[DEEP SUPPORT] {support}", 2.0))

    # Vulnerable sim — prefer deep/supportive interactions
    if friendship_score >= REL_FRIEND:
        if (
            sim_a.profile["ocean"]["neuroticism"] > 0.7
            or sim_b.profile["ocean"]["neuroticism"] > 0.7
            or bool(sim_a.fears)
            or bool(sim_b.fears)
        ):
            for action in INTERACTION_TYPES["deep"]:
                candidates.append((action, 1.5))

    candidates = [
        (action, weight)
        for action, weight in candidates
        if not sim_a.is_on_cooldown(action, current_tick)
    ]
    if not candidates:
        sim_a._action_cooldowns.clear()
        return "say hello"
    actions, weights = zip(*candidates)
    return random.choices(actions, weights=weights, k=1)[0]


def pick_interaction_pair(sims: list["Sim"], relationships: "RelationshipGraph"):
    if len(sims) < 2:
        return None
    candidates = []
    for index, sim_a in enumerate(sims):
        for sim_b in sims[index + 1 :]:
            a_pressures = sim_a.needs.pressure_vector()
            urgent_non_social_a = max(
                a_pressures.get("bladder", 0),
                a_pressures.get("hunger", 0),
                a_pressures.get("energy", 0),
            )
            if urgent_non_social_a > 0.85:
                continue
            score = (
                sim_a.want_pressure_toward(sim_b.sim_id) * 0.5
                + sim_b.want_pressure_toward(sim_a.sim_id) * 0.3
                + a_pressures.get("social", 0) * 0.2
                + random.uniform(0, 0.15)
            )
            candidates.append((score, sim_a, sim_b))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][2]
