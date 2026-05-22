from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

from llm.adjudicator import ADJUDICATOR_SYSTEM_BASE
from datasets.atomic import query_atomic
from datasets.emotion_calib import build_emotion_calibration_block
from datasets.social_iqa import sample_social_iqa

if TYPE_CHECKING:
    from datasets.loader import DatasetRegistry
    from core.memory import MemoryStore

_DIALOGUE_BUFFER_MAX_TURNS = 4  # inject at most this many prior turns
_DIALOGUE_STALE_TICKS = 12  # turns older than this are dropped

# GoEmotions (27 labels) → empathetic dataset labels (32 labels)
_GOEMO_TO_EMPATH: dict[str, str] = {
    "admiration": "impressed",
    "amusement": "joyful",
    "anger": "angry",
    "annoyance": "annoyed",
    "approval": "content",
    "caring": "caring",
    "confusion": "apprehensive",
    "curiosity": "anticipating",
    "desire": "anticipating",
    "disappointment": "disappointed",
    "disapproval": "disgusted",
    "disgust": "disgusted",
    "embarrassment": "embarrassed",
    "excitement": "excited",
    "fear": "afraid",
    "gratitude": "grateful",
    "grief": "devastated",
    "joy": "joyful",
    "love": "caring",
    "nervousness": "anxious",
    "optimism": "hopeful",
    "pride": "proud",
    "realization": "surprised",
    "relief": "content",
    "remorse": "guilty",
    "sadness": "sad",
    "surprise": "surprised",
    "neutral": "content",
}


def get_interaction_context(
    interaction: str,
    sim_a: Any,
    sim_b: Any,
    datasets: "DatasetRegistry | None" = None,
    memory_store: "MemoryStore | None" = None,
    current_tick: int = 0,
) -> str:
    parts: list[str] = []

    # ── System 1: Semantic episodic memory ───────────────────────────────────
    if memory_store is not None:
        try:
            relevant = memory_store.retrieve_relevant(
                sim_a.sim_id, sim_b.sim_id, query=interaction, top_k=3
            )
            if relevant:
                mem_lines = "; ".join(
                    f"{m.get('text', '')[:80]} (valence={m.get('valence', 0):+.2f})"
                    for m in relevant
                )
                parts.append(f"RELEVANT MEMORIES (semantic): {mem_lines}")

            # Long-term consolidated memories for sim_a
            lt = memory_store.recall_long_term(sim_a.sim_id, query=interaction, n=1)
            if lt:
                parts.append(f"LONG-TERM MEMORY: {lt[:200]}")
        except Exception:
            pass

    # ── System 2: Dialogue buffer (working memory) + conversation arc ────────
    buffer = getattr(sim_a, "_dialogue_buffer", [])
    partner_id = getattr(sim_a, "_dialogue_partner", "")
    if buffer and partner_id == sim_b.sim_id:
        # Drop stale turns
        fresh = [
            t
            for t in buffer
            if (current_tick - t.get("tick", 0)) <= _DIALOGUE_STALE_TICKS
        ][-_DIALOGUE_BUFFER_MAX_TURNS:]
        if fresh:
            lines: list[str] = []
            for t in fresh:
                v_tag = f" [v={t['valence']:+.2f}]" if "valence" in t else ""
                lines.append(
                    f"  [{t['tick']}] {t.get('speaker_a', sim_a.name)}: "
                    f"{t.get('content_a', '')[:80]}{v_tag}"
                )
                lines.append(
                    f"           {t.get('speaker_b', sim_b.name)}: "
                    f"{t.get('content_b', '')[:80]}"
                )
            parts.append("RECENT DIALOGUE:\n" + "\n".join(lines))

    # Conversation escalation stage — tells LLM where the arc currently stands
    conv_stage = getattr(sim_a, "_conversation_stage", "small_talk")
    consent = getattr(sim_a, "_consent_state", {}).get(sim_b.sim_id, "")
    _STAGE_HINTS = {
        "small_talk":          "Opening — keep tone light and exploratory.",
        "teasing":             "Playful banter phase — gentle teasing and wit are welcome.",
        "disclosure":          "Personal disclosure phase — sincere, vulnerable sharing fits.",
        "affectionate_intent": "Affectionate phase — warm romantic intent is appropriate.",
    }
    stage_hint = _STAGE_HINTS.get(conv_stage, "")
    consent_note = ""
    if consent == "withdrawn":
        consent_note = " CONSENT WITHDRAWN — do not escalate or romanticise further."
    elif consent == "given" and conv_stage == "affectionate_intent":
        consent_note = " Mutual interest confirmed."
    parts.append(f"CONVERSATION STAGE: {conv_stage}. {stage_hint}{consent_note}")

    # ── World sensor context (engine-provided) ────────────────────────────────
    world_ctx = str(getattr(sim_a, "_world_context_line", "") or "").strip()
    if world_ctx:
        parts.append(f"WORLD SENSOR CONTEXT: {world_ctx}")

    # ── Civilization context: faction + history (engine-provided) ─────────────
    try:
        _eng_ref = getattr(sim_a, "_engine_ref", None)
        if _eng_ref is not None:
            faction_ctx = _eng_ref.factions.inject_context(sim_a.sim_id, _eng_ref)
            if faction_ctx:
                parts.append(f"FACTION: {faction_ctx}")
            hist_digest = _eng_ref.world_history.digest(last_n=3, location="global")
            if hist_digest:
                parts.append(f"WORLD HISTORY: {hist_digest}")
    except Exception:
        pass

    # ── ATOMIC commonsense ────────────────────────────────────────────────────
    atomic = query_atomic(interaction)
    if atomic:
        parts.append(f"ATOMIC: {atomic}")

    # ── Social IQA reasoning ──────────────────────────────────────────────────
    iqa = sample_social_iqa(interaction)
    if iqa:
        parts.append(f"SOCIAL_IQA: {iqa}")

    # ── Social bias escalation ────────────────────────────────────────────────
    if (
        datasets
        and hasattr(datasets, "social_bias_norms")
        and datasets.social_bias_norms
    ):
        from datasets.social_bias import (
            is_potentially_offensive,
            sample_conflict_escalation_context,
        )

        if is_potentially_offensive(interaction):
            ctx = sample_conflict_escalation_context()
            if ctx:
                parts.append(ctx)

    # ── Vulnerable sim flag ───────────────────────────────────────────────────
    vulnerable = (
        sim_a.profile["ocean"]["neuroticism"] > 0.7
        or sim_b.profile["ocean"]["neuroticism"] > 0.7
        or bool(sim_a.fears)
        or bool(sim_b.fears)
    )
    if vulnerable:
        parts.append("Vulnerable sim present; apply empathetic reasoning.")

    # ── Persona consistency examples ──────────────────────────────────────────
    if datasets and hasattr(datasets, "persona_chat") and datasets.persona_chat:
        from datasets.persona_chat import get_persona_examples

        examples = get_persona_examples(sim_a.ocean, n=2)
        if examples:
            parts.append("PERSONA EXAMPLES for Sim A's voice:\n" + "\n".join(examples))

    # ── Empathetic situational utterance (matches sim A's dominant emotion) ───
    # Skip for neutral — no emotional context to ground; also skip for content/prepared
    # (generic states that produce mundane/off-topic utterances from this dataset)
    _SKIP_EMPATH = {"neutral", "content", "prepared"}
    if (
        datasets
        and getattr(datasets, "empath_index", None)
        and sim_a.emotion.dominant not in _SKIP_EMPATH
    ):
        try:
            from datasets.empathetic import sample_empathetic_utterance

            empath_label = _GOEMO_TO_EMPATH.get(sim_a.emotion.dominant, "content")
            pool = datasets.empath_index.get(empath_label, [])
            if not pool:
                pool = [u for v in datasets.empath_index.values() for u in v]
            # Filter: clean encoding artifacts, require length ≥ 60, real sentence
            clean_pool = []
            for u in pool:
                c = u.replace("_comma_", ",").replace("_period_", ".").strip()
                if len(c) >= 60 and c[0].isupper() and not c.startswith("I am ok"):
                    clean_pool.append(c)
            if clean_pool:
                utt = random.choice(clean_pool)
                parts.append(
                    f"EMPATHETIC CONTEXT ({sim_a.emotion.dominant}): {utt[:140]}"
                )
        except Exception:
            pass

    # ── Dialogue situational example — keyword-matched to the interaction ─────
    if datasets and getattr(datasets, "dialogue_actions", None):
        try:
            pool = datasets.dialogue_actions
            # Score by keyword overlap with the interaction phrase
            kw = set(w.lower() for w in interaction.split() if len(w) > 3)
            if kw:
                scored = sorted(
                    pool,
                    key=lambda s: sum(w in s.lower() for w in kw),
                    reverse=True,
                )
                # Take the top 20 matches, pick one; fall back to length-filtered random
                top = [s for s in scored[:20] if len(s) >= 50]
            else:
                top = []
            if not top:
                top = [s for s in pool if len(s) >= 50]
            if top:
                parts.append(f"SITUATIONAL EXAMPLE: {random.choice(top)[:140]}")
        except Exception:
            pass

    return "\n".join(parts)


