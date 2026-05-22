import random
from typing import TYPE_CHECKING, Optional

from config import INTERACTION_TYPES, REL_ACQUAINTANCE, REL_FRIEND
from core.traits import (
    interaction_weight_modifier,
    trait_blocks_interaction,
    trait_unlocks,
)
from core.social_chemistry import calculate_chemistry
from core.species import can_perform_interaction
from core.action_intelligence import (
    apply_interruption,
    build_action_chain,
    compute_social_risk,
    explain_choice,
    score_action_feasibility,
)
from world.context_sensors import sense_context
from core.action_prereqs import prerequisites_met

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
    "romantic or flirtatious interaction": "romantic",
    "playful funny casual interaction": "funny",
    "hostile argumentative confrontation": "mean",
    "casual friendly conversation": "friendly",
    # New categories
    "emotional support or mental health counseling": "support",
    "intellectual debate or philosophical discussion": "intellectual",
    "nostalgic memory sharing or reminiscing": "nostalgic",
    "conflict resolution reconciliation or forgiveness": "repair",
    "manipulative controlling or toxic social behavior": "toxic",
}
_NLI_INTERACTION_LABELS = list(_NLI_LABEL_TO_CAT.keys())


def _apply_stage_weights(
    sim_a: "Sim",
    sim_b: "Sim",
    relationship: "RelationshipRecord",
    candidates: list[tuple[str, float]],
    datasets: "DatasetRegistry | None",
) -> list[tuple[str, float]]:
    """
    Modulate candidate weights and inject dataset seeds according to the current
    conversation escalation stage (small_talk → teasing → disclosure → affectionate_intent).

    Organic feel comes from three signals layered together:
      1. relationship score (friendship / romance thresholds)
      2. moodlets (flirty/alluring shift the romantic track)
      3. recent buffer valence momentum (average stored on each turn)
      4. personality-adaptive arc multiplier from ConversationArcPolicy
    """
    stage = getattr(sim_a, "_conversation_stage", "small_talk")
    consent = getattr(sim_a, "_consent_state", {}).get(sim_b.sim_id, "")
    romance = relationship.romance
    friendship = relationship.friendship

    _both_adult = (
        sim_a.profile.get("age", 0) >= 16 and sim_b.profile.get("age", 0) >= 16
    )

    # Personality-adaptive stage multiplier — scales all boosts for this stage
    arc_mult = 1.0
    try:
        import engine.engine as _eng_mod

        eng = getattr(_eng_mod, "_current_engine", None)
        if eng is not None and hasattr(eng, "arc_policy"):
            arc_mult = eng.arc_policy.stage_multiplier(
                sim_a, sim_b, relationship, stage
            )
    except Exception:
        pass

    # ── Stage: teasing ────────────────────────────────────────────────────────
    if stage == "teasing":
        # Boost light-touch playful + early flirt + activity/discovery; suppress heavy deep/mean/toxic
        teasing_kw = (
            "tease",
            "joke",
            "banter",
            "playful",
            "flirt",
            "compliment",
            "one-liner",
            "impression",
            "pun",
            "roast",
            "quote",
        )
        tease_boost = max(0.1, 1.8 * arc_mult)
        candidates = [
            (a, w * tease_boost if any(k in a.lower() for k in teasing_kw) else w)
            for a, w in candidates
        ]
        # Boost activity/discovery — playful phase is good for joint doing
        for a, w in list(candidates):
            if a in INTERACTION_TYPES.get("activity", []):
                candidates = [(x, ww * 1.3 if x == a else ww) for x, ww in candidates]
            if a in INTERACTION_TYPES.get("discovery", []):
                candidates = [(x, ww * 1.2 if x == a else ww) for x, ww in candidates]
        # Suppress mean and toxic
        _suppress = set(INTERACTION_TYPES.get("mean", [])) | set(
            INTERACTION_TYPES.get("toxic", [])
        )
        for a, w in list(candidates):
            if a in _suppress:
                candidates = [(x, ww * 0.35 if x == a else ww) for x, ww in candidates]
        # Inject playful flirt line from dataset
        if datasets is not None and hasattr(datasets, "flirtflip_index"):
            try:
                from datasets.romance import sample_flirt_line

                line, tier = sample_flirt_line(min(romance, 30))  # cap at playful tier
                if line and tier in ("light", "playful"):
                    candidates.append(
                        (
                            f"[TEASE] {line[:200]}",
                            1.4,
                        )
                    )
            except Exception:
                pass

    # ── Stage: disclosure ─────────────────────────────────────────────────────
    elif stage == "disclosure":
        # Boost deep / support / nostalgic / intellectual; suppress comedy, mean, toxic
        deep_boost = max(0.05, 0.8 * arc_mult)
        for cat in ("deep", "support", "nostalgic", "intellectual"):
            for action in INTERACTION_TYPES.get(cat, []):
                candidates.append((action, deep_boost))
        _disclosure_suppress = (
            set(INTERACTION_TYPES.get("funny", []))
            | set(INTERACTION_TYPES.get("mean", []))
            | set(INTERACTION_TYPES.get("toxic", []))
        )
        candidates = [
            (a, w * 0.35) if a in _disclosure_suppress else (a, w)
            for a, w in candidates
        ]
        # Inject self-disclosure seed matched to current friendship depth
        if datasets is not None and hasattr(datasets, "self_disclosure_depth"):
            try:
                from datasets.self_disclosure import sample_by_depth

                disclosure, depth = sample_by_depth(friendship)
                if disclosure:
                    note = ""
                    if depth == "deep" and friendship < 40:
                        note = " (early deep disclosure — risk of over-sharing)"
                    candidates.append(
                        (
                            f"[SELF-DISCLOSURE — {depth.upper()}]\n"
                            f'Sim A shares: "{disclosure[:260]}"\n'
                            f"Adjudicate for trust fit at this friendship depth.{note}",
                            max(0.1, 1.6 * arc_mult),
                        )
                    )
            except Exception:
                pass

    # ── Stage: affectionate_intent ────────────────────────────────────────────
    elif stage == "affectionate_intent":
        if consent == "withdrawn":
            # Hard block on all romantic/intimate escalation
            _block = set(INTERACTION_TYPES.get("romantic", [])) | set(
                INTERACTION_TYPES.get("intimate", [])
            )
            candidates = [
                (a, w * 0.05) if a in _block else (a, w) for a, w in candidates
            ]
            # Pivot to repair/support — rejection is a signal to de-escalate warmly
            for action in INTERACTION_TYPES.get("repair", []):
                candidates.append((action, 0.9))
            for action in INTERACTION_TYPES.get("support", []):
                candidates.append((action, 0.7))
            return candidates  # skip dataset injections

        # Suppress toxic/repair/mean — wrong tone for romantic intimacy
        _aff_suppress = (
            set(INTERACTION_TYPES.get("toxic", []))
            | set(INTERACTION_TYPES.get("mean", []))
            | set(INTERACTION_TYPES.get("repair", []))
        )
        candidates = [
            (a, w * 0.05) if a in _aff_suppress else (a, w) for a, w in candidates
        ]

        # Boost full romantic tier (personality-scaled)
        romantic_boost = max(0.05, 0.9 * arc_mult)
        for action in INTERACTION_TYPES.get("romantic", []):
            candidates.append((action, romantic_boost))
        # Also boost nostalgic — shared memories deepen romantic moments
        for action in INTERACTION_TYPES.get("nostalgic", []):
            candidates.append((action, max(0.05, 0.5 * arc_mult)))

        # Boost intimate tier if both adult + high romance
        if _both_adult and romance >= 55 and "intimate" in INTERACTION_TYPES:
            for action in INTERACTION_TYPES["intimate"]:
                candidates.append(
                    (action, max(0.05, (0.6 + romance / 300.0) * arc_mult))
                )

        # Attachment dynamics seed (INTIMA)
        if (
            datasets is not None
            and hasattr(datasets, "intima_codes")
            and datasets.intima_codes
        ):
            try:
                from datasets.intimacy import sample_intima

                prompt = sample_intima(sim_a.profile.get("attachment", "general"))
                if prompt:
                    candidates.append(
                        (
                            f"[PARTNERS DYNAMICS]\n"
                            f"Attachment-aware interaction seed: {prompt[:320]}",
                            1.5,
                        )
                    )
            except Exception:
                pass

        # Suggestive register (adult tier 1)
        if (
            _both_adult
            and datasets is not None
            and hasattr(datasets, "sensual_patterns")
            and datasets.sensual_patterns
        ):
            line = random.choice(datasets.sensual_patterns)
            candidates.append(
                (
                    f"[INTIMATE — SUGGESTIVE]\n{line[:260]}",
                    1.3,
                )
            )

        # NSFW starter (adult tier 2) — only at high romance + consent given
        if (
            _both_adult
            and consent == "given"
            and romance >= 45
            and datasets is not None
            and hasattr(datasets, "reddit_nsfw_titles")
            and datasets.reddit_nsfw_titles
            and random.random() < 0.35
        ):
            try:
                from datasets.adult import sample_reddit_nsfw_title

                seed = sample_reddit_nsfw_title()
                if seed:
                    candidates.append(
                        (
                            f'[NSFW STARTER]\nSim A opens with: "{seed[:220]}"',
                            1.5 + romance / 220.0,
                        )
                    )
            except Exception:
                pass

    return candidates


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
    # Neural policy forced choice (phase 3 planner override)
    forced = getattr(sim_a, "_neural_forced_interaction", None)
    if forced and not sim_a.is_on_cooldown(str(forced), current_tick):
        sim_a._neural_forced_interaction = None
        return str(forced)

    # ── Intention stack bias (persistent multi-tick goals) ────────────────────
    intentions = getattr(sim_a, "intentions", None)
    if intentions is not None:
        try:
            bias_type, bias_target = intentions.active_bias()
            if bias_type:
                _bias_candidates = INTERACTION_TYPES.get(bias_type, [])
                if _bias_candidates:
                    if not bias_target or bias_target == sim_b.sim_id:
                        _chosen = random.choice(_bias_candidates)
                        if not sim_a.is_on_cooldown(_chosen, current_tick):
                            return _chosen
        except Exception:
            pass

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
    chem = calculate_chemistry(sim_a, sim_b)
    mood = sim_a.emotion.dominant_valence
    ocean = sim_a.ocean
    candidates: list[tuple[str, float]] = []
    env: dict[str, float] = {}
    try:
        from config import ENABLE_CONTEXT_SENSORS
        import engine.engine as _eng_mod

        if ENABLE_CONTEXT_SENSORS:
            env = sense_context(
                getattr(_eng_mod, "_current_engine", None), sim_a, sim_b
            )
    except Exception:
        env = {}

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
            if chem.chemistry >= 35:
                candidates.append(("hold hands", 1.0))
            if chem.chemistry <= -25:
                candidates.append(("chat", 0.2))
            _both_adult = (
                sim_a.profile.get("age", 0) >= 16 and sim_b.profile.get("age", 0) >= 16
            )
            if romance_score >= 65 and "intimate" in INTERACTION_TYPES and _both_adult:
                for action in INTERACTION_TYPES["intimate"]:
                    candidates.append((action, 0.42 + romance_score / 250.0))

    for special in sim_a.skills.unlocked_interactions():
        candidates.append((special, 1.2))

    # ── Support: emotional labour for a struggling partner ────────────────────
    _sim_b_struggling = (
        bool(sim_b.fears)
        or sim_b.profile["ocean"].get("neuroticism", 0) > 0.6
        or getattr(sim_b, "grief_stage", -1) >= 0
        or sim_b.needs.social < 30
    )
    if friendship_score >= REL_FRIEND and _sim_b_struggling:
        for action in INTERACTION_TYPES.get("support", []):
            candidates.append((action, 1.2 + ocean["agreeableness"] * 0.5))

    # ── Intellectual: debate, philosophy, thought experiments ─────────────────
    _intellectual_ready = (
        sim_a.skills.levels.get("logic", 0) >= 2
        or ocean.get("openness", 0.5) > 0.65
        or "genius" in sim_a.profile.get("traits", [])
        or "bookworm" in sim_a.profile.get("traits", [])
    )
    if _intellectual_ready and friendship_score >= REL_ACQUAINTANCE:
        for action in INTERACTION_TYPES.get("intellectual", []):
            candidates.append((action, 0.6 + ocean.get("openness", 0.5) * 0.5))

    # ── Activity: shared-interest physical or creative collaboration ──────────
    _shared_interest_count = len(
        set(sim_a.profile.get("interests", []))
        & set(sim_b.profile.get("interests", []))
    )
    _activity_skill = max(
        sim_a.skills.levels.get("cooking", 0),
        sim_a.skills.levels.get("fitness", 0),
        sim_a.skills.levels.get("creativity", 0),
    )
    if _shared_interest_count > 0 or _activity_skill >= 2:
        for action in INTERACTION_TYPES.get("activity", []):
            candidates.append((action, 0.7 + min(0.5, _shared_interest_count * 0.15)))

    # ── Nostalgic: shared memory recall ──────────────────────────────────────
    _shared_mem = int(getattr(relationship, "shared_memory_count", 0))
    if friendship_score >= 65 and _shared_mem >= 3:
        for action in INTERACTION_TYPES.get("nostalgic", []):
            candidates.append((action, 1.6 + min(0.4, _shared_mem * 0.05)))

    # ── Repair: post-conflict reconciliation ──────────────────────────────────
    _conflict_sentiments = {
        "betrayal",
        "heartbreak",
        "resentment",
        "betrayed_trust",
        "jealous_rage",
    }
    _has_conflict = any(
        s.name in _conflict_sentiments for s in getattr(relationship, "sentiments", [])
    )
    if _has_conflict or relationship.in_toxic_cycle:
        for action in INTERACTION_TYPES.get("repair", []):
            candidates.append((action, 1.7 if _has_conflict else 1.1))

    # ── Toxic: manipulation and dark social tactics ────────────────────────────
    _toxic_traits = {"jealous", "evil", "hot-headed", "narcissistic", "manipulative"}
    _is_manipulative = (
        ocean.get("neuroticism", 0.5) > 0.7
        and (
            bool(_toxic_traits & set(sim_a.profile.get("traits", [])))
            or relationship.in_toxic_cycle
        )
        and mood < 0.45
    )
    if _is_manipulative:
        for action in INTERACTION_TYPES.get("toxic", []):
            candidates.append((action, 0.4 + (1.0 - mood) * 0.9))

    # ── Practical: finance, health, and problem-solving ───────────────────────
    from config import LOW_FUNDS_THRESHOLD as _LFT

    _practical_need = (
        sim_a.simoleons < _LFT * 1.5
        or getattr(sim_a, "health_status", "healthy") != "healthy"
        or getattr(sim_b, "health_status", "healthy") != "healthy"
    )
    if _practical_need and friendship_score >= REL_ACQUAINTANCE:
        for action in INTERACTION_TYPES.get("practical", []):
            candidates.append(
                (action, 0.9 + (0.3 if friendship_score >= REL_FRIEND else 0.0))
            )

    # ── Discovery: early-stage preference and personality exploration ──────────
    _early_relationship = (
        friendship_score < 35 or int(getattr(relationship, "interactions", 0)) < 8
    )
    if _early_relationship:
        for action in INTERACTION_TYPES.get("discovery", []):
            candidates.append((action, 0.85 + ocean.get("extraversion", 0.5) * 0.3))

    # ── Conversation continuity / chain preference ───────────────────────────
    last_chain = list(getattr(sim_a, "_action_chain", []) or [])
    if last_chain:
        from config import ACTION_CHAIN_BOOST, ENABLE_ACTION_CHAINS

        if ENABLE_ACTION_CHAINS:
            candidates.append((last_chain[0], ACTION_CHAIN_BOOST))

    # ── Open-world action enrichment (feature-flagged) ───────────────────────
    try:
        from config import (
            ENABLE_OPEN_WORLD_ACTIONS,
            OPEN_WORLD_ACTIONS_CHANCE,
            OPEN_WORLD_ACTIONS_MAX_CANDIDATES,
        )

        if ENABLE_OPEN_WORLD_ACTIONS and random.random() < OPEN_WORLD_ACTIONS_CHANCE:
            from datasets.open_world_actions import sample_action_candidates

            candidates.extend(
                sample_action_candidates(
                    sim_a,
                    sim_b,
                    relationship,
                    max_candidates=max(1, int(OPEN_WORLD_ACTIONS_MAX_CANDIDATES)),
                )
            )
    except Exception:
        pass

    # ── Grief / isolation support bias ───────────────────────────────────────
    _grief_active = getattr(sim_a, "grief_stage", -1) >= 0
    _post_grief_isolated = (
        getattr(sim_a, "grief_stage", -1) == -1
        and getattr(sim_a, "grief_target", "")
        and sim_a.needs.social < 30
    )
    if _grief_active or _post_grief_isolated:
        # Heavily push toward deep/supportive interactions
        for action in INTERACTION_TYPES.get("deep", []):
            candidates.append((action, 1.5))
        candidates.append(("share feelings about recent loss", 2.0))
        candidates.append(("ask for emotional support", 1.8))
        candidates.append(("confide in someone trusted", 1.6))
        # Suppress mean/funny — inappropriate while grieving.
        # Match the full "mean" + "funny" category sets and common comedy keywords.
        _grief_suppress_set = set(INTERACTION_TYPES.get("mean", [])) | set(
            INTERACTION_TYPES.get("funny", [])
        )
        _grief_suppress_kw = (
            "insult",
            "mock",
            "roast",
            "prank",
            "joke",
            "impression",
            "meme",
            "tease",
            "argue",
            "rumour",
            "bully",
            "taunt",
            "comedy",
        )
        candidates = [
            (a, w * 0.1)
            if a in _grief_suppress_set or any(k in a for k in _grief_suppress_kw)
            else (a, w)
            for a, w in candidates
        ]
    # Also push sim_b to offer support if sim_a is visibly suffering
    _other_grief = getattr(sim_b, "grief_stage", -1) >= 0 or (
        getattr(sim_b, "grief_stage", -1) == -1
        and getattr(sim_b, "grief_target", "")
        and sim_b.needs.social < 30
    )
    if _other_grief:
        candidates.append(("offer condolences", 2.0))
        candidates.append(("check in on how they're doing", 1.8))
        candidates.append(("share a fond memory of what was lost", 1.5))

    # ── Grim Reaper lingering — push toward grim-specific interactions ────────
    try:
        import engine.engine as _eng_mod

        _gr = getattr(getattr(_eng_mod, "_current_engine", None), "grim_reaper", None)
        if _gr and _gr.is_present and _gr._linger:
            _grim_lot = _gr.lot_id
            _sim_lot = getattr(sim_a, "household_id", None)
            if _sim_lot and _sim_lot == _grim_lot:
                from world.grim_reaper import GRIM_SOCIAL_INTERACTIONS

                for grim_action in GRIM_SOCIAL_INTERACTIONS:
                    candidates.append((grim_action, 1.6))
                # Plead if recently grieving
                if getattr(sim_a, "grief_stage", -1) >= 0:
                    candidates.append(("plead with grim reaper", 2.5))
                # Chess if sim has decent logic
                if sim_a.skills.levels.get("logic", 0) >= 3:
                    candidates.append(("challenge grim to chess", 2.0))
    except Exception:
        pass

    # ── Item-aware interaction bonuses ───────────────────────────────────────
    _inv_types = {
        str(o.get("type", "")) for o in getattr(sim_a, "inventory_objects", [])
    }
    if "Book" in _inv_types or "Artifact" in _inv_types:
        candidates.append(("read together", 1.3))
        candidates.append(("discuss what you're reading", 1.1))
    if "Alcohol" in _inv_types:
        candidates.append(("share a drink", 1.4))
        candidates.append(("toast together", 1.2))
    if "Flower" in _inv_types:
        candidates.append(("give flowers", 1.5))
    if "Energy Drink" in _inv_types:
        candidates.append(("share energy drinks", 1.0))
    if "Collectible" in _inv_types:
        candidates.append(("show off collection", 1.0))
    if "Jewelry" in _inv_types or "Clothing" in _inv_types:
        candidates.append(("show off new outfit", 1.0))
    if _inv_types & {"Weapon", "Armor", "Explosive"}:
        candidates.append(("show weapon collection", 0.9))

    # ── Venue sensor weighting (noise/crowd/intimacy) ───────────────────────
    v = getattr(sim_a, "_current_venue", {}) or {}
    noise = float(v.get("noise", 0.0) or 0.0)
    crowd = float(v.get("crowd", 0.0) or 0.0)
    intimacy = float(v.get("intimacy", 0.0) or 0.0)
    if noise > 0.65 or crowd > 0.7:
        candidates = [
            (a, w * 0.75 if a in INTERACTION_TYPES.get("deep", []) else w)
            for a, w in candidates
        ]
        for action in INTERACTION_TYPES.get("friendly", [])[:2]:
            candidates.append((action, 0.22))
    if intimacy > 0.6:
        for action in INTERACTION_TYPES.get("deep", []):
            candidates.append((action, 0.22))
        for action in INTERACTION_TYPES.get("romantic", []):
            candidates.append((action, 0.18))

    # ── Skill-based interaction weighting ────────────────────────────────────
    skill_boost_map = {
        "comedy": ("funny", 0.4),
        "charisma": ("friendly", 0.3),
        "fitness": ("friendly", 0.2),
        "logic": ("deep", 0.3),
        "mischief": ("mean", 0.3),
    }
    for skill_name, (category, boost) in skill_boost_map.items():
        level = sim_a.skills.levels.get(skill_name, 0)
        if level >= 3 and category in INTERACTION_TYPES:
            mod = boost * (level / 10.0)
            for action in INTERACTION_TYPES[category]:
                candidates.append((action, mod))

    # ── Marriage proposal — unlocked at high romance + no current marriage ────
    if (
        romance_score >= 85
        and not getattr(sim_a, "_married_to", None)
        and not getattr(sim_b, "_married_to", None)
        and "first_love" in {s.name for s in getattr(relationship, "sentiments", [])}
    ):
        candidates.append(("propose marriage", 0.8))

    # ── Interest-match bonding ────────────────────────────────────────────────
    if (
        datasets is not None
        and hasattr(datasets, "interests_data")
        and datasets.interests_data
    ):
        interests_a = set(sim_a.profile.get("interests", []))
        interests_b = set(sim_b.profile.get("interests", []))
        shared = interests_a & interests_b
        solo = interests_a - interests_b  # only sim_a has it

        # Shared interest — highest weight (mutual passion)
        if shared and random.random() < 0.25:
            interest = random.choice(list(shared))
            from datasets.interests import (
                sample_interest_seed,
                format_interest_interaction,
            )

            seed = sample_interest_seed(interest, datasets.interests_data)
            if seed:
                bonus = 0.3 if len(shared) > 1 else 0.0
                candidates.append(
                    (
                        format_interest_interaction(
                            seed, sim_a.name, sim_b.name, shared=True
                        ),
                        1.5 + bonus,
                    )
                )

        # Solo interest — lower weight (one-sided enthusiasm)
        elif solo and random.random() < 0.12:
            interest = random.choice(list(solo))
            from datasets.interests import (
                sample_interest_seed,
                format_interest_interaction,
            )

            seed = sample_interest_seed(interest, datasets.interests_data)
            if seed:
                candidates.append(
                    (
                        format_interest_interaction(
                            seed, sim_a.name, sim_b.name, shared=False
                        ),
                        1.0,
                    )
                )

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
                "net_worth": float(
                    getattr(sim_a, "_portfolio_view", {}).get(
                        "net_worth", sim_a.simoleons
                    )
                ),
                "liability_value": float(
                    getattr(sim_a, "_portfolio_view", {}).get("liability_value", 0.0)
                ),
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

        # Self-disclosure depth curve — deferred to stage system in "disclosure" stage
        _conv_stage = getattr(sim_a, "_conversation_stage", "small_talk")
        if (
            random.random() < 0.06
            and friendship_score >= 20
            and hasattr(datasets, "self_disclosure_depth")
            and datasets.self_disclosure_depth
            and _conv_stage != "disclosure"
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
        # Deferred to stage system when in teasing/affectionate_intent stages
        if (
            friendship_score >= REL_ACQUAINTANCE
            and hasattr(datasets, "flirtflip_index")
            and _conv_stage not in ("teasing", "affectionate_intent")
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

        # Partners-state attachment dynamics (INTIMA) — deferred to stage system
        if (
            romance_score >= 80
            and hasattr(datasets, "intima_codes")
            and datasets.intima_codes
            and _conv_stage != "affectionate_intent"
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

        # Adult tier 1: suggestive register — deferred to stage system
        _both_of_age = (
            sim_a.profile.get("age", 0) >= 16 and sim_b.profile.get("age", 0) >= 16
        )
        if (
            romance_score >= 65
            and _both_of_age
            and hasattr(datasets, "sensual_patterns")
            and datasets.sensual_patterns
            and _conv_stage != "affectionate_intent"
        ):
            line = random.choice(datasets.sensual_patterns)
            candidates.append(
                (
                    f"[INTIMATE — SUGGESTIVE]\n{line[:260]}",
                    1.1,
                )
            )

        # Adult tier 2: NSFW starters — deferred to stage system when in affectionate_intent
        if (
            _both_of_age
            and hasattr(datasets, "reddit_nsfw_titles")
            and datasets.reddit_nsfw_titles
            and _conv_stage != "affectionate_intent"
        ):
            moodlet_hot = False
            try:
                moodlets = getattr(sim_a, "moodlets", None)
                if moodlets is not None:
                    moodlet_hot = any(
                        moodlets.has(k)
                        for k in (
                            "flirty",
                            "alluring",
                            "in_the_mood",
                            "love_is_in_the_air",
                        )
                    )
            except Exception:
                moodlet_hot = False

            likes_target = friendship_score >= REL_ACQUAINTANCE or romance_score >= 25
            flirt_state = (
                romance_score >= 35
                or sim_a.emotion.dominant == "desire"
                or moodlet_hot
                or "romantic" in sim_a.profile.get("traits", [])
            )

            if likes_target and flirt_state and random.random() < 0.20:
                from datasets.adult import sample_reddit_nsfw_title

                seed = sample_reddit_nsfw_title()
                if seed:
                    candidates.append(
                        (
                            f'[NSFW STARTER]\nSim A opens with a provocative line: "{seed[:220]}"',
                            1.35 + (romance_score / 220.0),
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
                    and int(getattr(relationship, "shared_memory_count", 0))
                    >= REMINISCE_MEMORY_MIN
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

        # Hippocorpus narrative memory — drives "nostalgic" category actions
        # recalled (positive) at friendship >= 55; retold (fragmented) at deep grief/trauma
        if (
            hasattr(datasets, "hippocorpus")
            and datasets.hippocorpus
            and friendship_score >= 55
            and random.random() < 0.09
        ):
            try:
                _grief_active_hc = getattr(sim_a, "grief_stage", -1) >= 0
                _mode = "retold" if _grief_active_hc else "recalled"
                _pool = datasets.hippocorpus.get(_mode, [])
                if not _pool:
                    _pool = datasets.hippocorpus.get("recalled", [])
                if _pool:
                    _entry = random.choice(_pool)
                    _snippet = str(_entry.get("story", _entry.get("text", "")))[:200]
                    if _snippet:
                        candidates.append(
                            (
                                f"[MEMORY — {_mode.upper()}]\n"
                                f'Sim A draws on a vivid memory: "{_snippet}"\n'
                                f"Adjudicate how Sim A shares or withholds this with Sim B.",
                                1.7 if friendship_score >= 65 else 1.1,
                            )
                        )
            except Exception:
                pass

        # CCPE discovery exchange — early preference elicitation (friendship < 35)
        # Models natural follow-up curiosity rather than monologuing
        if (
            hasattr(datasets, "ccpe_turns")
            and datasets.ccpe_turns
            and friendship_score < 35
            and random.random() < 0.12
        ):
            try:
                _turn = random.choice(datasets.ccpe_turns)
                _q = str(_turn.get("question", ""))[:120]
                _a = str(_turn.get("answer", ""))[:120]
                if _q and _a:
                    candidates.append(
                        (
                            f"[DISCOVERY]\n"
                            f'Sim A asks organically: "{_q}"\n'
                            f'Expected natural response style: "{_a[:80]}"\n'
                            f"Keep it curious, not interrogating.",
                            1.0,
                        )
                    )
            except Exception:
                pass

        # Health concern seed — when either sim is unwell or low energy
        if (
            hasattr(datasets, "health_symptoms")
            and datasets.health_symptoms
            and friendship_score >= REL_ACQUAINTANCE
        ):
            _either_unwell = (
                getattr(sim_a, "health_status", "healthy") != "healthy"
                or getattr(sim_b, "health_status", "healthy") != "healthy"
                or getattr(sim_a, "_low_energy_ticks", 0) >= 3
            )
            if _either_unwell and random.random() < 0.15:
                try:
                    from datasets.health import sample_symptom

                    _symptom = sample_symptom(sim_a.needs.energy)
                    if _symptom:
                        _who = (
                            sim_a.name
                            if getattr(sim_a, "health_status", "healthy") != "healthy"
                            else sim_b.name
                        )
                        candidates.append(
                            (
                                f"[HEALTH CONCERN]\n"
                                f'{_who} describes: "{_symptom["text"][:180]}"\n'
                                f"Possible cause: {_symptom.get('condition', 'unknown')}. "
                                f"Adjudicate how Sim A responds given their agreeableness.",
                                1.4,
                            )
                        )
                except Exception:
                    pass

    # Loneliness seed — FIG-Loneliness (when sim_a is socially isolated)
    if (
        datasets is not None
        and hasattr(datasets, "loneliness_index")
        and datasets.loneliness_index
    ):
        try:
            from core.arcs import is_lonely
            from datasets.loneliness import (
                sample_loneliness_seed,
                format_loneliness_interaction,
            )

            if is_lonely(sim_a):
                drought = getattr(sim_a, "_social_drought_ticks", 0)
                seed = sample_loneliness_seed(drought, sim_a.emotion.dominant)
                if seed:
                    candidates.append(
                        (format_loneliness_interaction(seed, drought), 1.5)
                    )
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
        and not trait_blocks_interaction(sim_a, action)
        and can_perform_interaction(sim_a, action)
        and action not in _event_blocked
        and prerequisites_met(sim_a, relationship, action)
    ]
    # Sentiment-unlocked interactions (with boosted weight)
    for unlocked in sentiment_unlocked_interactions(relationship):
        candidates.append((unlocked, 1.8))

    for unlocked in trait_unlocks(sim_a):
        candidates.append((unlocked, 1.35))

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
                    (a, w * mods[a] if a in mods else w) for a, w in candidates
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

    # ── Interruption routing for urgent states ───────────────────────────────
    from config import ENABLE_ACTION_INTERRUPTS

    if ENABLE_ACTION_INTERRUPTS:
        interruption = apply_interruption(
            {
                "fire_risk": float(
                    getattr(sim_a, "hazard_flags", {}).get("fire", 0.0) or 0.0
                ),
                "bladder_critical": float(getattr(sim_a.needs, "bladder", 50.0) or 50.0)
                < 8,
                "energy_critical": float(getattr(sim_a.needs, "energy", 50.0) or 50.0)
                < 8,
            }
        )
        if interruption and not sim_a.is_on_cooldown(interruption, current_tick):
            return interruption

    # System 2b: Stage-aware arc weights (tease → disclosure → affectionate_intent)
    candidates = _apply_stage_weights(sim_a, sim_b, relationship, candidates, datasets)

    # System 1: NLI-based category boosting — emergent routing from Sim state
    candidates = _nli_boost_candidates(sim_a, sim_b, relationship, candidates)
    weighted: list[tuple[str, float]] = []
    for action, weight in candidates:
        from config import ACTION_RISK_WEIGHT

        mod = interaction_weight_modifier(sim_a, action)
        feas = score_action_feasibility(sim_a, action, env)
        risk = compute_social_risk(sim_a, sim_b, relationship, action)
        desire_push = float(
            getattr(sim_a, "_desire_loop", {}).get("romance_push", 0.0) or 0.0
        )
        if desire_push > 0 and any(
            k in action for k in ("flirt", "love", "hands", "date")
        ):
            mod *= 1.0 + min(0.35, desire_push)
        weighted.append(
            (
                action,
                max(0.01, weight * mod * feas * (1.0 - (risk * ACTION_RISK_WEIGHT))),
            )
        )
    candidates = weighted
    # Neural policy weighting (phase 1 contextual interaction model)
    try:
        import engine.engine as _eng_mod

        eng = getattr(_eng_mod, "_current_engine", None)
        if eng is not None and hasattr(eng, "neural_policy"):
            goal_text = ""
            if getattr(sim_a, "active_wants", None):
                goal_text = max(
                    sim_a.active_wants, key=lambda w: float(w.priority)
                ).description
            feats = eng.neural_policy.extract_features(sim_a, goal_text, "social")
            candidates = [
                (
                    action,
                    eng.neural_policy.score_interaction(
                        sim_a, action, float(weight), feats
                    ),
                )
                for action, weight in candidates
            ]
    except Exception:
        pass
    # Adaptive bandit weighting (phase 1 online learning)
    try:
        import engine.engine as _eng_mod

        eng = getattr(_eng_mod, "_current_engine", None)
        if eng is not None and hasattr(eng, "adaptive_policy"):
            candidates = [
                (
                    action,
                    max(
                        0.01,
                        eng.adaptive_policy.score(sim_a, sim_b, action, float(weight)),
                    ),
                )
                for action, weight in candidates
            ]
    except Exception:
        pass

    actions, weights = zip(*candidates)
    pick = random.choices(actions, weights=weights, k=1)[0]
    sim_a._action_chain = build_action_chain(sim_a, pick)[1:]
    try:
        from config import ENABLE_ACTION_EXPLANATIONS

        top = sorted(candidates, key=lambda x: x[1], reverse=True)[:5]
        top_action, top_weight = top[0]
        top_feas = score_action_feasibility(sim_a, top_action, env)
        top_risk = compute_social_risk(sim_a, sim_b, relationship, top_action)
        sim_a._last_autonomy_choice = {
            "selected": pick,
            "top_candidates": top,
        }
        if ENABLE_ACTION_EXPLANATIONS:
            sim_a._last_autonomy_choice["explanation"] = explain_choice(
                top_action, float(top_weight), top_feas, top_risk, env
            )
    except Exception:
        pass
    return pick


def _reputation_adjustment(sim_b) -> float:
    """
    Reputation-driven score modifier for sim_b as an interaction target.
    Negative reputation makes sims avoid this person; positive reputation
    creates mild attraction. Avoidance is intentionally stronger than attraction.
    """
    from config import REPUTATION_SCORE_SCALE, REPUTATION_BOOST_CAP

    rep = getattr(sim_b, "reputation_score", 0.0)
    raw = rep / REPUTATION_SCORE_SCALE  # -0.50 to +0.50
    return min(raw, REPUTATION_BOOST_CAP)  # cap upward at +0.25


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
    return avg_valence * MEMORY_BIAS_WEIGHT  # -0.25 to +0.25


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
                + _reputation_adjustment(sim_b)  # avoided if bad rep; sought if good
                + _memory_bias(rel)  # seek those with positive history
                + attraction_bonus  # chemistry draws compatible sims
                + club_bonus  # club members prefer each other
                + getattr(sim_a, "autonomy_profile", {}).get("social", 0.0) * 0.08
                - getattr(sim_a, "autonomy_profile", {}).get("solitude", 0.0) * 0.08
                + (calculate_chemistry(sim_a, sim_b).chemistry / 100.0) * 0.2
            )
            candidates.append((score, sim_a, sim_b))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][2]
