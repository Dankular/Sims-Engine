"""
pygame_app/game.py — Game state + loop orchestration.

Wires SimEngine to the pygame render/event loop. run_tick() is called
on a configurable timer; TTS synthesis runs in a daemon thread so it
never blocks a frame.
"""
from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    from engine.engine import SimEngine
    from tts.engine import TTSEngine
    from narrative.story_runner import StoryRunner

SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0]   # ticks per real second


class Game:
    W, H = 1280, 720

    def __init__(
        self,
        engine: "SimEngine",
        tts: "TTSEngine | None" = None,
        story_runner: "StoryRunner | None" = None,
    ):
        self.engine = engine
        self.tts = tts
        self.story_runner = story_runner

        self.running = True
        self.paused = False
        self._speed_idx = 2            # index into SPEEDS; default 1.0 t/s
        self._accum = 0.0              # time since last tick (seconds)

        # Last engine snapshot — updated once per tick
        self._state: dict = engine.get_state()

        # Event log: list of dicts {icon, text, tick}
        self._event_log: list[dict] = []

        # Story segments queue: {speaker, text}
        self._story_segments: list[dict] = []
        self._tts_queue: queue.Queue = queue.Queue()
        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

        # Selected sim id
        self.selected_sim_id: str | None = None

        # Subscribe to engine events
        engine._bus.on("interaction_resolved", self._on_interaction)
        engine._bus.on("career_event",         self._on_career)
        engine._bus.on("life_event",            self._on_life)
        engine._bus.on("child_born",            self._on_child_born)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def speed(self) -> float:
        return SPEEDS[self._speed_idx]

    @property
    def tick_interval(self) -> float:
        return 1.0 / self.speed

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, renderer) -> None:
        clock = pygame.time.Clock()
        while self.running:
            dt = clock.tick(60) / 1000.0

            self._handle_pygame_events()

            if not self.paused:
                self._accum += dt
                if self._accum >= self.tick_interval:
                    self._accum -= self.tick_interval
                    self.engine.run_tick()
                    self._state = self.engine.get_state()

            renderer.draw(self)
            pygame.display.flip()

    # ── Input ─────────────────────────────────────────────────────────────────

    def set_renderer(self, renderer) -> None:
        self._renderer = renderer

    def _handle_pygame_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_SPACE:
                    self.paused = not self.paused
                elif event.key == pygame.K_n:
                    self.engine.run_tick()
                    self._state = self.engine.get_state()
                    self._accum = 0.0
                elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                    self._speed_idx = min(len(SPEEDS) - 1, self._speed_idx + 1)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    self._speed_idx = max(0, self._speed_idx - 1)
                elif event.key == pygame.K_s:
                    self._toggle_story()

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if hasattr(self, "_renderer"):
                    self._renderer.handle_click(event.pos, self)

    def _toggle_story(self) -> None:
        # Story mode toggle: attach/detach StoryRunner
        pass

    # ── Engine event callbacks ────────────────────────────────────────────────

    def _on_interaction(self, **kw) -> None:
        result = kw.get("result", {})
        sim_a = kw["sim_a"]
        sim_b = kw["sim_b"]
        valence = float(kw.get("valence", 0))
        memory = result.get("memory_tag", "")
        fd = float(result.get("friendship_delta", 0))
        rd = float(result.get("romance_delta", 0))
        action = kw.get("interaction", "")
        sign = "+" if valence >= 0 else ""
        self._add_event(
            "⚡",
            f"{sim_a.name} → {sim_b.name}  [{action}]  "
            f"F:{fd:+.0f} R:{rd:+.0f}  val:{sign}{valence:.2f}  \"{memory}\"",
            kw.get("tick", 0),
        )
        if result.get("reasoning"):
            self._add_story_event(result.get("reasoning", ""), is_narrator=True)

    def _on_career(self, **kw) -> None:
        result = kw.get("result", {})
        self._add_event(
            "💼",
            f"{kw['sim'].name} — {result.get('event_type','?')}: {result.get('narrative','')}",
            kw.get("tick", 0),
        )

    def _on_life(self, **kw) -> None:
        result = kw.get("result", {})
        self._add_event(
            "🌟",
            f"{result.get('event_type','life event')}: {result.get('narrative','')}",
            kw.get("tick", 0),
        )

    def _on_child_born(self, **kw) -> None:
        child = kw["child"]
        pa = kw["parent_a"]
        pb = kw["parent_b"]
        self._add_event(
            "👶",
            f"{child.name} born to {pa.name} & {pb.name}  "
            f"[traits: {', '.join(child.profile['traits'])}]",
            kw.get("tick", 0),
        )
        # Refresh state so new sim appears immediately
        self._state = self.engine.get_state()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _add_event(self, icon: str, text: str, tick: int) -> None:
        self._event_log.insert(0, {"icon": icon, "text": text, "tick": tick})
        self._event_log = self._event_log[:60]   # keep last 60

    def _add_story_event(self, text: str, is_narrator: bool = True) -> None:
        speaker = "narrator" if is_narrator else "sim"
        self._story_segments.insert(0, {"speaker": speaker, "text": text})
        self._story_segments = self._story_segments[:8]

    def queue_tts(self, speaker: str, text: str, tick: int = 0) -> None:
        """Queue a TTS segment for background synthesis + playback."""
        if self.tts:
            self._tts_queue.put((speaker, text, tick))

    def _tts_worker(self) -> None:
        while True:
            item = self._tts_queue.get()
            if item is None:
                break
            speaker, text, tick = item
            try:
                if self.tts:
                    self.tts.speak(speaker, text, tick=tick)
            except Exception:
                pass
            self._tts_queue.task_done()

    def shutdown(self) -> None:
        self._tts_queue.put(None)
        self.engine.shutdown()
