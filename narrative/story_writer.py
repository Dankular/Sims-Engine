"""
narrative/story_writer.py — LLM-powered story generator.

Takes a batch of simulation events and asks the LLM to write a short
narrator+dialogue script suitable for TTS narration.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.backend import LLMBackend

logger = logging.getLogger(__name__)

STORY_SYSTEM = """You are a dramatic narrator for an AI life simulation called "The Sims Engine".
Your job is to turn raw simulation events into a compelling, emotionally rich audiobook-style story.

Write in present tense, third person. Be vivid but concise — each segment should feel like a chapter beat.
Include the sims' emotions, motivations, and personality where relevant.

ALWAYS respond with valid JSON only — no markdown, no preamble.

Return this exact structure:
{
  "segments": [
    {"speaker": "narrator", "text": "<1-3 dramatic sentences describing what happened>"},
    {"speaker": "<Sim Name>", "text": "<optional short in-character line the sim would say>"},
    {"speaker": "narrator", "text": "<optional closing beat or emotional note>"}
  ]
}

Rules:
- narrator segments: 1-3 sentences, vivid and story-like
- sim dialogue: 1 short sentence max, true to their traits and emotion
- only include dialogue when it adds drama or character
- keep total segments between 2 and 5"""


def _build_event_summary(events: list[dict]) -> str:
    lines = []
    for ev in events:
        etype = ev.get("type", "event")
        if etype == "interaction":
            lines.append(
                f"INTERACTION: {ev['sim_a']} → {ev['sim_b']} [{ev['action']}] "
                f"| valence={ev['valence']:+.2f} | memory=\"{ev.get('memory', '')}\" "
                f"| reaction=\"{ev.get('reaction', '')}\" | reasoning=\"{ev.get('reasoning', '')}\""
            )
        elif etype == "career":
            lines.append(
                f"CAREER EVENT: {ev['sim']} ({ev.get('event_type', '?')}) "
                f"| {ev.get('narrative', '')} | perf{ev.get('performance_delta', 0):+.0f} §{ev.get('simoleon_delta', 0):+.0f}"
            )
        elif etype == "life":
            lines.append(
                f"LIFE EVENT: {ev.get('event_type', '?')} — {ev.get('narrative', '')}"
            )
    return "\n".join(lines) if lines else "No significant events this tick."


def generate_story_script(
    llm: "LLMBackend",
    events: list[dict],
    sim_profiles: list[dict],
    tick: int,
) -> list[dict]:
    """Ask the LLM to narrate a batch of events. Returns list of {speaker, text} dicts."""
    if not events:
        return []

    profile_summary = "\n".join(
        f"- {p['name']}: {p['job']}, traits={p['traits']}, aspiration={p['aspiration']}, "
        f"emotion={p.get('emotion', 'neutral')}"
        for p in sim_profiles
    )
    event_summary = _build_event_summary(events)

    # Class 4: Hippocorpus narrative style scaffolding
    narrative_style = ""
    avg_valence = sum(e.get("valence", 0) for e in events if "valence" in e)
    if events:
        avg_valence /= len([e for e in events if "valence" in e] or [1])
    try:
        from datasets.hippocorpus import sample_narrative_scaffold, get_memory_drift_note
        sim_openness = sim_profiles[0].get("ocean", {}).get("openness", 0.5) \
                       if sim_profiles else 0.5
        scaffold = sample_narrative_scaffold(avg_valence, sim_openness)
        drift_note = get_memory_drift_note(avg_valence)
        if scaffold:
            narrative_style = (
                f"\nNARRATIVE STYLE GUIDE:\n{drift_note}\n"
                f"Reference tone: \"{scaffold[:250]}\"\n"
            )
    except Exception:
        pass

    user_prompt = (
        f"Tick {tick} — narrate these simulation events as a story:\n\n"
        f"SIMS:\n{profile_summary}\n\n"
        f"EVENTS:\n{event_summary}\n"
        f"{narrative_style}\n"
        "Return only the JSON script."
    )

    try:
        raw = llm.chat(
            system=STORY_SYSTEM,
            user=user_prompt,
            max_tokens=400,
            temperature=0.85,
        )
        # Strip think tags / markdown fences
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        if not raw.startswith("{"):
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                raw = match.group(0)
        data = json.loads(raw)
        segments = data.get("segments", [])
        if isinstance(segments, list):
            return [s for s in segments if isinstance(s, dict) and s.get("text")]
    except Exception as exc:
        logger.warning("Story generation failed: %s", exc)

    return []
