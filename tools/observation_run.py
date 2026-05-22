"""
tools/observation_run.py — Headless simulation runner for behavioral observation.

Runs the engine for N ticks with a configurable number of sims, attaches the
InteractionObserver, and saves a structured JSONL log.

Usage
-----
  # Fast mock run — no LLM required, good for catalog/pattern analysis
  python tools/observation_run.py --sims 8 --ticks 300 --mock

  # Real LLM run — requires llama-server on port 8080
  python tools/observation_run.py --sims 5 --ticks 100

  # Controlled seed for reproducible runs
  python tools/observation_run.py --sims 6 --ticks 200 --mock --seed 42

  # Custom output path
  python tools/observation_run.py --sims 8 --ticks 300 --mock --out reports/run_v2.jsonl

Output
------
  reports/run_<timestamp>.jsonl   — one JSON record per resolved interaction
  Stdout summary after completion.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_engine(n_sims: int, mock: bool):
    from datasets.loader import DatasetRegistry
    from identity.profile_factory import generate_sim_profile
    from core.sim import Sim
    from engine.engine import SimEngine

    print(f"  Loading datasets...", end=" ", flush=True)
    t0 = time.time()
    datasets = DatasetRegistry.load(workers=4)
    print(f"done ({time.time()-t0:.1f}s)")

    print(f"  Generating {n_sims} sim profiles...", end=" ", flush=True)
    essays = datasets.okcupid_essays or []
    sims = [Sim(generate_sim_profile(okcupid_essays=essays or None)) for _ in range(n_sims)]
    print("done")

    if mock:
        from llm.mock_backend import MockLLMBackend
        backend = MockLLMBackend()
        print("  LLM: MockLLMBackend")
    else:
        from llm.backend import LlamaServerBackend
        backend = LlamaServerBackend()
        print("  LLM: LlamaServerBackend")

    engine = SimEngine(sims=sims, llm=backend, datasets=datasets)
    return engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless observation run")
    parser.add_argument("--sims",  type=int, default=6,    help="Number of sims")
    parser.add_argument("--ticks", type=int, default=200,  help="Number of ticks to run")
    parser.add_argument("--mock",  action="store_true",    help="Use mock LLM (fast, no server required)")
    parser.add_argument("--seed",  type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--out",   type=str, default=None, help="Output JSONL path")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        print(f"Random seed: {args.seed}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out or f"reports/run_{timestamp}.jsonl"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"\nObservation run: {args.sims} sims x {args.ticks} ticks -> {out_path}")
    print("-" * 60)

    engine = _make_engine(args.sims, args.mock)

    from engine.observer import InteractionObserver
    observer = InteractionObserver(out_path)
    observer.attach(engine)

    print(f"\nRunning {args.ticks} ticks...", flush=True)
    t_start = time.time()
    interval = max(1, args.ticks // 20)  # progress every 5%

    for tick in range(args.ticks):
        engine.run_tick()
        if (tick + 1) % interval == 0:
            elapsed = time.time() - t_start
            rate = (tick + 1) / elapsed
            eta = (args.ticks - tick - 1) / rate if rate > 0 else 0
            print(
                f"  tick {tick+1:4d}/{args.ticks}  "
                f"interactions={observer._count:4d}  "
                f"{rate:.1f} ticks/s  ETA {eta:.0f}s",
                flush=True,
            )

    n_records = observer.close()
    elapsed = time.time() - t_start

    # -- Summary ---------------------------------------------------------------
    print(f"\n{'-'*60}")
    print(f"Completed {args.ticks} ticks in {elapsed:.1f}s")
    print(f"Interactions recorded: {n_records}")
    print(f"Output: {out_path}")
    print(f"\nNext step:  python tools/mine_patterns.py {out_path}")


if __name__ == "__main__":
    main()
