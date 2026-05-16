"""
datasets/group_scenes.py — Multi-party dialogue scenes for group interactions.

Sources:
  michellejieli/friends_dataset — 1,000 Friends TV scenes, 3-6 speakers/scene,
                                    speaker + utterance + emotion per line
  marcodsn/SOC-2508            — Synthetic Online Conversations with explicit
                                    relationship triggers (why this convo starts now)

Group event mechanics:
  Triggers when 3+ ACTIVE sims are in high-crowd venue (crowd >= 0.7)
  One sim initiates; 2+ witnesses tagged; all receive relationship deltas
  Comedy in group (skill >= 5): raises friendship with all witnesses
  Argument in group: faction dynamics (witnesses side with higher-friendship sim)
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY_SCENES   = "group_scenes"
_CACHE_KEY_TRIGGERS = "group_triggers"
_MAX_SCENES   = 500
_MAX_TRIGGERS = 300


def load_group_scenes() -> list[dict]:
    """Returns list of {speakers: [str], lines: [{speaker, text, emotion}]} scene dicts."""
    cached = cache_load(_CACHE_KEY_SCENES)
    if cached:
        return cached

    scenes: list[dict] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("michellejieli/friends_dataset", split="train",
                          streaming=True, trust_remote_code=True)
        current_scene: list[dict] = []
        current_id = None
        count = 0

        for row in ds:
            if count >= _MAX_SCENES:
                break
            scene_id = row.get("scene_id") or row.get("episode") or str(count)
            speaker  = (row.get("speaker") or row.get("character") or "Unknown").strip()
            text     = (row.get("utterance") or row.get("text") or "").strip()
            emotion  = (row.get("emotion") or "neutral").strip()

            if scene_id != current_id:
                if current_scene and len({l["speaker"] for l in current_scene}) >= 2:
                    speakers = list({l["speaker"] for l in current_scene})
                    scenes.append({"speakers": speakers, "lines": current_scene[:8]})
                    count += 1
                current_scene = []
                current_id = scene_id

            if text:
                current_scene.append({"speaker": speaker, "text": text, "emotion": emotion})

        cache_save(_CACHE_KEY_SCENES, scenes)
    except Exception:
        pass
    return scenes


def load_group_triggers() -> list[str]:
    """Returns list of conversation trigger phrases from SOC-2508."""
    cached = cache_load(_CACHE_KEY_TRIGGERS)
    if cached:
        return cached

    triggers: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("marcodsn/SOC-2508", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(triggers) >= _MAX_TRIGGERS:
                break
            trigger = (row.get("trigger") or row.get("scenario") or
                       row.get("context") or row.get("text") or "").strip()
            if trigger and 10 < len(trigger) < 200:
                triggers.append(trigger)
    except Exception:
        pass
    if triggers:
        cache_save(_CACHE_KEY_TRIGGERS, triggers)
    return triggers


def sample_group_scene() -> dict | None:
    scenes = load_group_scenes()
    return random.choice(scenes) if scenes else None


def sample_trigger() -> str | None:
    triggers = load_group_triggers()
    return random.choice(triggers) if triggers else None


def format_group_interaction(scene: dict, sim_names: list[str],
                              trigger: str | None = None) -> str:
    """
    Map Friends characters to sim names and format as a group scene prompt.
    """
    char_to_sim = {}
    for i, char in enumerate(scene.get("speakers", [])):
        if i < len(sim_names):
            char_to_sim[char] = sim_names[i]

    lines_text = "\n".join(
        f"  {char_to_sim.get(l['speaker'], l['speaker'])}: \"{l['text'][:120]}\""
        for l in scene.get("lines", [])[:5]
    )
    trigger_note = f"Trigger: {trigger}\n" if trigger else ""
    return (
        f"[GROUP EVENT — {len(sim_names)} Sims present]\n"
        f"{trigger_note}"
        f"Scene template:\n{lines_text}\n"
        f"Adjudicate how all Sims (initiator + witnesses) are affected. "
        f"Comedy/warmth in a group raises friendship with all witnesses. "
        f"Conflict creates faction dynamics — bystanders side with whoever they know better."
    )
