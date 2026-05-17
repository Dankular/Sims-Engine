"""
world/calendar.py — In-game calendar with named days and seasonal holidays.

The game calendar tracks:
  - Current day, month, year (derived from tick_count + TICKS_PER_YEAR)
  - 8 annual holidays that affect all sims simultaneously
  - Holiday effects: social/fun boost, special interactions unlock

GameCalendar.tick() fires holiday events and exposes date context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Holiday:
    name: str
    month: int          # 1-12
    day_fraction: float # 0.0..1.0 within the month (0.5 = mid-month)
    social_boost: float
    fun_boost: float
    mood: str           # emotion to give all sims
    special_interactions: list[str]
    description: str


HOLIDAYS: list[Holiday] = [
    Holiday("New Year's Day",     1,  0.0,  15, 10, "excitement",
            ["celebrate_holiday", "make_resolution"],
            "The world starts fresh. Sims feel hopeful and festive."),
    Holiday("Love Day",           2,  0.45, 10, 15, "love",
            ["give_gift", "express_love", "serenade"],
            "Romance is in the air. Single sims feel a touch of longing."),
    Holiday("Spring Festival",    4,  0.0,  12, 12, "optimism",
            ["celebrate_holiday", "share_holiday_meal", "dance_together"],
            "Flowers bloom. Sims feel renewed energy and social warmth."),
    Holiday("Summer Solstice",    6,  0.5,  10, 15, "excitement",
            ["celebrate_holiday", "outdoor_adventure", "share_story"],
            "The longest day. Sims head outdoors and socialise freely."),
    Holiday("Harvest Moon",       9,  0.5,  12, 10, "gratitude",
            ["share_holiday_meal", "express_gratitude", "celebrate_together"],
            "A time of thankfulness. Sims reflect on the year so far."),
    Holiday("Spooky Season",     10,  0.9,  8,  12, "surprise",
            ["share_scary_story", "celebrate_holiday", "joke"],
            "Mischief and mystery. Sims get a thrill from the uncanny."),
    Holiday("Winterfest",        12,  0.6,  15, 15, "joy",
            ["exchange_gifts", "share_holiday_meal", "celebrate_holiday", "carol"],
            "The warmest winter night. Families and friends gather."),
    Holiday("Winter Solstice",   12,  0.85, 10, 8,  "sentimental",
            ["reflect_on_year", "confide", "express_gratitude"],
            "The longest night. Sims turn inward and cherish connections."),
]


class GameCalendar:
    def __init__(self, ticks_per_year: int = 365) -> None:
        self._tpy          = ticks_per_year
        self._ticks_per_month = ticks_per_year / 12
        self._fired_holidays: set[str] = set()
        self._current_year  = 1

    # ── Public API ─────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        t = engine.tick_count
        year  = t // self._tpy + 1
        doy   = t % self._tpy    # day of year (0-indexed)

        if year != self._current_year:
            self._current_year = year
            self._fired_holidays.clear()   # reset for new year

        self._check_holidays(doy, engine)

    def date_dict(self, tick: int) -> dict:
        tpy         = self._tpy
        year        = tick // tpy + 1
        doy         = tick % tpy
        month_idx   = int(doy / (tpy / 12))
        month_idx   = min(month_idx, 11)
        month_name  = MONTHS[month_idx]
        day_of_week = DAYS_OF_WEEK[tick % 7]
        day_in_month= int((doy % (tpy / 12)) / (tpy / 12 / 28)) + 1

        upcoming    = self._upcoming_holiday(doy, tpy)

        return {
            "year": year,
            "month": month_name,
            "day": min(day_in_month, 28),
            "day_of_week": day_of_week,
            "upcoming_holiday": upcoming,
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _doy_for_holiday(self, h: Holiday) -> int:
        month_start = int((h.month - 1) / 12 * self._tpy)
        offset      = int(h.day_fraction * (self._tpy / 12))
        return month_start + offset

    def _check_holidays(self, doy: int, engine: "SimEngine") -> None:
        for h in HOLIDAYS:
            fire_doy = self._doy_for_holiday(h)
            key      = f"{self._current_year}:{h.name}"
            if abs(doy - fire_doy) <= 1 and key not in self._fired_holidays:
                self._fired_holidays.add(key)
                self._fire_holiday(h, engine)

    def _fire_holiday(self, h: Holiday, engine: "SimEngine") -> None:
        import logging
        logging.getLogger(__name__).info("[Calendar] Holiday: %s", h.name)

        for sim in engine.sims:
            sim.needs.restore("social", h.social_boost)
            sim.needs.restore("fun",    h.fun_boost)
            sim.emotion.add(h.mood, 0.7, duration=6, source=f"holiday:{h.name}")

            # Add unlocked interactions temporarily
            if not hasattr(sim, "_holiday_interactions"):
                sim._holiday_interactions = []
            sim._holiday_interactions = list(h.special_interactions)

        engine._bus.emit(
            "holiday",
            name=h.name,
            description=h.description,
            special_interactions=h.special_interactions,
            tick=engine.tick_count,
        )

    def _upcoming_holiday(self, doy: int, tpy: int) -> str | None:
        for h in sorted(HOLIDAYS, key=lambda hh: self._doy_for_holiday(hh)):
            hd = self._doy_for_holiday(h)
            if hd >= doy:
                days_away = hd - doy
                return f"{h.name} in ~{days_away} days"
        # Wrap to next year
        first = min(HOLIDAYS, key=lambda hh: self._doy_for_holiday(hh))
        return f"{first.name} next year"
