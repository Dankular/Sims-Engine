"""
sim_v2 — standalone simulation runner.

Usage:
    python -m sim_v2                        # 3 sims, 10 ticks, llama-cpp backend
    python -m sim_v2 --sims 5              # 5 sims
    python -m sim_v2 --ticks 20            # 20 ticks
    python -m sim_v2 --profile             # print one profile as JSON and exit
    python -m sim_v2 --backend ollama      # use Ollama HTTP backend
    python -m sim_v2 --backend llama-server --llama-url http://127.0.0.1:8080/v1/chat/completions
    python -m sim_v2 --dry-run             # 2 sims, 1 tick, no delay (smoke test)
    python -m sim_v2 --update              # clear dataset cache and re-download
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m sim_v2",
        description="AI Sims Engine v2 — local LLM-powered life simulation",
    )
    p.add_argument("--sims", type=int, default=3, help="Number of sims (default: 3)")
    p.add_argument("--ticks", type=int, default=10, help="Ticks to run (default: 10)")
    p.add_argument(
        "--profile", action="store_true", help="Print one profile as JSON and exit"
    )
    p.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between ticks (default: 0.5)"
    )
    p.add_argument("--dry-run", action="store_true", help="2 sims, 1 tick, no delay")
    p.add_argument(
        "--update", action="store_true", help="Clear dataset cache and re-download"
    )
    p.add_argument(
        "--backend",
        default="ollama",
        choices=["llama-cpp", "ollama", "llama-server"],
        help="LLM backend (default: ollama)",
    )
    p.add_argument("--ollama-model", default=None, help="Ollama model name")
    p.add_argument("--ollama-url", default=None, help="Ollama API URL")
    p.add_argument("--llama-url", default=None, help="llama-server OpenAI-compat URL")
    p.add_argument("--llama-model", default=None, help="Model id sent to llama-server")
    p.add_argument(
        "--llm-timeout",
        type=int,
        default=240,
        help="LLM request timeout (default: 240)",
    )
    p.add_argument("--no-datasets", action="store_true", help="Skip dataset loading")
    p.add_argument(
        "--story",
        action="store_true",
        help="Enable story narration + TTS after each tick",
    )
    p.add_argument(
        "--narrate-every",
        type=int,
        default=1,
        help="Narrate every N ticks (default: 1)",
    )
    p.add_argument(
        "--tts-quality", type=int, default=8, help="TTS quality steps 5-12 (default: 8)"
    )
    p.add_argument(
        "--tts-speed", type=float, default=1.0, help="TTS speed 0.7-2.0 (default: 1.0)"
    )
    p.add_argument(
        "--no-audio-save", action="store_true", help="Don't save audio files to disk"
    )
    p.add_argument(
        "--narrator-voice-id",
        default=None,
        help="Narrator voice id/name (e.g. from el_voices.json)",
    )
    p.add_argument(
        "--list-voices",
        action="store_true",
        help="List available narrator voices from el_voices.json and exit",
    )
    p.add_argument(
        "--voice-language",
        default=None,
        help="Optional language filter for --list-voices (e.g. en)",
    )
    p.add_argument(
        "--voice-category",
        default=None,
        help="Optional category filter for --list-voices (e.g. professional)",
    )
    p.add_argument(
        "--voice-limit",
        type=int,
        default=25,
        help="Max rows printed by --list-voices (default: 25)",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if args.list_voices:
        from tts.voice_catalog import load_voice_catalog, list_voices

        voices = load_voice_catalog()
        if not voices:
            print("[INFO] No el_voices.json found in project root.")
            print(
                "[INFO] Download it first:"
                ' curl -L "https://huggingface.co/spaces/Daankular/omnivoices-11labs/resolve/main/el_voices.json" -o "el_voices.json"'
            )
            return
        rows = list_voices(
            voices,
            language=args.voice_language,
            category=args.voice_category,
            limit=max(1, args.voice_limit),
        )
        print(
            f"[INFO] Voices: showing {len(rows)} / {len(voices)}"
            f" (language={args.voice_language or 'any'}, category={args.voice_category or 'any'})"
        )
        for i, v in enumerate(rows, 1):
            print(
                f"{i:>2}. id={v.get('id', '')} | name={v.get('name', '')}"
                f" | lang={v.get('language', '')} | category={v.get('category', '')}"
            )
        return

    if args.dry_run:
        args.sims = 2
        args.ticks = 1
        args.delay = 0.0
        print("[INFO] Dry-run mode: sims=2 ticks=1 delay=0\n")

    # Dataset cache management
    if args.update:
        from datasets.cache import clear_dataset_cache

        clear_dataset_cache()
        print("[INFO] Dataset cache cleared.\n")

    # Profile-only mode
    if args.profile:
        from identity.profile_factory import generate_sim_profile

        print(json.dumps(generate_sim_profile(), indent=2, ensure_ascii=False))
        return

    # Apply backend env overrides before creating the backend
    if args.ollama_model:
        os.environ["SIM_V2_OLLAMA_MODEL"] = args.ollama_model
    if args.ollama_url:
        os.environ["SIM_V2_OLLAMA_URL"] = args.ollama_url
    if args.llama_url:
        os.environ["SIM_V2_LLAMA_SERVER_URL"] = args.llama_url
    if args.llama_model:
        os.environ["SIM_V2_LLAMA_SERVER_MODEL"] = args.llama_model
    if args.llm_timeout:
        os.environ.setdefault("SIM_V2_OLLAMA_TIMEOUT", str(args.llm_timeout))
        os.environ.setdefault("SIM_V2_LLAMA_SERVER_TIMEOUT", str(args.llm_timeout))
    # Adult datasets always loaded; age-gated at interaction selection (sim.age >= 16)

    # llama-cpp downloads the GGUF at construction time — skip for dry-run
    if args.dry_run and args.backend == "llama-cpp":
        args.backend = "ollama"
        print("[INFO] dry-run: switched backend to ollama to skip GGUF download\n")

    from llm.backend import create_backend

    print(f"[INFO] LLM backend: {args.backend}")
    llm = create_backend(args.backend)

    # Load datasets
    datasets = None
    essays: list[str] = []
    if not args.no_datasets:
        print("[INFO] Loading datasets...")
        from datasets.loader import load_all_datasets

        datasets = load_all_datasets()
        essays = datasets.okcupid_essays
        print(
            f"[INFO] Datasets ready — {len(datasets.social_norms)} social norms, "
            f"{len(essays)} OkCupid essays, "
            f"{len(datasets.atomic_index)} ATOMIC keywords\n"
        )
        print("[INFO] Adult content age-gated at sim.age >= 16 (no flag required).\n")

    # Generate sims
    from identity.profile_factory import generate_sim_profile
    from core.sim import Sim
    from display import (
        print_sim_profile,
        print_tick_header,
        print_active_sims,
        print_summary,
        attach,
    )

    print(f"[INFO] Generating {args.sims} sim profiles...\n")
    sims: list[Sim] = []
    for _ in range(args.sims):
        profile = generate_sim_profile(okcupid_essays=essays or None)
        sim = Sim(profile)
        sims.append(sim)
        print_sim_profile(sim)

    # Assign households
    from world.households import assign_households

    households = assign_households(sims)
    print(f"\n[INFO] {len(households)} household(s) created.")

    # Persistence layer
    from persistence.sqlite import PersistenceLayer

    db = PersistenceLayer()

    # Build and wire engine
    from engine.engine import SimEngine

    engine = SimEngine(sims=sims, llm=llm, datasets=datasets, db=db)
    engine.households = households
    attach(engine)

    # Story mode — TTS narration
    if args.story:
        from tts.engine import TTSEngine
        from narrative.story_runner import attach as attach_story

        tts = TTSEngine(
            quality=args.tts_quality,
            speed=args.tts_speed,
            save_audio=not args.no_audio_save,
            narrator_voice=args.narrator_voice_id,
        )
        tts.assign_voices([s.name for s in sims])
        _story_runner = attach_story(engine, tts, llm, narrate_every=args.narrate_every)
        print(
            f"[INFO] Story mode ON — TTS narration every {args.narrate_every} tick(s)\n"
        )
    else:
        _story_runner = None

    print(f"\n[INFO] Starting simulation — {args.ticks} ticks\n")
    try:
        for _ in range(args.ticks):
            print_tick_header(engine)
            engine.run_tick()
            print_active_sims(engine)
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")

    # Drain pending LLM calls before summary
    if engine._pending:
        print(f"\n[INFO] Draining {len(engine._pending)} pending adjudication(s)...")
        engine.flush_pending()

    # Narrate any events that resolved during the drain
    if _story_runner:
        _story_runner.flush()

    print_summary(engine)
    engine.shutdown()
    print("\n[INFO] Done.\n")


if __name__ == "__main__":
    main()
