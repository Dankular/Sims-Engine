"""
pygame_app/main.py — Pygame dashboard entry point.

Usage:
    python pygame_app/main.py                        # tick-based, 3 sims
    python pygame_app/main.py --sims 5 --realtime    # real-time mode
    python pygame_app/main.py --realtime --sim-speed 525600 --until-death
    python pygame_app/main.py --tts                  # + OmniVoice audio
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pygame

from pygame_app.game import Game
from pygame_app.renderer import Renderer, W as DEFAULT_W, H as DEFAULT_H


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Sims Engine — Dashboard")
    p.add_argument("--sims",        type=int,   default=4)
    p.add_argument("--backend",     default="ollama",
                   choices=["ollama", "llama-server", "llama-cpp"])
    p.add_argument("--ollama-model", default=None)
    p.add_argument("--no-datasets", action="store_true")
    p.add_argument("--tts",         action="store_true",
                   help="Enable OmniVoice TTS audio")
    p.add_argument("--realtime",    action="store_true",
                   help="Use RealtimeSimEngine (non-blocking update every frame)")
    p.add_argument("--sim-speed",   type=float, default=3_600.0,
                   help="Sim speed (sim-sec/real-sec). 3600=1hr/s, 525600=1yr/min")
    p.add_argument("--until-death", action="store_true",
                   help="Exit when all sims die (realtime mode)")
    p.add_argument("--width",  type=int, default=DEFAULT_W)
    p.add_argument("--height", type=int, default=DEFAULT_H)
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if args.ollama_model:
        os.environ["SIM_V2_OLLAMA_MODEL"] = args.ollama_model

    # ── LLM backend ──────────────────────────────────────────────────────────
    from llm.backend import create_backend
    llm = create_backend(args.backend)
    print(f"[INFO] LLM: {args.backend}")

    # ── Datasets ─────────────────────────────────────────────────────────────
    datasets, essays = None, []
    if not args.no_datasets:
        print("[INFO] Loading datasets...")
        from datasets.loader import load_all_datasets
        datasets = load_all_datasets()
        essays = datasets.okcupid_essays
        print(f"[INFO] Datasets ready ({len(datasets.social_norms)} norms)")

    # ── Sims + households ─────────────────────────────────────────────────────
    from identity.profile_factory import generate_sim_profile
    from core.sim import Sim
    from world.households import assign_households
    from persistence.sqlite import PersistenceLayer
    print(f"[INFO] Generating {args.sims} sims...")
    sims       = [Sim(generate_sim_profile(okcupid_essays=essays or None)) for _ in range(args.sims)]
    households = assign_households(sims)
    db         = PersistenceLayer()

    # ── Engine ────────────────────────────────────────────────────────────────
    from engine.engine import SimEngine
    engine = SimEngine(sims=sims, llm=llm, datasets=datasets, db=db)
    engine.households = households

    # ── Realtime wrapper ──────────────────────────────────────────────────────
    rt = None
    if args.realtime:
        from engine.realtime import RealtimeSimEngine
        rt = RealtimeSimEngine(engine, speed=args.sim_speed)
        speed_lbl = rt.clock.speed_label()
        yr_secs   = rt.clock.wall_seconds_per_sim_year()
        print(f"[INFO] Realtime mode — speed={speed_lbl}  (1 sim yr ≈ {yr_secs:.0f}s real)")

    # ── TTS ───────────────────────────────────────────────────────────────────
    tts = None
    if args.tts:
        from tts.engine import TTSEngine
        tts = TTSEngine(speed=1.0, save_audio=True)
        tts.assign_voices([s.name for s in sims])
        print("[INFO] TTS ready (OmniVoice)")

    # ── Pygame window ─────────────────────────────────────────────────────────
    pygame.init()
    pygame.display.set_caption("The Sims Engine")

    try:
        icon = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.circle(icon, (10, 15, 26), (16, 16), 16)
        pygame.draw.circle(icon, (55, 215, 120), (16, 16), 12, 3)
        pygame.draw.circle(icon, (80, 175, 245), (16, 16), 6)
        pygame.display.set_icon(icon)
    except Exception:
        pass

    surface  = pygame.display.set_mode((args.width, args.height))
    renderer = Renderer(surface)
    game     = Game(engine, tts=tts, rt=rt)

    mode_str = "REALTIME" if rt else "TICK"
    print(f"[INFO] Window {args.width}×{args.height} — {mode_str} mode")
    print("[INFO] Controls: SPACE=pause  +/-=speed  TAB=focus  N=tick(tick-mode)  ESC=quit\n")

    try:
        game.run(renderer)
        if args.until_death:
            while not engine.all_sims_dead:
                pygame.event.pump()
    finally:
        game.shutdown()
        pygame.quit()
        print("[INFO] Window closed.")


if __name__ == "__main__":
    main()
