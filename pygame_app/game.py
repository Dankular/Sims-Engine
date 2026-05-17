"""
pygame_app/game.py — Game loop orchestration for the information-rich dashboard.

Supports both:
  - Tick-based: engine.run_tick() on a timer
  - Real-time:  rt.update() every frame (pass realtime=True)
"""
from __future__ import annotations

import queue
import threading
import time
from typing import TYPE_CHECKING

import pygame

from pygame_app import colors as C

if TYPE_CHECKING:
    from engine.engine import SimEngine
    from engine.realtime import RealtimeSimEngine
    from tts.engine import TTSEngine

SPEEDS = [0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0]


class Game:
    W, H = 1600, 900

    def __init__(
        self,
        engine: "SimEngine",
        tts:    "TTSEngine | None"       = None,
        rt:     "RealtimeSimEngine | None" = None,
    ):
        self.engine  = engine
        self.tts     = tts
        self._rt     = rt

        self.running  = True
        self.paused   = False
        self._speed_idx = 3
        self._tab_idx  = 0

        # State is written by engine thread, read by render thread.
        # get_state() returns a plain dict (JSON-safe) so the snapshot
        # is safe to pass across threads without a lock.
        self._state_lock = threading.Lock()
        self._state: dict = engine.get_state()

        self.selected_sim_id: str | None = None

        # Event feed — engine thread appends, render thread reads.
        # Use a lock so list mutations are safe.
        self._feed_lock   = threading.Lock()
        self.event_log: list[dict]    = []
        self.valence_history: list[float] = []
        self.last_model_trace: list[tuple] = []

        # TTS synthesis (separate daemon thread)
        self._tts_queue = queue.Queue()
        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

        # Subscribe to engine events (callbacks fire on engine thread — safe)
        engine._bus.on("interaction_queued",   self._on_queued)
        engine._bus.on("interaction_resolved", self._on_resolved)
        engine._bus.on("career_event",         self._on_career)
        engine._bus.on("life_event",           self._on_life)
        engine._bus.on("child_born",           self._on_child_born)
        engine._bus.on("stage_transition",     self._on_stage)
        engine._bus.on("sim_died",             self._on_died)

        # Start engine on its own thread — keeps render loop unblocked
        self._engine_thread = threading.Thread(
            target=self._engine_loop, daemon=True, name="engine"
        )
        self._engine_thread.start()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def speed(self) -> float:
        return SPEEDS[self._speed_idx]

    @property
    def state(self) -> dict:
        with self._state_lock:
            return self._state

    def get_feed_snapshot(self) -> tuple[list, list, list]:
        """Thread-safe snapshot of feed data for the renderer."""
        with self._feed_lock:
            return (
                list(self.event_log),
                list(self.valence_history),
                list(self.last_model_trace),
            )

    # ── Engine background thread ──────────────────────────────────────────────

    def _engine_loop(self) -> None:
        """
        Runs engine ticks (or rt.update()) on a background thread.
        The render loop never touches the engine directly — only reads
        the state snapshot via self.state.
        """
        accum = 0.0
        last  = time.monotonic()

        while self.running:
            now     = time.monotonic()
            elapsed = now - last
            last    = now

            if not self.paused:
                if self._rt:
                    # Real-time: update as fast as possible
                    self._rt.update()
                    snap = self._rt.get_state()
                else:
                    # Tick-based: fire at self.speed ticks per second
                    accum += elapsed * self.speed
                    if accum >= 1.0:
                        accum -= 1.0
                        self.engine.run_tick()
                    snap = self.engine.get_state()

                with self._state_lock:
                    self._state = snap

            # Yield to avoid pegging a core
            time.sleep(0.005)

    # ── Render loop (main thread — never touches engine) ──────────────────────

    def run(self, renderer) -> None:
        clock = pygame.time.Clock()
        while self.running:
            clock.tick(60)
            self._handle_events(renderer)
            renderer.draw(self)
            pygame.display.flip()

    # ── Input handling ────────────────────────────────────────────────────────

    def _handle_events(self, renderer) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.KEYDOWN:
                k = event.key
                if k == pygame.K_ESCAPE:
                    self.running = False
                elif k == pygame.K_SPACE:
                    self.paused = not self.paused
                elif k == pygame.K_n and not self._rt:
                    self.engine.run_tick()
                    # state updated by engine thread automatically
                    pass
                elif k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                    if self._rt:
                        self._rt.set_speed(self._rt.clock.speed * 2)
                    else:
                        self._speed_idx = min(len(SPEEDS) - 1, self._speed_idx + 1)
                elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    if self._rt:
                        self._rt.set_speed(max(1, self._rt.clock.speed / 2))
                    else:
                        self._speed_idx = max(0, self._speed_idx - 1)
                elif k == pygame.K_TAB:
                    self._cycle_focus()

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                renderer.handle_click(event.pos, self)

    def _cycle_focus(self) -> None:
        sims = self.state.get("sims", [])
        ids  = [s["id"] for s in sims]
        if not ids:
            return
        if self.selected_sim_id not in ids:
            self._tab_idx = 0
        else:
            self._tab_idx = (ids.index(self.selected_sim_id) + 1) % len(ids)
        self.selected_sim_id = ids[self._tab_idx]

    # ── Engine event callbacks ────────────────────────────────────────────────

    def _on_queued(self, **kw) -> None:
        sim_a  = kw["sim_a"]
        sim_b  = kw["sim_b"]
        action = kw.get("interaction", "")
        self._add_event(
            "⚡", f"{sim_a.name} → {sim_b.name}  [{action[:40]}]",
            C.TEXT_DIM,
        )

    def _on_resolved(self, **kw) -> None:
        result  = kw.get("result", {})
        sim_a   = kw["sim_a"]
        sim_b   = kw["sim_b"]
        valence = float(kw.get("valence", 0))
        action  = kw.get("interaction", "")
        fd      = float(result.get("friendship_delta", 0))
        rd      = float(result.get("romance_delta", 0))
        memory  = result.get("memory_tag", "")
        reaction= result.get("sim_b_reaction", "")
        emo_a   = result.get("emotion_a", "")
        emo_b   = result.get("emotion_b", "")

        with self._feed_lock:
            self.valence_history.append(valence)
            if len(self.valence_history) > 200:
                self.valence_history = self.valence_history[-200:]

        v_col = C.VALENCE_POS if valence > 0.15 else C.VALENCE_NEG if valence < -0.15 else C.VALENCE_NEU

        sub = []
        if reaction:
            sub.append((f'"{reaction[:60]}"', C.TEXT_DIM))
        sub.append((
            f"F:{fd:+.1f}  R:{rd:+.1f}  Val:{valence:+.2f}",
            v_col,
        ))
        if emo_a or emo_b:
            sub.append((f"Emo: {sim_a.name}→{emo_a}  {sim_b.name}→{emo_b}", C.TEXT_DIM))
        if memory:
            sub.append((f"Mem: {memory[:50]}", C.TEXT_GHOST))

        self._add_event(
            "✅", f"{sim_a.name} → {sim_b.name}  [{action[:36]}]",
            v_col, sub=sub,
        )

        with self._feed_lock:
            self.last_model_trace = self._build_model_trace(result, valence)

        # TTS
        if reaction and self.tts:
            self.queue_tts(sim_b.name, reaction, emotion=emo_b)

    def _build_model_trace(self, result: dict, valence: float) -> list[tuple]:
        """Extract model contribution signals from the result dict."""
        trace: list[tuple] = []
        trace.append(("Valence", f"{valence:+.3f}", C.valence_colour_norm(valence + 0.5) if hasattr(C, 'valence_colour_norm') else C.VALENCE_NEU))
        if result.get("emotion_a"):
            trace.append(("GoEmo A", result["emotion_a"], C.emotion_colour(result["emotion_a"])))
        if result.get("emotion_b"):
            trace.append(("GoEmo B", result["emotion_b"], C.emotion_colour(result["emotion_b"])))
        if result.get("memory_tag"):
            trace.append(("Memory", result["memory_tag"][:40], C.TEXT_DIM))
        if result.get("reasoning"):
            trace.append(("Reason", result["reasoning"][:60], C.TEXT_GHOST))
        fd = float(result.get("friendship_delta", 0))
        if abs(fd) > 0:
            fc = C.VALENCE_POS if fd > 0 else C.VALENCE_NEG
            trace.append(("Friendship Δ", f"{fd:+.1f}", fc))
        return trace

    def _on_career(self, **kw) -> None:
        result = kw.get("result", {})
        sim    = kw["sim"]
        delta  = result.get("performance_delta", 0)
        col    = C.VALENCE_POS if delta >= 0 else C.VALENCE_NEG
        self._add_event(
            "💼",
            f"{sim.name} — {result.get('event_type','?')}: {result.get('narrative','')[:60]}",
            col,
        )

    def _on_life(self, **kw) -> None:
        result = kw.get("result", {})
        sim_a  = kw.get("sim_a")
        name   = sim_a.name if sim_a else "?"
        self._add_event(
            "🌟",
            f"[{result.get('event_type','?')}] {name}: {result.get('narrative','')[:60]}",
            C.TEXT_GOLD,
        )
        pass  # state snapshot updated by engine thread

    def _on_child_born(self, **kw) -> None:
        child = kw["child"]
        pa    = kw["parent_a"]
        pb    = kw["parent_b"]
        self._add_event(
            "👶",
            f"{child.name} born to {pa.name} & {pb.name}",
            C.STAGE_COLOUR.get("child", C.TEXT_BRIGHT),
            sub=[(f"Traits: {', '.join(child.profile['traits'][:3])}", C.TEXT_DIM)],
        )
        pass  # state snapshot updated by engine thread

    def _on_stage(self, **kw) -> None:
        sim     = kw["sim"]
        new_stg = kw["new_stage"].replace("_", " ")
        age     = kw["age"]
        col     = C.STAGE_COLOUR.get(kw["new_stage"], C.TEXT)
        self._add_event(
            "🎂",
            f"{sim.name} turns {age}  →  {new_stg}",
            col,
        )

    def _on_died(self, **kw) -> None:
        sim = kw["sim"]
        age = kw["age"]
        self._add_event(
            "✝",
            f"{sim.name}  passed away at age {age}",
            C.TEXT_DIM,
            sub=[(f"Aspiration: {sim.profile.get('aspiration','?')}  §{sim.simoleons:.0f}", C.TEXT_GHOST)],
        )
        pass  # state snapshot updated by engine thread

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _add_event(
        self,
        icon: str,
        text: str,
        colour: tuple = C.TEXT,
        sub: list | None = None,
    ) -> None:
        entry = {"icon": icon, "text": text, "colour": colour, "sub": sub or []}
        with self._feed_lock:
            self.event_log.insert(0, entry)
            self.event_log = self.event_log[:80]

    def queue_tts(self, speaker: str, text: str, tick: int = 0, emotion: str = "") -> None:
        if self.tts:
            self._tts_queue.put((speaker, text, tick, emotion))

    def _tts_worker(self) -> None:
        while True:
            item = self._tts_queue.get()
            if item is None:
                break
            speaker, text, tick, emotion = ((*item, "")[:4])
            try:
                if self.tts:
                    self.tts.speak(speaker, text, tick=tick, emotion=emotion)
            except Exception:
                pass
            self._tts_queue.task_done()

    def shutdown(self) -> None:
        self._tts_queue.put(None)
        if self._rt:
            self._rt.shutdown()
        else:
            self.engine.shutdown()
