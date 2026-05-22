"""
llm/timing.py — Lightweight trace timer for boot + tick + LLM call latency.

Usage
-----
# Wrap any LLMBackend:
from llm.timing import TimedBackend, store
llm = TimedBackend(raw_backend, name="llama-server/qwen3.5-0.8b-mtp")

# Time a named boot phase:
with store.phase("datasets"):
    datasets = load_all_datasets()

# Read stats:
GET /timings
"""
from __future__ import annotations

import time
import threading
from collections import deque
from contextlib import contextmanager
from typing import Any


class TimingStore:
    """Thread-safe accumulator for boot, tick, and LLM call timings."""

    def __init__(self, max_ticks: int = 100, max_llm: int = 500) -> None:
        self._lock = threading.Lock()
        # Boot
        self.boot_phases: dict[str, float] = {}
        self.boot_total_s: float | None = None
        self.boot_started_at: float = 0.0
        # Ticks
        self._ticks: deque[dict] = deque(maxlen=max_ticks)
        # LLM calls
        self._llm: deque[dict] = deque(maxlen=max_llm)

    # ── Boot phase tracking ────────────────────────────────────────────────────

    def start_boot(self) -> None:
        self.boot_started_at = time.monotonic()

    def record_phase(self, phase: str, elapsed: float) -> None:
        with self._lock:
            self.boot_phases[phase] = round(elapsed, 3)

    def finish_boot(self) -> float:
        elapsed = round(time.monotonic() - self.boot_started_at, 3)
        with self._lock:
            self.boot_total_s = elapsed
        return elapsed

    @contextmanager
    def phase(self, name: str):
        t0 = time.monotonic()
        yield
        self.record_phase(name, time.monotonic() - t0)

    # ── Tick tracking ──────────────────────────────────────────────────────────

    def record_tick(self, tick_num: int, elapsed: float) -> None:
        with self._lock:
            self._ticks.append({
                "tick": tick_num,
                "elapsed_s": round(elapsed, 3),
            })

    # ── LLM call tracking ─────────────────────────────────────────────────────

    def record_llm(
        self,
        backend: str,
        elapsed: float,
        prompt_chars: int = 0,
        prompt_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        with self._lock:
            self._llm.append({
                "backend": backend,
                "elapsed_s": round(elapsed, 3),
                "prompt_chars": prompt_chars,
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
            })

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        with self._lock:
            ticks = list(self._ticks)
            llm = list(self._llm)
            phases = dict(self.boot_phases)
            boot_total = self.boot_total_s

        def _stats(values: list[float]) -> dict:
            if not values:
                return {"count": 0, "avg_s": None, "min_s": None, "max_s": None}
            return {
                "count": len(values),
                "avg_s": round(sum(values) / len(values), 3),
                "min_s": round(min(values), 3),
                "max_s": round(max(values), 3),
            }

        tick_times = [t["elapsed_s"] for t in ticks]
        llm_times  = [c["elapsed_s"] for c in llm]

        return {
            "boot": {
                "phases_s": phases,
                "total_s": boot_total,
            },
            "ticks": {
                **_stats(tick_times),
                "recent": ticks[-10:],
            },
            "llm": {
                **_stats(llm_times),
                "recent": llm[-10:],
            },
        }

    # ── Pretty-print helpers ──────────────────────────────────────────────────

    def print_boot(self) -> None:
        phases_str = "  ".join(
            f"{k}={v:.2f}s" for k, v in self.boot_phases.items()
        )
        total = self.boot_total_s or 0.0
        print(f"[BOOT]  {phases_str}  →  total={total:.2f}s")

    def print_tick(self, tick_num: int, elapsed: float, llm_elapsed: float | None) -> None:
        llm_part = f"  llm={llm_elapsed:.2f}s" if llm_elapsed is not None else ""
        print(f"[Tick {tick_num:>4}]  tick={elapsed:.3f}s{llm_part}")


# Module-level singleton — import this everywhere
store = TimingStore()


class TimedBackend:
    """
    Drop-in wrapper around any LLMBackend.
    Records every chat() call duration to the global TimingStore.
    """

    def __init__(self, backend: Any, name: str = "llm") -> None:
        self._backend = backend
        self._name = name
        # Most-recent call duration — read by the tick endpoint
        self.last_elapsed: float | None = None
        self._call_lock = threading.Lock()

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.7,
        schema: dict | None = None,
    ) -> str:
        t0 = time.monotonic()
        result = self._backend.chat(system, user, max_tokens, temperature, schema)
        elapsed = time.monotonic() - t0
        prompt_tokens = getattr(self._backend, "_last_prompt_tokens", 0)
        output_tokens = getattr(self._backend, "_last_output_tokens", 0)
        with self._call_lock:
            self.last_elapsed = elapsed
        store.record_llm(
            self._name, elapsed,
            prompt_chars=len(system) + len(user),
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )
        return result

    # Transparent attribute pass-through for anything else the engine accesses
    def __getattr__(self, item: str):
        return getattr(self._backend, item)
