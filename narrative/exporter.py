"""
narrative/exporter.py — Structured story export layer (Gap 1).

Subscribes to the engine EventBus and writes timestamped, human-readable
story transcripts in BORU arc format (inciting → escalation → resolution)
to exports/story_<tick>.json on each chapter flush.

Attach via:
    from narrative.exporter import StoryExporter
    exporter = StoryExporter(engine, chapter_size=10)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

_EXPORTS_DIR = Path("exports")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class StoryExporter:
    """
    Collects simulation events and flushes a structured JSON chapter every
    `chapter_size` ticks.  Optionally samples a BORU arc scaffold to frame
    the chapter narrative.
    """

    def __init__(self, engine: "SimEngine", chapter_size: int = 10):
        self._engine       = engine
        self._chapter_size = chapter_size
        self._events: list[dict] = []
        self._chapter_num  = 0
        self._chapter_start_tick = 0
        _EXPORTS_DIR.mkdir(exist_ok=True)

        engine._bus.on("interaction_resolved", self._on_interaction)
        engine._bus.on("life_event",           self._on_life)
        engine._bus.on("career_event",         self._on_career)
        engine._bus.on("tick_complete",        self._on_tick)

    # ── Event collectors ──────────────────────────────────────────────────────

    def _on_interaction(self, **kw) -> None:
        result = kw.get("result", {})
        self._events.append({
            "type":      "interaction",
            "tick":      kw.get("tick", 0),
            "sim_a":     kw["sim_a"].name,
            "sim_b":     kw["sim_b"].name,
            "action":    kw.get("interaction", ""),
            "valence":   round(float(kw.get("valence", 0)), 2),
            "memory":    result.get("memory_tag", ""),
            "reaction":  result.get("sim_b_reaction", "")[:120],
            "emotion_a": result.get("emotion_a", ""),
            "emotion_b": result.get("emotion_b", ""),
        })

    def _on_life(self, **kw) -> None:
        result = kw.get("result", {})
        self._events.append({
            "type":      "life_event",
            "tick":      kw.get("tick", 0),
            "sim":       kw["sim_a"].name if kw.get("sim_a") else "unknown",
            "event":     result.get("event_type", ""),
            "narrative": result.get("narrative", "")[:200],
        })

    def _on_career(self, **kw) -> None:
        result = kw.get("result", {})
        self._events.append({
            "type":      "career_event",
            "tick":      kw.get("tick", 0),
            "sim":       kw["sim"].name if kw.get("sim") else "unknown",
            "event":     result.get("event_type", ""),
            "narrative": result.get("narrative", "")[:200],
        })

    # ── Flush trigger ─────────────────────────────────────────────────────────

    def _on_tick(self, **kw) -> None:
        tick = kw.get("tick", 0)
        if (tick - self._chapter_start_tick) >= self._chapter_size:
            self._flush(tick)

    def _flush(self, tick: int) -> None:
        if not self._events:
            return

        self._chapter_num += 1
        chapter = self._build_chapter(tick)

        filename = _EXPORTS_DIR / f"story_ch{self._chapter_num:04d}_tick{tick:04d}.json"
        try:
            filename.write_text(json.dumps(chapter, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("[EXPORT] Chapter %d written → %s (%d events)",
                        self._chapter_num, filename.name, len(self._events))
        except Exception as exc:
            logger.warning("Story export failed: %s", exc)

        self._events.clear()
        self._chapter_start_tick = tick

    def _build_chapter(self, end_tick: int) -> dict:
        """
        Frame collected events as a BORU-style chapter:
          inciting    → first significant event
          escalation  → mid-chapter peak (highest |valence|)
          resolution  → last event
        """
        events = self._events

        # Title: name the two most-mentioned Sims
        sim_mentions: dict[str, int] = {}
        for ev in events:
            for key in ("sim_a", "sim_b", "sim"):
                name = ev.get(key)
                if name:
                    sim_mentions[name] = sim_mentions.get(name, 0) + 1
        top_sims = sorted(sim_mentions, key=sim_mentions.get, reverse=True)[:2]
        title = (
            f"{' & '.join(top_sims)} — Chapter {self._chapter_num}"
            if top_sims else f"Chapter {self._chapter_num}"
        )

        # Inciting: first event with any narrative or interaction
        inciting = self._summarise_event(events[0]) if events else ""

        # Escalation: highest absolute valence event
        peak = max(
            (ev for ev in events if ev.get("valence") is not None),
            key=lambda e: abs(e.get("valence", 0)),
            default=events[len(events) // 2] if events else None,
        )
        escalation = self._summarise_event(peak) if peak else ""

        # Resolution: last event
        resolution = self._summarise_event(events[-1]) if len(events) > 1 else ""

        # BORU scaffold from dataset if available
        boru_scaffold = None
        try:
            from datasets.boru import sample_arc
            boru_scaffold = sample_arc()
        except Exception:
            pass

        return {
            "chapter":    self._chapter_num,
            "title":      title,
            "ticks":      [self._chapter_start_tick, end_tick],
            "timestamp":  _now(),
            "boru_arc": {
                "inciting":   inciting,
                "escalation": escalation,
                "resolution": resolution,
                "scaffold":   boru_scaffold,   # raw BORU arc for inspiration
            },
            "events": events,
            "sim_activity": sim_mentions,
        }

    @staticmethod
    def _summarise_event(ev: dict) -> str:
        if not ev:
            return ""
        if ev["type"] == "interaction":
            return (
                f"[Tick {ev['tick']}] {ev['sim_a']} → {ev['sim_b']}: "
                f"{ev['action']} (valence {ev['valence']:+.2f}). "
                f"Reaction: {ev['reaction'][:80]}"
            )
        if ev["type"] in ("life_event", "career_event"):
            return f"[Tick {ev['tick']}] {ev.get('sim', '?')}: {ev.get('narrative', '')[:120]}"
        return str(ev)

    def flush_now(self) -> None:
        """Force an export flush immediately."""
        self._flush(self._engine.tick_count)
