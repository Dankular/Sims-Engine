from dataclasses import dataclass, field
import concurrent.futures
import os

from datasets.aita import load_aita_index
from datasets.blended_skill import load_blended_skill
from datasets.ccpe import load_ccpe
from datasets.character_voices import load_character_voices
from datasets.cooking import load_cooking_dialogs
from datasets.soda import load_soda
from datasets.fitness import load_fitness_content
from datasets.group_scenes import load_group_scenes, load_group_triggers
from datasets.health import load_health_symptoms
from datasets.nostalgia import load_nostalgia_templates
from datasets.reconciliation import load_counsel_chat
from datasets.travel import load_travel_content
from datasets.creative_works import load_creative_works
from datasets.culture import load_culture_index
from datasets.debate import load_debate_index
from datasets.finance import load_finance_questions
from datasets.manipulation import load_manipulation_index
from datasets.atomic import load_atomic_index
from datasets.confessions import load_confessions
from datasets.convai2 import load_convai2_seeds
from datasets.daily_dialog import load_daily_dialog
from datasets.dialogue import load_dialogue_actions
from datasets.emotion_calib import load_emotion_calibration
from datasets.emotional_intelligence import load_ei_scenarios
from datasets.empathetic import load_empath_index
from datasets.ethics import load_ethics_norms
from datasets.event2mind import load_event2mind
from datasets.hippocorpus import load_hippocorpus
from datasets.jokes import load_jokes, load_dadjokes
from datasets.mental_chat import load_mental_chat
from datasets.moral_choice import load_moral_choice
from datasets.moral_stories import load_moral_stories
from datasets.okcupid import load_okcupid_essays
from datasets.persona_chat import load_persona_chat
from datasets.persuasion import load_persuasion
from datasets.social_bias import load_social_bias_norms
from datasets.social_iqa import load_social_iqa_index
from datasets.social_norms import load_social_norms
from datasets.social_orientation import load_orientation_examples
from datasets.romance import load_flirtflip, load_rizz_corpus
from datasets.intimacy import load_intima
from datasets.boru import load_boru_arcs
from datasets.self_disclosure import load_self_disclosure
from datasets.adult import (
    load_adult_norms,
    load_literotica_snippets,
    load_sensual_speech_patterns,
)
from datasets.interests import load_all_interests
from datasets.loneliness import load_loneliness_index
from datasets.trauma import load_trauma_index
from datasets.social_conformity import load_conformity_examples


