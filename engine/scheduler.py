import random
from typing import TYPE_CHECKING, Optional

from config import INTERACTION_TYPES, REL_ACQUAINTANCE, REL_FRIEND

if TYPE_CHECKING:
    from core.sim import Sim
    from core.relationships import RelationshipGraph, RelationshipRecord
    from datasets.loader import DatasetRegistry

# Pre-build reverse mapping: action → category, for NLI weight boosting
_ACTION_TO_CATEGORY: dict[str, str] = {}
for _cat, _actions in INTERACTION_TYPES.items():
    for _a in _actions:
        _ACTION_TO_CATEGORY[_a] = _cat

# NLI label → INTERACTION_TYPES category
_NLI_LABEL_TO_CAT: dict[str, str] = {
    "deep emotional conversation about feelings or fears": "deep",
    "romantic or flirtatious interaction":                 "romantic",
    "playful funny casual interaction":                    "funny",
    "hostile argumentative confrontation":                 "mean",
    "casual friendly conversation":                        "friendly",
}
_NLI_INTERACTION_LABELS = list(_NLI_LABEL_TO_CAT.keys())


def _nli_boost_candidates(
    sim_a: "Sim",
    sim_b: "Sim",
    rel: "RelationshipRecord",
    candidates: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """
    System 1 — Scheduler NLI routing.
    Classifies the Sim-pair state into an interaction category and boosts
    matching candidate weights by 2×.  Falls back to unchanged list.
    """
    try:
        from llm.small_models import zero_shot_classify
        from core.arcs import is_lonely

        parts = [f"{sim_a.name}: emotion={sim_a.emotion.dominant}"]
        if sim_a.grief_stage >= 0:
            parts.append(f"grief_stage={sim_a.grief_stage}")
        if is_lonely(sim_a):
            parts.append("socially_isolated")
        if getattr(sim_a, "_burnout_active", False):
            parts.append("burnt_out")

        state = (
            f"{', '.join(parts)}. "
            f"{sim_b.name}: emotion={sim_b.emotion.dominant}. "
            f"Relationship: {rel.state_label()}, "
            f"friendship={rel.friendship:.0f}, romance={rel.romance:.0f}."
        )

        result = zero_shot_classify(state, _NLI_INTERACTION_LABELS, threshold=0.38)
        if result is None:
            return candidates

        predicted_cat = _NLI_LABEL_TO_CAT.get(result[0], "")
        if not predicted_cat:
            return candidates

        boosted_actions = set(INTERACTION_TYPES.get(predicted_cat, []))
        return [
            (action, weight * 2.0 if action in boosted_actions else weight)
            for action, weight in candidates
        ]
    except Exception:
        return candidates


def choose_interaction(
    sim_a: "Sim",
    sim_b: "Sim",
    relationship: "RelationshipRecord",
    current_tick: int = 0,
    datasets: Optional["DatasetRegistry"] = None,
) -> str:
    # ── System 4: Goal-driven interaction takes priority ──────────────────────
    goal = getattr(sim_a, "_active_goal", None)
    if goal is not None:
        try:
            from core.goals import is_goal_valid, goal_to_interaction
            if (
                is_goal_valid(goal, current_tick)
                and goal.target_sim == sim_b.sim_id
                and not sim_a.is_on_cooldown(goal.action_type, current_tick)
            ):
                return goal_to_interaction(goal)
        except Exception:
            pass

    friendship_score = relationship.friendship
    romance_score = relationship.romance
    mood = sim_a.emotion.dominant_valence
    ocean = sim_a.ocean
    candidates: list[tuple[str, float]] = []

    # ── Reputation gating — adjust base weights before building candidate list ─
    rep_b = getattr(sim_b, "reputation_score", 0.0)
    rep_a = getattr(sim_a, "reputation_score", 0.0)
    # How willing sim_a is to be warm toward sim_b (0.5 if villainous, 1.5 if celebrated)
    warmth_mod = max(0.5, min(1.5, 1.0 + rep_b / 100.0))
    # Sims with bad own reputation lean toward hostile/defensive interactions
    hostility_mod = max(1.0, 1.0 + (-rep_a / 80.0))

    for action in INTERACTION_TYPES["friendly"]:
        candidates.append((action, (1.0 + ocean["extraversion"] * 0.5) * warmth_mod))

    if (
        sim_a.skills.levels.get("comedy", 0) > 1
        or "cheerful" in sim_a.profile["traits"]
    ):
        for action in INTERACTION_TYPES["funny"]:
            candidates.append((action, 0.8))

    if mood < 0.35 or "hot-headed" in sim_a.profile["traits"]:
        for action in INTERACTION_TYPES["mean"]:
            candidates.append((action, (1 - mood) * 0.6 * hostility_mod))

    if friendship_score >= REL_FRIEND:
        for action in INTERACTION_TYPES["deep"]:
            candidates.append((action, (0.7 + ocean["openness"] * 0.3) * warmth_mod))

    if friendship_score >= REL_ACQUAINTANCE:
        is_romantic = (
            "romantic" in sim_a.profile["traits"]
            or sim_a.profile["aspiration"] == "Romance"
        )
        if is_romantic or friendship_score >= REL_FRIEND:
            romantic_actions: list[str] = []
            if romance_score < 30:
                romantic_actions = ["flirt", "compliment appearance"]
            elif romance_score < 55:
                romantic_actions = ["flirt", "compliment appearance", "hold hands"]
            else:
                romantic_actions = INTERACTION_TYPES["romantic"]
            for action in romantic_actions:
                candidates.append(
                    (action, 0.5 * (ocean["extraversion"] + ocean["openness"]) / 2)
                )
            _both_adult = (
                sim_a.profile.get("age", 0) >= 16
                and sim_b.profile.get("age", 0) >= 16
            )
            if romance_score >= 65 and "intimate" in INTERACTION_TYPES and _both_adult:
                for action in INTERACTION_TYPES["intimate"]:
                    candidates.append((action, 0.42 + romance_score / 250.0))

    for special in sim_a.skills.unlocked_interactions():
        candidates.append((special, 1.2))

    # ── Marriage proposal — unlocked at high romance + no current marriage ────
    if (
        romance_score >= 85
        and not getattr(sim_a, "_married_to", None)
        and not getattr(sim_b, "_married_to", None)
        and "first_love" in {s.name for s in getattr(relationship, "sentiments", [])}
    ):
        candidates.append(("propose marriage", 0.8))

    # ── Interest-match bonding ────────────────────────────────────────────────
    if datasets is not None and hasattr(datasets, "interests_data") and datasets.interests_data:
        interests_a = set(sim_a.profile.get("interests", []))
        interests_b = set(sim_b.profile.get("interests", []))
        shared = interests_a & interests_b
        solo   = interests_a - interests_b  # only sim_a has it

        # Shared interest — highest weight (mutual passion)
        if shared and random.random() < 0.25:
            interest = random.choice(list(shared))
            from datasets.interests import sample_interest_seed, format_interest_interaction
            seed = sample_interest_seed(interest, datasets.interests_data)
            if seed:
                bonus = 0.3 if len(shared) > 1 else 0.0
                candidates.append((
                    format_interest_interaction(seed, sim_a.name, sim_b.name, shared=True),
                    1.5 + bonus,
                ))

        # Solo interest — lower weight (one-sided enthusiasm)
        elif solo and random.random() < 0.12:
            interest = random.choice(list(solo))
            from datasets.interests import sample_interest_seed, format_interest_interaction
            seed = sample_interest_seed(interest, datasets.interests_data)
            if seed:
                candidates.append((
                    format_interest_interaction(seed, sim_a.name, sim_b.name, shared=False),
                    1.0,
                ))

    # ── Dataset-enhanced seeds ────────────────────────────────────────────────
    if datasets is not None:
        # EmpathDialogues — emotion-state seed (20% chance)
        if random.random() < 0.20 and datasets.empath_index:
            from datasets.empathetic import sample_empathetic_utterance

            empath = sample_empathetic_utterance(
                sim_a.emotion.dominant, datasets.empath_index
            )
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

        # SODA — naturalistic social dialogue seed (12% chance, high quality)
        if (
            random.random() < 0.12
            and hasattr(datasets, "soda_index")
            and datasets.soda_index
        ):
            from datasets.soda import sample_soda_seed

            soda_seed = sample_soda_seed(sim_a.emotion.dominant)
            if soda_seed:
                candidates.append((soda_seed, 0.93))

        # blended_skill_talk — varied register seed (10% chance)
        elif (
            random.random() < 0.10
            and hasattr(datasets, "blended_skill")
            and datasets.blended_skill
        ):
            from datasets.blended_skill import sample_blended_utterance

            # Choose skill based on OCEAN
            ocean = sim_a.ocean
            if ocean["agreeableness"] > 0.6:
                skill = "empathy"
            elif ocean["openness"] > 0.6:
                skill = "knowledge"
            else:
                skill = "persona"
            utterance = sample_blended_utterance(skill)
            if utterance:
                candidates.append((utterance, 0.90))

        # Multi-character dialogue seed (5% chance, lowest priority now)
        elif random.random() < 0.05 and datasets.dialogue_actions:
            candidates.append((random.choice(datasets.dialogue_actions), 0.88))

        # Moral dilemma — moral_stories (5% chance, high weight when triggered)
        if random.random() < 0.05 and datasets.moral_stories:
            from datasets.moral_stories import (
                sample_dilemma,
                format_dilemma_interaction,
            )

            dilemma = sample_dilemma()
            if dilemma:
                candidates.append((format_dilemma_interaction(dilemma), 1.8))

        # Moral choice — ninoscherrer/moralchoice (3% chance, prefers ambiguous)
        elif random.random() < 0.03 and datasets.moral_choice:
            from datasets.moral_choice import (
                sample_moral_choice,
                format_moral_choice_interaction,
            )

            choice = sample_moral_choice(prefer_ambiguous=True)
            if choice:
                candidates.append((format_moral_choice_interaction(choice), 1.6))

        # AITA / reddit-ethics — community judgment dilemma (3% chance)
        if (
            random.random() < 0.03
            and hasattr(datasets, "aita_index")
            and datasets.aita_index
        ):
            from datasets.aita import sample_aita_for_topic

            sim_state = {
                "emotion": sim_a.emotion.dominant,
                "simoleons": sim_a.simoleons,
                "career_performance": sim_a.career_performance,
                "romance": getattr(sim_a, "_current_romance", 0),
            }
            entry = sample_aita_for_topic(sim_state)
            if entry:
                candidates.append(
                    (
                        f"[COMMUNITY DILEMMA] {entry['text'][:300]}\n"
                        f"The community judged: {entry['verdict']}. "
                        f"How does Sim A respond given their personality?",
                        1.7,
                    )
                )

        # EI Scenario — emotional intelligence test (3% chance)
        if (
            random.random() < 0.03
            and hasattr(datasets, "ei_scenarios")
            and datasets.ei_scenarios
        ):
            from datasets.emotional_intelligence import (
                sample_ei_scenario,
                format_ei_interaction,
            )

            ei = sample_ei_scenario()
            if ei:
                candidates.append((format_ei_interaction(ei), 1.7))

        # Class 3: Jokes — Comedy skill-gated (15% when comedy skill > 0)
        comedy_skill = sim_a.skills.levels.get("comedy", 0)
        if random.random() < 0.15 and comedy_skill > 0.5:
            if (
                comedy_skill >= 5
                and hasattr(datasets, "dadjokes")
                and datasets.dadjokes
            ):
                from datasets.jokes import sample_dadjoke, format_dadjoke_interaction

                dj = sample_dadjoke()
                if dj:
                    candidates.append(
                        (format_dadjoke_interaction(dj), 0.9 + comedy_skill * 0.1)
                    )
            elif hasattr(datasets, "jokes_by_tier") and datasets.jokes_by_tier:
                from datasets.jokes import (
                    sample_joke_for_skill,
                    format_joke_interaction,
                )

                joke = sample_joke_for_skill(comedy_skill)
                if joke:
                    candidates.append(
                        (
                            format_joke_interaction(joke, comedy_skill),
                            0.8 + comedy_skill * 0.08,
                        )
                    )

        # Class 5: Convince — Charisma-gated persuasion (4% when charisma > 3)
        charisma = sim_a.skills.levels.get("charisma", 0)
        if (
            random.random() < 0.04
            and charisma >= 3
            and hasattr(datasets, "persuasion_args")
            and datasets.persuasion_args
        ):
            from datasets.persuasion import sample_argument, format_convince_interaction

            arg = sample_argument()
            if arg:
                candidates.append(
                    (format_convince_interaction(arg), 1.0 + charisma * 0.1)
                )

        # Class 6: Confession — friendship-gated (6% when friendship > 35)
        if (
            random.random() < 0.06
            and friendship_score >= 35
            and hasattr(datasets, "confessions_index")
            and datasets.confessions_index
        ):
            from datasets.confessions import (
                sample_confession,
                format_confession_interaction,
            )

            fear_labels = [f.label for f in sim_a.fears]
            confession = sample_confession(
                sim_a.emotion.dominant, fear_labels, friendship_score
            )
            if confession:
                candidates.append(
                    (
                        format_confession_interaction(confession, friendship_score),
                        1.5 if friendship_score >= 65 else 1.1,
                    )
                )

        # Self-disclosure depth curve — stage-appropriate confiding
        if (
            random.random() < 0.06
            and friendship_score >= 20
            and hasattr(datasets, "self_disclosure_depth")
            and datasets.self_disclosure_depth
        ):
            from datasets.self_disclosure import sample_by_depth

            disclosure, depth = sample_by_depth(friendship_score)
            if disclosure:
                note = ""
                if depth == "deep" and friendship_score < 40:
                    note = " Early over-disclosure risk: embarrassment and friendship penalty likely."
                elif depth == "surface" and friendship_score >= 65:
                    note = " At this closeness, under-disclosure can feel avoidant and reduce warmth."
                candidates.append(
                    (
                        f"[SELF-DISCLOSURE — {depth.upper()}]\n"
                        f'Sim A shares: "{disclosure[:260]}"\n'
                        f"Adjudicate trust fit for current friendship tier.{note}",
                        1.2 if depth != "deep" else 1.5,
                    )
                )

        # Romance dataset grounding — flirtflip tiers + charisma rizz unlock
        if friendship_score >= REL_ACQUAINTANCE and hasattr(
            datasets, "flirtflip_index"
        ):
            from datasets.romance import sample_flirt_line, sample_rizz_intro

            line, tier = sample_flirt_line(romance_score)
            charisma = sim_a.skills.levels.get("charisma", 0)
            if line:
                early_bold_risk = ""
                if tier == "bold" and romance_score < (45 if charisma >= 7 else 55):
                    early_bold_risk = (
                        " Early escalation risk: if Sim B has low agreeableness, "
                        "annoyance/disgust is likely."
                    )
                candidates.append(
                    (
                        f"[ROMANCE — {tier.upper()}]\n"
                        f'Sim A attempts: "{line}"\n'
                        f"Style is tiered by romance score ({romance_score:.0f}).{early_bold_risk}",
                        0.95 + (romance_score / 180),
                    )
                )
            if charisma >= 7 and random.random() < 0.20:
                rizz = sample_rizz_intro()
                if rizz:
                    candidates.append(
                        (
                            f"[ENCHANTING INTRODUCTION]\n"
                            f'Sim A delivers a high-chemistry opening: "{rizz[:220]}"',
                            1.6,
                        )
                    )

        # Partners-state attachment dynamics (INTIMA)
        if (
            romance_score >= 80
            and hasattr(datasets, "intima_codes")
            and datasets.intima_codes
        ):
            from datasets.intimacy import sample_intima

            prompt = sample_intima(sim_a.profile.get("attachment", "general"))
            if prompt:
                candidates.append(
                    (
                        f"[PARTNERS DYNAMICS]\n"
                        f"Attachment-aware interaction seed: {prompt[:320]}",
                        1.35,
                    )
                )

        # Adult tier 1: suggestive but non-explicit intimate register
        # Age-gated: both sims must be >= 16
        _both_of_age = (
            sim_a.profile.get("age", 0) >= 16
            and sim_b.profile.get("age", 0) >= 16
        )
        if (
            romance_score >= 65
            and _both_of_age
            and hasattr(datasets, "sensual_patterns")
            and datasets.sensual_patterns
        ):
            line = random.choice(datasets.sensual_patterns)
            candidates.append(
                (
                    f"[INTIMATE — SUGGESTIVE]\n{line[:260]}",
                    1.1,
                )
            )

        # Gap 1: Debate — logic skill-gated (8% when logic >= 3)
        logic_skill = sim_a.skills.levels.get("logic", 0)
        if random.random() < 0.08 and logic_skill >= 3:
            if hasattr(datasets, "debate_index") and datasets.debate_index:
                from datasets.debate import (
                    sample_debate_argument,
                    format_debate_interaction,
                )

                arg = sample_debate_argument(logic_skill)
                if arg:
                    candidates.append(
                        (
                            format_debate_interaction(arg, logic_skill),
                            1.0 + logic_skill * 0.12,
                        )
                    )

        # Gap 2: Cooking — skill-gated (8% when cooking >= 3)
        cooking_skill = sim_a.skills.levels.get("cooking", 0)
        if random.random() < 0.08 and cooking_skill >= 3:
            if hasattr(datasets, "cooking_dialogs") and datasets.cooking_dialogs:
                from datasets.cooking import sample_recipe, format_cooking_interaction

                recipe = sample_recipe(cooking_skill)
                if recipe:
                    guest_diets = [sim_b.profile.get("diet", "omnivore")]
                    candidates.append(
                        (
                            format_cooking_interaction(
                                recipe, cooking_skill, guest_diets
                            ),
                            1.0 + cooking_skill * 0.08,
                        )
                    )

        # Gap 3: Creativity — skill-gated (10% when creativity >= 2)
        creativity_skill = sim_a.skills.levels.get("creativity", 0)
        if random.random() < 0.10 and creativity_skill >= 2:
            if hasattr(datasets, "creative_works") and datasets.creative_works:
                from datasets.creative_works import (
                    sample_creative_work,
                    format_creative_interaction,
                )

                work = sample_creative_work(creativity_skill)
                if work:
                    candidates.append(
                        (
                            format_creative_interaction(work, creativity_skill),
                            1.0 + creativity_skill * 0.1,
                        )
                    )

        # Gap 4: Manipulation — toxic initiator (5% when conditions met)
        if random.random() < 0.05 and hasattr(datasets, "manipulation_index"):
            from datasets.manipulation import (
                is_toxic_initiator,
                sample_manipulation,
                format_manipulation_interaction,
            )

            if is_toxic_initiator(sim_a):
                manip = sample_manipulation()
                if manip:
                    candidates.append((format_manipulation_interaction(manip), 1.6))

        # Gap 1: Fitness — skill-gated (8% when fitness >= 1)
        fitness_skill = sim_a.skills.levels.get("fitness", 0)
        if random.random() < 0.08 and fitness_skill >= 1:
            if hasattr(datasets, "fitness_content") and datasets.fitness_content:
                from datasets.fitness import (
                    sample_fitness_content,
                    format_fitness_interaction,
                )

                item = sample_fitness_content(fitness_skill)
                if item:
                    fitness_b = sim_b.skills.levels.get("fitness", 0)
                    candidates.append(
                        (
                            format_fitness_interaction(item, fitness_skill, fitness_b),
                            1.0 + fitness_skill * 0.08,
                        )
                    )

        # Travel interest — 15% when sim_a has "travel" interest
        if (
            "travel" in sim_a.profile.get("interests", [])
            and random.random() < 0.15
            and hasattr(datasets, "travel_content")
            and datasets.travel_content
        ):
            from datasets.travel import sample_travel_seed, format_travel_interaction

            seed = sample_travel_seed()
            if seed:
                both = "travel" in sim_b.profile.get("interests", [])
                candidates.append(
                    (format_travel_interaction(seed, both), 1.6 if both else 1.0)
                )

        # Reminisce — unlocked at friendship >= 65 AND memories >= 5
        if friendship_score >= 65 and datasets is not None:
            rel_obj = None
            try:
                from engine.scheduler import pick_interaction_pair  # avoid circular
            except Exception:
                pass
            # We don't have the rel here directly, but we can check via a helper
            if hasattr(datasets, "nostalgia_templates"):
                from datasets.nostalgia import (
                    REMINISCE_FRIENDSHIP_MIN,
                    REMINISCE_MEMORY_MIN,
                    sample_reminisce_template,
                    format_reminisce_interaction,
                )

                if (
                    friendship_score >= REMINISCE_FRIENDSHIP_MIN
                    and random.random() < 0.10
                ):
                    template = sample_reminisce_template()
                    # shared memories will be filled by engine; pass empty for now
                    candidates.append(
                        (
                            format_reminisce_interaction(template, []),
                            2.2,  # highest weight — strongest bonding action
                        )
                    )

        # Reconciliation — post-toxic cycle (8% chance when applicable)
        if (
            hasattr(datasets, "counsel_chat")
            and datasets.counsel_chat
            and random.random() < 0.08
        ):
            from datasets.reconciliation import (
                sample_counsel_exchange,
                format_reconciliation_interaction,
                RECONCILIATION_FRIENDSHIP_MIN,
                RECONCILIATION_FRIENDSHIP_MAX,
            )

            if (
                RECONCILIATION_FRIENDSHIP_MIN
                <= friendship_score
                <= RECONCILIATION_FRIENDSHIP_MAX
            ):
                exchange = sample_counsel_exchange()
                candidates.append(
                    (
                        format_reconciliation_interaction(
                            exchange, sim_a.name, sim_b.name, friendship_score
                        ),
                        1.8,
                    )
                )

        # Gap 6: Financial stress seeds (10% when simoleons < threshold)
        from config import LOW_FUNDS_THRESHOLD

        if (
            sim_a.simoleons < LOW_FUNDS_THRESHOLD
            and random.random() < 0.10
            and hasattr(datasets, "finance_questions")
            and datasets.finance_questions
        ):
            from datasets.finance import (
                sample_financial_stress_seed,
                format_financial_seed,
            )

            seed = sample_financial_stress_seed(sim_a.simoleons)
            if seed:
                candidates.append((format_financial_seed(seed), 1.2))

    # Loneliness seed — FIG-Loneliness (when sim_a is socially isolated)
    if datasets is not None and hasattr(datasets, "loneliness_index") and datasets.loneliness_index:
        try:
            from core.arcs import is_lonely
            from datasets.loneliness import sample_loneliness_seed, format_loneliness_interaction
            if is_lonely(sim_a):
                drought = getattr(sim_a, "_social_drought_ticks", 0)
                seed = sample_loneliness_seed(drought, sim_a.emotion.dominant)
                if seed:
                    candidates.append((format_loneliness_interaction(seed, drought), 1.5))
        except Exception:
            pass

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

    # ── Sentiment gating — block or unlock interactions ───────────────────────
    from core.sentiments import (
        is_interaction_blocked,
        sentiment_unlocked_interactions,
    )
    # _blocked_interactions: set by EventEngine consequences (temporary event blocks)
    _event_blocked = set(getattr(sim_a, "_blocked_interactions", []))

    candidates = [
        (action, weight)
        for action, weight in candidates
        if not sim_a.is_on_cooldown(action, current_tick)
        and not is_interaction_blocked(relationship, action)
        and action not in _event_blocked
    ]
    # Sentiment-unlocked interactions (with boosted weight)
    for unlocked in sentiment_unlocked_interactions(relationship):
        candidates.append((unlocked, 1.8))

    # ── Milestone-unlocked interactions ───────────────────────────────────────
    for unlocked in getattr(sim_a, "_unlocked_interactions", []):
        candidates.append((unlocked, 1.5))

    # ── Club rule weight modifiers ────────────────────────────────────────────
    try:
        import engine.engine as _eng_mod
        if hasattr(_eng_mod, "_current_engine") and _eng_mod._current_engine:
            mods = _eng_mod._current_engine.clubs.interaction_weight_mods(
                sim_a.sim_id, sim_b.sim_id
            )
            if mods:
                candidates = [
                    (a, w * mods[a] if a in mods else w)
                    for a, w in candidates
                ]
    except Exception:
        pass

    # ── Celebrity fan interaction ─────────────────────────────────────────────
    from config import CELEBRITY_INTERACTION_THRESHOLD
    if getattr(sim_b, "celebrity_score", 0) >= CELEBRITY_INTERACTION_THRESHOLD:
        candidates.append(("ask for autograph", 1.2))
        candidates.append(("fan encounter", 1.0))

    # ── Holiday special interactions ──────────────────────────────────────────
    for holiday_action in getattr(sim_a, "_holiday_interactions", []):
        candidates.append((holiday_action, 1.6))

    if not candidates:
        sim_a._action_cooldowns.clear()
        return "say hello"

    # System 1: NLI-based category boosting — emergent routing from Sim state
    candidates = _nli_boost_candidates(sim_a, sim_b, relationship, candidates)

    actions, weights = zip(*candidates)
    return random.choices(actions, weights=weights, k=1)[0]


def _reputation_adjustment(sim_b) -> float:
    """
    Reputation-driven score modifier for sim_b as an interaction target.
    Negative reputation makes sims avoid this person; positive reputation
    creates mild attraction. Avoidance is intentionally stronger than attraction.
    """
    from config import REPUTATION_SCORE_SCALE, REPUTATION_BOOST_CAP
    rep = getattr(sim_b, "reputation_score", 0.0)
    raw = rep / REPUTATION_SCORE_SCALE          # -0.50 to +0.50
    return min(raw, REPUTATION_BOOST_CAP)       # cap upward at +0.25


def _memory_bias(rel) -> float:
    """
    Score adjustment based on the shared memory valence history between two sims.
    Positive shared history → seek each other out.
    Negative shared history → drift apart.
    """
    from config import MEMORY_BIAS_LOOKBACK, MEMORY_BIAS_WEIGHT
    memories = getattr(rel, "memories", [])
    if not memories:
        return 0.0
    recent = memories[-MEMORY_BIAS_LOOKBACK:]
    avg_valence = sum(m.get("valence", 0.0) for m in recent) / len(recent)
    return avg_valence * MEMORY_BIAS_WEIGHT     # -0.25 to +0.25


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

            rel = relationships.get(sim_a.sim_id, sim_b.sim_id)

            # Attraction bonus — romantic sims gravitate toward compatible partners
            from core.compatibility import attraction_score as _attr
            attr = _attr(sim_a, sim_b)
            attraction_bonus = attr * 0.15  # max ±0.15

            # Club co-membership bonus
            club_bonus = 0.0
            clubs = getattr(sim_a, "_clubs", None)
            try:
                from world.clubs import ClubManager as _CM
                # Access via engine if available; otherwise skip gracefully
                import engine.engine as _eng_mod
                if hasattr(_eng_mod, "_current_engine") and _eng_mod._current_engine:
                    club_bonus = _eng_mod._current_engine.clubs.pair_score_bonus(
                        sim_a.sim_id, sim_b.sim_id
                    )
            except Exception:
                pass

            score = (
                sim_a.want_pressure_toward(sim_b.sim_id) * 0.5
                + sim_b.want_pressure_toward(sim_a.sim_id) * 0.3
                + a_pressures.get("social", 0) * 0.2
                + random.uniform(0, 0.15)
                # Emergent mechanics — reputation and memory shape who talks to whom
                + _reputation_adjustment(sim_b)   # avoided if bad rep; sought if good
                + _memory_bias(rel)               # seek those with positive history
                + attraction_bonus                # chemistry draws compatible sims
                + club_bonus                      # club members prefer each other
            )
            candidates.append((score, sim_a, sim_b))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][2]
