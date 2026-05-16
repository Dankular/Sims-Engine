"""
pygame_app/main.py — Pygame entry point.

Usage:
    python pygame_app/main.py
    python pygame_app/main.py --sims 5 --story
"""
from __future__ import annotations

import argparse
import os
import sys

# Ensure project root is on path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pygame

from pygame_app.game import Game
from pygame_app.renderer import Renderer


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Sims Engine — Pygame")
    p.add_argument("--sims",          type=int,   default=3)
    p.add_argument("--backend",       default="ollama",
                   choices=["ollama", "llama-server", "llama-cpp"])
    p.add_argument("--no-datasets",   action="store_true")
    p.add_argument("--story",         action="store_true",
                   help="Enable LLM story narration (text only in-window)")
    p.add_argument("--tts",           action="store_true",
                   help="Enable Supertonic TTS audio output alongside story")
    p.add_argument("--width",         type=int, default=1280)
    p.add_argument("--height",        type=int, default=720)
    p.add_argument("--ollama-model",  default=None)
    return p


def main() -> None:
    args = _build_parser().parse_args()

    # ── LLM backend ──────────────────────────────────────────────────────────
    if args.ollama_model:
        os.environ["SIM_V2_OLLAMA_MODEL"] = args.ollama_model

    from llm.backend import create_backend
    llm = create_backend(args.backend)
    print(f"[INFO] LLM backend: {args.backend}")

    # ── Datasets ─────────────────────────────────────────────────────────────
    datasets = None
    essays: list[str] = []
    if not args.no_datasets:
        print("[INFO] Loading datasets...")
        from datasets.loader import load_all_datasets
        datasets = load_all_datasets()
        essays = datasets.okcupid_essays
        print(f"[INFO] {len(datasets.social_norms)} norms, {len(essays)} essays ready.")

    # ── Sims ─────────────────────────────────────────────────────────────────
    from identity.profile_factory import generate_sim_profile
    from core.sim import Sim
    print(f"[INFO] Generating {args.sims} sims...")
    sims = [Sim(generate_sim_profile(okcupid_essays=essays or None)) for _ in range(args.sims)]

    from world.households import assign_households
    households = assign_households(sims)

    from persistence.sqlite import PersistenceLayer
    db = PersistenceLayer()

    # ── Engine ────────────────────────────────────────────────────────────────
    from engine.engine import SimEngine
    engine = SimEngine(sims=sims, llm=llm, datasets=datasets, db=db)
    engine.households = households

    # ── TTS (optional) ────────────────────────────────────────────────────────
    tts = None
    if args.tts:
        from tts.engine import TTSEngine
        tts = TTSEngine(quality=8, speed=1.0, save_audio=True)
        tts.assign_voices([s.name for s in sims])
        print("[INFO] TTS ready.")

    # ── Story runner (optional) ───────────────────────────────────────────────
    story_runner = None
    if args.story:
        from narrative.story_runner import attach as attach_story
        story_runner = attach_story(engine, tts or _NullTTS(), llm, narrate_every=1)
        print("[INFO] Story mode ON.")

    # ── Pygame init ───────────────────────────────────────────────────────────
    pygame.init()
    pygame.display.set_caption("Sims Engine")

    # Try to set a nice window icon
    try:
        icon = pygame.Surface((32, 32))
        icon.fill((28, 34, 52))
        pygame.draw.circle(icon, (80, 180, 120), (16, 16), 12)
        pygame.display.set_icon(icon)
    except Exception:
        pass

    W, H = args.width, args.height
    surface = pygame.display.set_mode((W, H))

    renderer = Renderer(surface)
    game = Game(engine, tts=tts, story_runner=story_runner)
    game.set_renderer(renderer)

    print(f"[INFO] Window open {W}×{H} — SPACE=pause  N=tick  +/-=speed  ESC=quit\n")

    try:
        game.run(renderer)
    finally:
        game.shutdown()
        pygame.quit()
        print("[INFO] Pygame closed.")


class _NullTTS:
    """Drop-in for TTSEngine when TTS is disabled."""
    def speak(self, *a, **kw): pass
    def speak_script(self, *a, **kw): pass
    def assign_voices(self, *a, **kw): pass


if __name__ == "__main__":
    main()