@dataclass
class DatasetRegistry:
    # ── Original ──────────────────────────────────────────────────────────────
    okcupid_essays: list[str]
    social_norms: list[str]
    atomic_index: dict
    social_iqa_index: dict
    empath_index: dict
    dialogue_actions: list[str]
    convai2_seeds: list[str]
    emotion_calib: dict
    # ── Class: Moral Dilemmas ─────────────────────────────────────────────────
    moral_stories: list[dict] = field(default_factory=list)
    moral_choice: list[dict] = field(default_factory=list)
    # ── Emotional Cascades ────────────────────────────────────────────────────
    event2mind_index: dict = field(default_factory=dict)
    # ── Venue-Aware Dialogue Seeds ────────────────────────────────────────────
    daily_dialog_index: dict = field(default_factory=dict)
    # ── Mental Health / Deep Support ──────────────────────────────────────────
    mental_chat_index: dict = field(default_factory=dict)
    # ── Persona Consistency ───────────────────────────────────────────────────
    persona_chat: list[dict] = field(default_factory=list)
    # ── Social Friction ───────────────────────────────────────────────────────
    social_bias_norms: list[dict] = field(default_factory=list)
    # ── Ethics Calibration ────────────────────────────────────────────────────
    ethics_norms: dict = field(default_factory=dict)
    # ── Class 1: Reputation & Community Judgment ──────────────────────────────
    aita_index: dict = field(default_factory=dict)
    # ── Class 2: Social Orientation (Circumplex) ──────────────────────────────
    orientation_examples: dict = field(default_factory=dict)
    # ── Class 3: Comedy / Jokes ───────────────────────────────────────────────
    jokes_by_tier: dict = field(default_factory=dict)
    dadjokes: list[dict] = field(default_factory=list)
    # ── Class 4: Memory Texture ───────────────────────────────────────────────
    hippocorpus: dict = field(default_factory=dict)
    # ── Class 5: Persuasion ───────────────────────────────────────────────────
    persuasion_args: list[dict] = field(default_factory=list)
    # ── Class 6: Confessions ─────────────────────────────────────────────────
    confessions_index: dict = field(default_factory=dict)
    # ── Class 7: Emotional Intelligence ──────────────────────────────────────
    ei_scenarios: list[dict] = field(default_factory=list)
    # ── Coded Gaps ────────────────────────────────────────────────────────────
    debate_index: dict = field(default_factory=dict)  # Gap 1: Logic
    cooking_dialogs: list[dict] = field(default_factory=list)  # Gap 2: Cooking
    creative_works: dict = field(default_factory=dict)  # Gap 3: Creativity
    manipulation_index: dict = field(default_factory=dict)  # Gap 4: Toxic
    culture_index: dict = field(default_factory=dict)  # Gap 5: Culture
    finance_questions: list[str] = field(default_factory=list)  # Gap 6: Finance
    # New gaps
    fitness_content: dict = field(default_factory=dict)
    group_scenes: list[dict] = field(default_factory=list)
    group_triggers: list[str] = field(default_factory=list)
    health_symptoms: list[dict] = field(default_factory=list)
    travel_content: list[str] = field(default_factory=list)
    nostalgia_templates: list[str] = field(default_factory=list)
    counsel_chat: list[dict] = field(default_factory=list)
    # Dialogue variety / chat voice
    soda_index: dict = field(default_factory=dict)
    blended_skill: dict = field(default_factory=dict)
    character_voices: dict = field(default_factory=dict)
    ccpe_turns: list[dict] = field(default_factory=list)
    flirtflip_index: dict = field(default_factory=dict)
    rizz_corpus: list[str] = field(default_factory=list)
    intima_codes: dict = field(default_factory=dict)
    boru_arcs: list[dict] = field(default_factory=list)
    self_disclosure_depth: dict = field(default_factory=dict)
    sensual_patterns: list[str] = field(default_factory=list)
    prosocial_nsfw_norms: list[str] = field(default_factory=list)
    literotica_snippets: list[str] = field(default_factory=list)
    # ── Interest-specific content (15 interests) ──────────────────────────────
    interests_data:       dict        = field(default_factory=dict)
    # ── Arc system datasets ────────────────────────────────────────────────────
    loneliness_index:     dict        = field(default_factory=dict)
    trauma_index:         dict        = field(default_factory=dict)
    conformity_examples:  list[dict]  = field(default_factory=list)

    @classmethod
    def load(cls, workers: int = 4) -> "DatasetRegistry":
        loaders = {
            # Original
            "okcupid_essays": load_okcupid_essays,
            "social_norms": load_social_norms,
            "atomic_index": load_atomic_index,
            "social_iqa_index": load_social_iqa_index,
            "empath_index": load_empath_index,
            "dialogue_actions": load_dialogue_actions,
            "convai2_seeds": load_convai2_seeds,
            "emotion_calib": load_emotion_calibration,
            # Wave 1
            "moral_stories": load_moral_stories,
            "moral_choice": load_moral_choice,
            "event2mind_index": load_event2mind,
            "daily_dialog_index": load_daily_dialog,
            "mental_chat_index": load_mental_chat,
            "persona_chat": load_persona_chat,
            "social_bias_norms": load_social_bias_norms,
            "ethics_norms": load_ethics_norms,
            # Wave 2 — 7 classes
            "aita_index": load_aita_index,
            "orientation_examples": load_orientation_examples,
            "jokes_by_tier": load_jokes,
            "dadjokes": load_dadjokes,
            "hippocorpus": load_hippocorpus,
            "persuasion_args": load_persuasion,
            "confessions_index": load_confessions,
            "ei_scenarios": load_ei_scenarios,
            # Coded gaps
            "debate_index": load_debate_index,
            "cooking_dialogs": load_cooking_dialogs,
            "creative_works": load_creative_works,
            "manipulation_index": load_manipulation_index,
            "culture_index": load_culture_index,
            "finance_questions": load_finance_questions,
            "fitness_content": load_fitness_content,
            "group_scenes": load_group_scenes,
            "group_triggers": load_group_triggers,
            "health_symptoms": load_health_symptoms,
            "travel_content": load_travel_content,
            "nostalgia_templates": load_nostalgia_templates,
            "counsel_chat": load_counsel_chat,
            "soda_index": load_soda,
            "blended_skill": load_blended_skill,
            "character_voices": load_character_voices,
            "ccpe_turns": load_ccpe,
            "flirtflip_index": load_flirtflip,
            "rizz_corpus": load_rizz_corpus,
            "intima_codes": load_intima,
            "boru_arcs": load_boru_arcs,
            "self_disclosure_depth": load_self_disclosure,
            # Adult datasets — always loaded; age-gated at interaction selection
            "sensual_patterns":    load_sensual_speech_patterns,
            "prosocial_nsfw_norms": load_adult_norms,
            "literotica_snippets": load_literotica_snippets,
            # Interest-specific content for all 15 ungrounded interests
            "interests_data":      load_all_interests,
            # Arc system datasets
            "loneliness_index":    load_loneliness_index,
            "trauma_index":        load_trauma_index,
            "conformity_examples": load_conformity_examples,
        }
        _list_keys_arc = {"conformity_examples"}
        _list_keys = {
            "okcupid_essays",
            "social_norms",
            "dialogue_actions",
            "convai2_seeds",
            "moral_stories",
            "moral_choice",
            "persona_chat",
            "social_bias_norms",
            "dadjokes",
            "persuasion_args",
            "ei_scenarios",
            "cooking_dialogs",
            "finance_questions",
            "group_scenes",
            "group_triggers",
            "health_symptoms",
            "travel_content",
            "nostalgia_templates",
            "counsel_chat",
            "ccpe_turns",
            "rizz_corpus",
            "boru_arcs",
            "sensual_patterns",
            "prosocial_nsfw_norms",
            "literotica_snippets",
        }
        results: dict[str, object] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {key: pool.submit(fn) for key, fn in loaders.items()}
            for key, future in futures.items():
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = [] if (key in _list_keys or key in _list_keys_arc) else {}

        return cls(
            okcupid_essays=results["okcupid_essays"],
            social_norms=results["social_norms"],
            atomic_index=results["atomic_index"],
            social_iqa_index=results["social_iqa_index"],
            empath_index=results["empath_index"],
            dialogue_actions=results["dialogue_actions"],
            convai2_seeds=results["convai2_seeds"],
            emotion_calib=results["emotion_calib"],
            moral_stories=results["moral_stories"],
            moral_choice=results["moral_choice"],
            event2mind_index=results["event2mind_index"],
            daily_dialog_index=results["daily_dialog_index"],
            mental_chat_index=results["mental_chat_index"],
            persona_chat=results["persona_chat"],
            social_bias_norms=results["social_bias_norms"],
            ethics_norms=results["ethics_norms"],
            aita_index=results["aita_index"],
            orientation_examples=results["orientation_examples"],
            jokes_by_tier=results["jokes_by_tier"],
            dadjokes=results["dadjokes"],
            hippocorpus=results["hippocorpus"],
            persuasion_args=results["persuasion_args"],
            confessions_index=results["confessions_index"],
            ei_scenarios=results["ei_scenarios"],
            debate_index=results["debate_index"],
            cooking_dialogs=results["cooking_dialogs"],
            creative_works=results["creative_works"],
            manipulation_index=results["manipulation_index"],
            culture_index=results["culture_index"],
            finance_questions=results["finance_questions"],
            fitness_content=results["fitness_content"],
            group_scenes=results["group_scenes"],
            group_triggers=results["group_triggers"],
            health_symptoms=results["health_symptoms"],
            travel_content=results["travel_content"],
            nostalgia_templates=results["nostalgia_templates"],
            counsel_chat=results["counsel_chat"],
            soda_index=results["soda_index"],
            blended_skill=results["blended_skill"],
            character_voices=results["character_voices"],
            ccpe_turns=results["ccpe_turns"],
            flirtflip_index=results.get("flirtflip_index", {}),
            rizz_corpus=results.get("rizz_corpus", []),
            intima_codes=results.get("intima_codes", {}),
            boru_arcs=results.get("boru_arcs", []),
            self_disclosure_depth=results.get("self_disclosure_depth", {}),
            sensual_patterns=results.get("sensual_patterns", []),
            prosocial_nsfw_norms=results.get("prosocial_nsfw_norms", []),
            literotica_snippets=results.get("literotica_snippets", []),
            interests_data=results.get("interests_data", {}),
            loneliness_index=results.get("loneliness_index", {}),
            trauma_index=results.get("trauma_index", {}),
            conformity_examples=results.get("conformity_examples", []),
        )


def load_all_datasets(workers: int = 4) -> DatasetRegistry:
    return DatasetRegistry.load(workers=workers)