def build_adjudicator_system(
    norms: list[str],
    datasets: "DatasetRegistry | None" = None,
    interaction: str = "",
) -> str:
    prompt = ADJUDICATOR_SYSTEM_BASE

    if norms:
        prompt += "\n\nSOCIAL NORMS:\n" + "\n".join(f"- {n}" for n in norms)

    prompt += "\n\n" + build_emotion_calibration_block()

    if datasets and hasattr(datasets, "ethics_norms") and datasets.ethics_norms:
        from datasets.ethics import get_ethics_calibration

        ethics_block = get_ethics_calibration(n_commonsense=2, n_virtue=2)
        if ethics_block:
            prompt += ethics_block

    if (
        datasets
        and hasattr(datasets, "prosocial_nsfw_norms")
        and datasets.prosocial_nsfw_norms
        and any(
            tag in interaction.upper() for tag in ["[INTIMATE", "INTIMATE_ENCOUNTER"]
        )
    ):
        sample = datasets.prosocial_nsfw_norms[:4]
        prompt += "\n\nADULT CONTEXT NORMS:\n" + "\n".join(f"- {n}" for n in sample)

    return prompt


def get_life_event_context(event_type: str, narrative: str) -> str:
    try:
        from datasets.event2mind import emotional_cascade

        cascade = emotional_cascade(f"{event_type} {narrative}")
        parts: list[str] = []
        if cascade.get("xReact"):
            parts.append(f"Sim A likely feels: {', '.join(cascade['xReact'])}")
        if cascade.get("oReact"):
            parts.append(f"Others around them feel: {', '.join(cascade['oReact'])}")
        if cascade.get("xWant"):
            parts.append(f"Sim A now wants: {', '.join(cascade['xWant'])}")
        return "\n".join(parts)
    except Exception:
        return ""
