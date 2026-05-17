"""
engine/clock.py — Sim clock mapping wall time to in-game time.

The sim clock maintains a continuous relationship between real wall-clock
seconds and compressed sim time, supporting real-time game loops.

Speed reference (sim_speed = sim_seconds per real_second):
  1          → 1:1 real time   (1 real hour = 1 sim hour)
  3_600      → 1 sim hr/sec    (1 sim year ≈ 2.4 real hours, full life ≈ 7.6 real days)
  86_400     → 1 sim day/sec   (1 sim year ≈ 6 real minutes, full life ≈ 7.6 real hours)
  525_600    → 1 sim yr/min    (full life ≈ 75 real minutes)  ← good for --until-death
  31_557_600 → 1 sim yr/sec   (full life ≈ 75 real seconds)  ← stress test
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

# Default: 1 sim hour per real second (balance of visibility and speed)
DEFAULT_SIM_SPEED: float = 3_600.0

# Seconds in one sim year
_SIM_SECONDS_PER_YEAR: float = 365.25 * 24 * 3_600


class SimClock:
    """
    Maps monotonic wall time to sim time at a configurable compression ratio.
    Thread-safe reads; call set_speed() to change rate without discontinuity.
    """

    def __init__(
        self,
        speed: float = DEFAULT_SIM_SPEED,
        start_date: datetime | None = None,
    ):
        self.speed: float = speed
        self._wall_anchor: float = time.monotonic()
        self._sim_anchor: datetime = start_date or datetime(2025, 1, 1, 8, 0, 0)

    # ── Core properties ───────────────────────────────────────────────────────

    @property
    def sim_now(self) -> datetime:
        """Current in-game datetime."""
        return self._sim_anchor + timedelta(
            seconds=(time.monotonic() - self._wall_anchor) * self.speed
        )

    @property
    def wall_now(self) -> float:
        """Current wall clock (monotonic seconds)."""
        return time.monotonic()

    @property
    def elapsed_sim_years(self) -> float:
        """Sim years elapsed since engine start."""
        elapsed_sim_s = (time.monotonic() - self._wall_anchor) * self.speed
        return elapsed_sim_s / _SIM_SECONDS_PER_YEAR

    def elapsed_sim_hours_since(self, wall_ref: float) -> float:
        """Sim hours elapsed since a wall-clock reference point."""
        return (time.monotonic() - wall_ref) * self.speed / 3_600.0

    # ── Speed control ─────────────────────────────────────────────────────────

    def set_speed(self, new_speed: float) -> None:
        """Change compression ratio without discontinuity in sim time."""
        current_sim = self.sim_now
        self._wall_anchor = time.monotonic()
        self._sim_anchor = current_sim
        self.speed = new_speed

    # ── Utility ───────────────────────────────────────────────────────────────

    def wall_seconds_per_sim_year(self) -> float:
        return _SIM_SECONDS_PER_YEAR / self.speed

    def label(self) -> str:
        dt = self.sim_now
        return dt.strftime("%d %b %Y  %H:%M")

    def speed_label(self) -> str:
        s = self.speed
        if s < 120:
            return f"{s:.0f}×"
        if s < 7_200:
            return f"{s/60:.0f} min/s"
        if s < 172_800:
            return f"{s/3600:.0f} hr/s"
        return f"{s/86400:.1f} day/s"
