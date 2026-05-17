"""
world/weather.py — Weather and temperature system.

Weather cycles based on an in-game seasonal calendar. Each state has
temperature ranges and effect multipliers on sim needs and mood.

WeatherSystem.tick() updates weather state and applies sim effects.
Weather is injected into the adjudicator context prompt.
Exposed in get_state() as current_weather.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

# ── Weather states ─────────────────────────────────────────────────────────────

@dataclass
class WeatherState:
    name: str
    temperature: float          # Celsius
    hygiene_modifier: float     # per tick (negative = gets dirty in rain)
    energy_modifier: float      # per tick (cold drains energy)
    fun_modifier: float         # sunshine boosts fun
    mood_label: str             # injected into adjudicator prompt
    danger: bool = False        # True = hypothermia/heatstroke risk


WEATHER_STATES: dict[str, WeatherState] = {
    "sunny":   WeatherState("sunny",   24.0,  0.0,  0.0,  2.0, "bright and cheerful"),
    "cloudy":  WeatherState("cloudy",  16.0,  0.0, -0.5,  0.0, "overcast and neutral"),
    "rainy":   WeatherState("rainy",   12.0, -2.0, -1.0, -1.0, "damp and introspective"),
    "stormy":  WeatherState("stormy",   9.0, -3.0, -2.0, -2.0, "tense and dramatic",     danger=False),
    "snowy":   WeatherState("snowy",    0.0, -1.0, -2.5, -0.5, "cold and cosy",          danger=True),
    "foggy":   WeatherState("foggy",   10.0,  0.0, -0.5, -0.5, "mysterious and quiet"),
    "heatwave":WeatherState("heatwave",38.0,  0.0, -1.5, -1.0, "sweltering and irritable", danger=True),
}

# Month index (0-11) → weather probability table
# Keys are weather state names, values are relative weights
_SEASONAL_WEIGHTS: dict[int, dict[str, float]] = {
    0:  {"snowy": 3, "cloudy": 3, "rainy": 2, "sunny": 1, "foggy": 1},            # January
    1:  {"snowy": 2, "cloudy": 3, "rainy": 2, "sunny": 2, "foggy": 1},            # February
    2:  {"rainy": 3, "cloudy": 2, "sunny": 3, "foggy": 2, "snowy": 1},            # March
    3:  {"sunny": 3, "cloudy": 2, "rainy": 3, "foggy": 1},                        # April
    4:  {"sunny": 4, "cloudy": 2, "rainy": 2, "stormy": 1},                       # May
    5:  {"sunny": 5, "heatwave": 2, "cloudy": 2, "stormy": 1},                    # June
    6:  {"sunny": 5, "heatwave": 3, "cloudy": 1, "stormy": 1},                    # July
    7:  {"sunny": 4, "heatwave": 2, "cloudy": 2, "stormy": 2},                    # August
    8:  {"sunny": 3, "cloudy": 3, "rainy": 2, "foggy": 2},                        # September
    9:  {"cloudy": 3, "rainy": 3, "foggy": 2, "sunny": 2},                        # October
    10: {"rainy": 3, "cloudy": 3, "foggy": 2, "snowy": 2},                        # November
    11: {"snowy": 4, "cloudy": 3, "foggy": 2, "rainy": 1},                        # December
}

WEATHER_CHANGE_INTERVAL = 8    # ticks between weather updates
DANGER_EXPOSURE_TICKS   = 3    # ticks in dangerous weather before health risk
TEMPERATURE_EFFECT_SCALE = 0.3  # dampens temperature-driven need modifiers


class WeatherSystem:
    def __init__(self) -> None:
        self.current: WeatherState = WEATHER_STATES["sunny"]
        self._danger_exposure: dict[str, int] = {}  # sim_id → ticks in danger
        self._ticks_since_change = 0

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        self._ticks_since_change += 1
        if self._ticks_since_change >= WEATHER_CHANGE_INTERVAL:
            self._maybe_change_weather(engine)
            self._ticks_since_change = 0

        self._apply_effects(engine)

    def _maybe_change_weather(self, engine: "SimEngine") -> None:
        # Determine current month from tick count
        ticks_per_year = getattr(engine, "_ticks_per_year", 365)
        month = int((engine.tick_count % ticks_per_year) / (ticks_per_year / 12)) % 12
        weights = _SEASONAL_WEIGHTS.get(month, _SEASONAL_WEIGHTS[6])
        states  = list(weights.keys())
        wts     = [weights[s] for s in states]
        chosen  = random.choices(states, weights=wts, k=1)[0]
        if chosen != self.current.name:
            self.current = WEATHER_STATES[chosen]
            engine._bus.emit(
                "weather_changed",
                weather=self.current.name,
                temperature=self.current.temperature,
                tick=engine.tick_count,
            )
            import logging
            logging.getLogger(__name__).info(
                "[Weather] → %s (%.0f°C) — %s",
                self.current.name, self.current.temperature, self.current.mood_label,
            )

    def _apply_effects(self, engine: "SimEngine") -> None:
        w = self.current
        for sim in engine.sims:
            if getattr(sim, "_sleeping", False):
                continue

            # Need modifiers
            if w.hygiene_modifier != 0:
                sim.needs.hygiene = max(0, min(100, sim.needs.hygiene + w.hygiene_modifier))
            if w.energy_modifier != 0:
                sim.needs.energy  = max(0, min(100, sim.needs.energy  + w.energy_modifier * TEMPERATURE_EFFECT_SCALE))
            if w.fun_modifier > 0:
                sim.needs.fun = max(0, min(100, sim.needs.fun + w.fun_modifier * 0.5))

            # Sunshine mood lift
            if w.name == "sunny":
                sim.emotion.add("optimism", 0.2, duration=2, source="sunshine")
            elif w.name in ("stormy", "snowy"):
                sim.emotion.add("apprehensive", 0.2, duration=2, source=f"weather:{w.name}")

            # Danger exposure tracking
            if w.danger:
                self._danger_exposure[sim.sim_id] = (
                    self._danger_exposure.get(sim.sim_id, 0) + 1
                )
                if self._danger_exposure[sim.sim_id] >= DANGER_EXPOSURE_TICKS:
                    self._apply_danger(sim, w, engine)
                    self._danger_exposure[sim.sim_id] = 0
            else:
                self._danger_exposure.pop(sim.sim_id, None)

    def _apply_danger(self, sim: "Sim", w: WeatherState, engine: "SimEngine") -> None:
        if w.name == "snowy":
            sim.needs.energy = max(0, sim.needs.energy - 8)
            sim.emotion.add("fear", 0.4, duration=3, source="hypothermia_risk")
        elif w.name == "heatwave":
            sim.needs.hygiene = max(0, sim.needs.hygiene - 10)
            sim.needs.energy  = max(0, sim.needs.energy  - 5)
            sim.emotion.add("discomfort", 0.5, duration=3, source="heatstroke_risk")

    # ── Context injection ─────────────────────────────────────────────────────

    def context_line(self) -> str:
        """One-liner for the adjudicator user prompt."""
        w = self.current
        return (
            f"Current weather: {w.name} ({w.temperature:.0f}°C) — {w.mood_label}."
        )

    def state_dict(self) -> dict:
        w = self.current
        return {
            "name": w.name,
            "temperature": w.temperature,
            "mood_label": w.mood_label,
            "danger": w.danger,
        }
