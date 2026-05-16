"""
narrative/story_runner.py — Wires the event bus to story generation and TTS.

Attach to a SimEngine via attach(engine, tts, llm). After every N ticks
(or when a significant event fires), it collects events, generates a story
script, and speaks it through the TTS engine.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine
    from llm.backend import LLMBackend
    from tts.engine import TTSEngine

from narrative.story_writer import generate_story_script

logger = logging.getLogger(__name__)


class StoryRunner:
    """
    Collects simulation events and narrates them via TTS every `narrate_every` ticks.
    """

    def __init__(
        self,
        engine: "SimEngine",
        tts: "TTSEngine",
        llm: "LLMBackend",
        narrate_every: int = 1,
    ):
        self._engine = engine
        self._tts = tts
        self._llm = llm
        self._narrate_every = narrate_every
        self._pending_events: list[dict] = []
        self._last_narrated_tick = -1

        # Subscribe to engine event bus
        engine._bus.on("interaction_resolved", self._on_interaction)
        engine._bus.on("career_event", self._on_career)
        engine._bus.on("life_event", self._on_life)
        engine._bus.on("child_born", self._on_child_born)
        engine._bus.on("tick_complete", self._on_tick_complete)

    # ── Event collectors ──────────────────────────────────────────────────────

    def _on_interaction(self, **kw):
        result = kw.get("result", {})
        self._pending_events.append({
            "type": "interaction",
            "sim_a": kw["sim_a"].name,
            "sim_b": kw["sim_b"].name,
            "action": kw.get("interaction", ""),
            "valence": float(kw.get("valence", 0)),
            "memory": result.get("memory_tag", ""),
            "reaction": result.get("sim_b_reaction", ""),
            "reasoning": result.get("reasoning", ""),
        })

    def _on_career(self, **kw):
        result = kw.get("result", {})
        self._pending_events.append({
            "type": "career",
            "sim": kw["sim"].name,
            "event_type": result.get("event_type", ""),
            "narrative": result.get("narrative", ""),
            "performance_delta": result.get("performance_delta", 0),
            "simoleon_delta": result.get("simoleon_delta", 0),
        })

    def _on_life(self, **kw):
        result = kw.get("result", {})
        self._pending_events.append({
            "type": "life",
            "event_type": result.get("event_type", "life event"),
            "narrative": result.get("narrative", ""),
        })

    def _on_child_born(self, **kw):
        child = kw["child"]
        parent_a = kw["parent_a"]
        parent_b = kw["parent_b"]
        self._pending_events.append({
            "type": "life",
            "event_type": "child_born",
            "narrative": (
                f"{child.name} was born to {parent_a.name} and {parent_b.name}. "
                f"The child inherited traits: {', '.join(child.profile['traits'])}."
            ),
        })

    # ── Narration trigger ─────────────────────────────────────────────────────

    def _on_tick_complete(self, **kw):
        tick = kw.get("tick", 0)
        if not self._pending_events:
            return
        if (tick - self._last_narrated_tick) < self._narrate_every:
            return

        self._last_narrated_tick = tick
        events = list(self._pending_events)
        self._pending_events.clear()

        # Build slim sim profile list for context
        sim_profiles = [
            {
                "name": s.name,
                "job": s.profile["job"],
                "traits": s.profile["traits"],
                "aspiration": s.profile["aspiration"],
                "emotion": s.emotion.dominant,
            }
            for s in self._engine.sims
        ]

        print(f"\n  📖 Narrating tick {tick}...")
        segments = generate_story_script(self._llm, events, sim_profiles, tick)
        if not segments:
            print("  [Story] No script generated.")
            return

        # System 3: annotate segments with dominant emotion from events
        dominant_emotion = ""
        for ev in events:
            if ev.get("type") == "interaction":
                dominant_emotion = (
                    self._engine._sim_lookup.get(
                        next((s.sim_id for s in self._engine.sims if s.name == ev["sim_a"]), ""),
                        None,
                    )
                )
                if dominant_emotion and hasattr(dominant_emotion, "emotion"):
                    dominant_emotion = dominant_emotion.emotion.dominant
                    break
        emotion_tagged = [
            {**seg, "emotion": seg.get("emotion", dominant_emotion)}
            for seg in segments
        ]

        # Print script to terminal
        for seg in emotion_tagged:
            speaker = seg["speaker"]
            text = seg["text"]
            tag = "📣" if speaker.lower() == "narrator" else f"💬 {speaker}"
            print(f"  {tag}: {text}")

        # Speak it
        self._tts.speak_script(emotion_tagged, tick=tick)


    def flush(self) -> None:
        """Narrate any events still pending (e.g. after flush_pending drain)."""
        if self._pending_events:
            self._on_tick_complete(tick=self._engine.tick_count)


def attach(
    engine: "SimEngine",
    tts: "TTSEngine",
    llm: "LLMBackend",
    narrate_every: int = 1,
) -> StoryRunner:
    """Attach a StoryRunner to the engine and return it."""
    runner = StoryRunner(engine, tts, llm, narrate_every=narrate_every)
    return runner
