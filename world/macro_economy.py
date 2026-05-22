from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

__all__ = ["LaborMarket", "GoodsMarket", "EconomicCycle", "MacroEconomy"]


class LaborMarket:
    def __init__(self) -> None:
        self.employment_rate: float = 0.75
        self.wage_pressure: float = 0.0
        self._history: list[float] = []

    def tick(self, engine: "SimEngine") -> None:
        sims = engine.sims
        if not sims:
            return

        employed = sum(
            1 for s in sims if s.profile.get("job", "Unemployed") != "Unemployed"
        )
        self.employment_rate = employed / len(sims)

        raw = (self.employment_rate - 0.75) * 2.0
        self.wage_pressure = max(-1.0, min(1.0, raw))

        self._history.append(self.employment_rate)
        if len(self._history) > 20:
            self._history = self._history[-20:]

        if self.wage_pressure > 0.3:
            for sim in sims:
                if sim.profile.get("job", "Unemployed") != "Unemployed":
                    sim.career_performance = min(100.0, sim.career_performance + 0.5)

        elif self.wage_pressure < -0.3:
            unemployed = [
                s for s in sims if s.profile.get("job", "Unemployed") == "Unemployed"
            ]
            for sim in unemployed:
                if random.random() < 0.10 and hasattr(sim, "moodlets"):
                    sim.moodlets.add("stressed", source="labor_market_pressure")


class GoodsMarket:
    def __init__(self) -> None:
        self.price_index: float = 1.0
        self._consumption_ticks: int = 0
        self._sims_count: int = 0

    def tick(self, engine: "SimEngine") -> None:
        self._sims_count = len(engine.sims)
        if engine.tick_count % 10 != 0:
            return

        if not hasattr(engine, "_shop_visit_count"):
            engine._shop_visit_count = 0  # type: ignore[attr-defined]

        visits_this_window = engine._shop_visit_count - self._consumption_ticks
        self._consumption_ticks = engine._shop_visit_count

        per_capita = (
            visits_this_window / max(1, self._sims_count * 10)
        )

        if per_capita > 1.5:
            self.price_index = min(2.0, self.price_index * 1.01)
            logger.debug("[GoodsMarket] high consumption → price_index %.3f", self.price_index)
        elif per_capita < 0.5:
            self.price_index = max(0.5, self.price_index * 0.99)
            logger.debug("[GoodsMarket] low consumption → price_index %.3f", self.price_index)

    def get_adjusted_cost(self, base_cost: float) -> float:
        return base_cost * self.price_index


class EconomicCycle:
    PHASES = ["expansion", "peak", "contraction", "trough"]
    PHASE_DURATIONS: dict[str, int] = {
        "expansion": 40,
        "peak": 20,
        "contraction": 35,
        "trough": 15,
    }

    def __init__(self) -> None:
        self.phase: str = "expansion"
        self.phase_tick: int = 0
        self.aggregate_wealth: float = 0.0
        self._wealth_history: list[float] = []

    def tick(self, engine: "SimEngine") -> None:
        self.aggregate_wealth = sum(s.simoleons for s in engine.sims)
        self._wealth_history.append(self.aggregate_wealth)
        if len(self._wealth_history) > 30:
            self._wealth_history = self._wealth_history[-30:]

        self.phase_tick += 1
        if self.phase_tick >= self.PHASE_DURATIONS[self.phase]:
            self._advance_phase(engine)

        if self.phase in ("contraction", "trough"):
            if hasattr(engine, "stocks"):
                engine.stocks.on_event("recession", 1.5)
        elif self.phase in ("expansion", "peak"):
            if hasattr(engine, "stocks"):
                engine.stocks.on_event("boom", 1.2)

    def _advance_phase(self, engine: "SimEngine") -> None:
        idx = self.PHASES.index(self.phase)
        self.phase = self.PHASES[(idx + 1) % len(self.PHASES)]
        self.phase_tick = 0
        logger.info("[EconomicCycle] phase → %s", self.phase)
        engine._bus.emit(
            "economy_phase_change",
            phase=self.phase,
            tick=engine.tick_count,
        )

    def gini(self, engine: "SimEngine") -> float:
        wealth = sorted(s.simoleons for s in engine.sims)
        n = len(wealth)
        if n == 0:
            return 0.0
        total = sum(wealth)
        if total == 0.0:
            return 0.0
        # Trapezoidal Gini from Lorenz curve
        cumulative = 0.0
        lorenz_area = 0.0
        for i, w in enumerate(wealth):
            cumulative += w
            lorenz_area += cumulative / total
        gini_val = 1.0 - (2.0 * lorenz_area / n) + (1.0 / n)
        return round(max(0.0, min(1.0, gini_val)), 4)


class MacroEconomy:
    def __init__(self) -> None:
        self.labor = LaborMarket()
        self.goods = GoodsMarket()
        self.cycle = EconomicCycle()

    def tick(self, engine: "SimEngine") -> None:
        try:
            self.labor.tick(engine)
        except Exception as exc:
            logger.debug("[MacroEconomy] labor.tick error: %s", exc)
        try:
            self.goods.tick(engine)
        except Exception as exc:
            logger.debug("[MacroEconomy] goods.tick error: %s", exc)
        try:
            self.cycle.tick(engine)
        except Exception as exc:
            logger.debug("[MacroEconomy] cycle.tick error: %s", exc)

    def summary(self) -> dict:
        return {
            "employment_rate": round(self.labor.employment_rate, 4),
            "wage_pressure": round(self.labor.wage_pressure, 4),
            "price_index": round(self.goods.price_index, 4),
            "phase": self.cycle.phase,
            "phase_tick": self.cycle.phase_tick,
            "aggregate_wealth": round(self.cycle.aggregate_wealth, 2),
        }
