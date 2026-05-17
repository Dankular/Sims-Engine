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
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable or disable story narration + TTS after each tick (default: enabled)",
    )
    p.add_argument(
        "--narrate-every",
        type=int,
        default=1,
        help="Narrate every N ticks (default: 1)",
    )
    p.add_argument(
        "--tts-steps",
        type=int,
        default=32,
        help="OmniVoice diffusion steps — higher = slower but better (default: 32)",
    )
    p.add_argument(
        "--tts-speed", type=float, default=1.0, help="TTS speed 0.7-2.0 (default: 1.0)"
    )
    p.add_argument(
        "--tts-device",
        default="cpu",
        help="OmniVoice device: 'cpu' or 'cuda:0' (default: cpu)",
    )
    p.add_argument(
        "--no-audio-save", action="store_true", help="Don't save audio files to disk"
    )
    p.add_argument(
        "--narrator-voice-id",
        default=None,
        help="Narrator voice slot: M1-M5 or F1-F5 (default: F1)",
    )
    p.add_argument(
        "--list-voices",
        action="store_true",
        help="List available narrator voice slots and exit",
    )
    p.add_argument(
        "--export-story",
        action="store_true",
        help="Export story chapters as JSON to exports/ directory",
    )
    p.add_argument(
        "--export-chapter-size",
        type=int,
        default=10,
        help="Number of ticks per exported story chapter (default: 10)",
    )
    p.add_argument(
        "--until-death",
        action="store_true",
        help="Run until the last sim dies naturally (ignores --ticks)",
    )
    p.add_argument(
        "--ticks-per-year",
        type=int,
        default=None,
        help="Override ticks per in-game year (default: 50 from config)",
    )
    p.add_argument(
        "--realtime",
        action="store_true",
        help=(
            "Real-time game loop mode: non-blocking update() at target FPS, "
            "wall-clock timestamps, continuous aging. Use with --sim-speed."
        ),
    )
    p.add_argument(
        "--sim-speed",
        type=float,
        default=3_600.0,
        help=(
            "Sim seconds per real second (default: 3600 = 1 sim hr/real sec). "
            "3600: good for open-ended play (1 sim year ≈ 2.4 real hours). "
            "86400: 1 sim day/real sec, full life ≈ 7.6 real hours. "
            "525600: 1 sim year/real minute, full life ≈ 75 real minutes (best for --until-death)."
        ),
    )
    p.add_argument(
        "--fps",
        type=int,
        default=20,
        help="Target frame rate for realtime display updates (default: 20)",
    )
    p.add_argument(
        "--analytics",
        action="store_true",
        help="Track metrics and generate post-run analytics report (charts + summary JSON)",
    )
    p.add_argument(
        "--analytics-dir",
        default="reports",
        help="Output directory for analytics charts (default: reports/)",
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
    # ── Network / NATS ────────────────────────────────────────────────────────
    p.add_argument(
        "--nats",
        metavar="URL",
        default=None,
        help="NATS server URL to join the distributed world (e.g. nats://localhost:4222)",
    )
    p.add_argument(
        "--room",
        default="global",
        help=(
            "Room to join on startup: 'global' (default), "
            "'personal' (local only), or a custom friends room ID"
        ),
    )
    p.add_argument(
        "--client-id",
        default=None,
        metavar="ID",
        help="Stable client identity string (auto-generated UUID if omitted)",
    )
    return p


def _run_realtime(engine, args, tracker, exporter, story_runner) -> None:
    """Real-time game loop — non-blocking update() at target FPS."""
    from engine.realtime import RealtimeSimEngine
    from display import print_active_sims

    rt = RealtimeSimEngine(engine, speed=args.sim_speed)
    frame_time = 1.0 / max(1, args.fps)

    years_est = rt.clock.wall_seconds_per_sim_year()
    speed_lbl = rt.clock.speed_label()

    if _RICH:
        from rich.console import Console
        _con = Console()
        _con.print(
            f"\n[bold bright_cyan]REALTIME MODE[/]  "
            f"speed=[bold]{speed_lbl}[/]  "
            f"fps=[bold]{args.fps}[/]  "
            f"1 sim year ≈ [bold]{years_est:.0f}s[/] real\n"
        )
    else:
        print(f"\n[REALTIME] speed={speed_lbl}  fps={args.fps}  "
              f"1 sim year ≈ {years_est:.0f}s real\n")

    try:
        while not rt.all_sims_dead:
            t0 = time.monotonic()

            rt.update()

            # Display
            state = rt.get_state()
            _print_realtime_header(state)
            print_active_sims(engine)

            if tracker:
                tracker.snapshot(engine.tick_count)

            if story_runner:
                pass  # story_runner fires from EventBus automatically

            # Frame cap
            spent = time.monotonic() - t0
            remaining = frame_time - spent
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print("\n[Interrupted]")

    # Flush
    if story_runner:
        story_runner.flush()
    if exporter:
        exporter.flush_now()

    from display import print_summary
    print_summary(engine)

    if tracker:
        from analytics.report import generate
        print("\n[INFO] Generating analytics report...")
        report_dir = generate(tracker, output_dir=args.analytics_dir)
        print(f"[INFO] Report → {report_dir}\n")

    rt.shutdown()


def _print_realtime_header(state: dict) -> None:
    sim_lbl  = state.get("sim_label", "")
    spd_lbl  = state.get("speed_label", "")
    pending  = state.get("pending_interactions", 0)
    n_sims   = len(state.get("sims", []))
    pending_str = f"  [yellow]⏳ {pending} pending[/]" if pending else ""

    if _RICH:
        from rich.console import Console
        Console().rule(
            f"[bold bright_cyan]{sim_lbl}[/]"
            f"  [dim]{spd_lbl}[/]"
            f"  [dim]|[/]  [dim]{n_sims} sims alive[/]"
            f"{pending_str}"
        )
    else:
        print(f"\n  [{sim_lbl}]  speed={spd_lbl}  {n_sims} sims  "
              + (f"⏳{pending}" if pending else ""))


try:
    from rich.console import Console as _RichCheck
    _RICH = True
except ImportError:
    _RICH = False


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

    # ── NATS distributed network (optional) ──────────────────────────────────
    if args.nats:
        import uuid as _uuid
        from engine.network import NATSNetwork
        from engine.rooms import GLOBAL_ROOM, personal_room, room_label

        client_id = args.client_id or _uuid.uuid4().hex
        room_id   = (
            personal_room(client_id) if args.room == "personal"
            else (args.room or GLOBAL_ROOM)
        )
        print(f"[INFO] Connecting to NATS: {args.nats}")
        print(f"[INFO]   client-id : {client_id[:16]}…")
        print(f"[INFO]   room      : {room_label(room_id)}\n")
        _network = NATSNetwork(
            url=args.nats,
            client_id=client_id,
            owned_sim_ids={s.sim_id for s in sims},
            starting_room=room_id,
        )
        engine.attach_network(_network, room_id)

    # Story mode — TTS narration
    if args.story:
        from tts.engine import TTSEngine
        from narrative.story_runner import attach as attach_story

        tts = TTSEngine(
            num_steps=args.tts_steps,
            speed=args.tts_speed,
            save_audio=not args.no_audio_save,
            narrator_voice=args.narrator_voice_id,
            device=args.tts_device,
        )
        tts.assign_voices([s.name for s in sims])
        _story_runner = attach_story(engine, tts, llm, narrate_every=args.narrate_every)
        print(
            f"[INFO] Story mode ON — TTS narration every {args.narrate_every} tick(s)\n"
        )
    else:
        _story_runner = None

    # Narrative export layer (Gap 1)
    if args.export_story:
        from narrative.exporter import StoryExporter

        _exporter = StoryExporter(engine, chapter_size=args.export_chapter_size)
        print(
            f"[INFO] Story export ON — chapters every {args.export_chapter_size} ticks → exports/\n"
        )
    else:
        _exporter = None

    # Analytics tracker
    _tracker = None
    if args.analytics:
        from analytics.tracker import SimTracker
        _tracker = SimTracker(engine)
        print(f"[INFO] Analytics tracking ON → {args.analytics_dir}/\n")

    # Ticks-per-year override
    if args.ticks_per_year:
        import config as _cfg
        _cfg.TICKS_PER_YEAR = args.ticks_per_year
        print(f"[INFO] Ticks per year: {args.ticks_per_year}\n")

    # ── Real-time game loop mode ──────────────────────────────────────────────
    if args.realtime:
        _run_realtime(engine, args, _tracker, _exporter, _story_runner)
        return

    # ── Tick-based run loop ────────────────────────────────────────────────────
    if args.until_death:
        oldest_age = max(s.profile.get("age", 25) for s in sims)
        from config import TICKS_PER_YEAR as TPY
        from core.life_stage import DEATH_AGE_MAX
        est_ticks = (DEATH_AGE_MAX - oldest_age) * TPY
        print(
            f"\n[INFO] Running until last sim dies  "
            f"(~{est_ticks:,} ticks estimated for oldest sim)\n"
        )
    else:
        print(f"\n[INFO] Starting simulation — {args.ticks} ticks\n")

    def _should_continue(tick_num: int) -> bool:
        if args.until_death:
            return not engine.all_sims_dead
        return tick_num < args.ticks

    try:
        tick_num = 0
        while _should_continue(tick_num):
            print_tick_header(engine)
            engine.run_tick()
            print_active_sims(engine)
            if _tracker:
                _tracker.snapshot(engine.tick_count)
            tick_num += 1
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")

    if args.until_death and engine.all_sims_dead:
        print("\n[INFO] All sims have died. Simulation complete.")

    # Drain pending LLM calls before summary
    if engine._pending:
        print(f"\n[INFO] Draining {len(engine._pending)} pending adjudication(s)...")
        engine.flush_pending()

    # Narrate any events that resolved during the drain
    if _story_runner:
        _story_runner.flush()

    # Flush story export
    if _exporter:
        _exporter.flush_now()

    print_summary(engine)

    # Generate analytics report
    if _tracker:
        from analytics.report import generate
        print("\n[INFO] Generating analytics report...")
        report_dir = generate(_tracker, output_dir=args.analytics_dir)
        print(f"[INFO] Report saved → {report_dir}\n")

    engine.shutdown()
    print("\n[INFO] Done.\n")


if __name__ == "__main__":
    main()
