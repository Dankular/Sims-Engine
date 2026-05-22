"""
analytics/tracker.py — Per-tick data collection for post-run analytics.

Subscribes to the engine EventBus and snapshots state each tick.
All data stays in memory; call .serialise() at end of run to get a
JSON-safe dict, or pass directly to analytics/report.py.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

# Numeric emotion label for heatmap colouring
_EMOTION_VALENCE = {
    "joy": 0.9,
    "love": 0.95,
    "excitement": 0.85,
    "admiration": 0.8,
    "amusement": 0.75,
    "gratitude": 0.85,
    "optimism": 0.8,
    "pride": 0.7,
    "relief": 0.65,
    "approval": 0.6,
    "caring": 0.7,
    "curiosity": 0.55,
    "surprise": 0.5,
    "realization": 0.55,
    "desire": 0.6,
    "neutral": 0.5,
    "sadness": 0.2,
    "grief": 0.1,
    "disappointment": 0.15,
    "remorse": 0.2,
    "anger": 0.05,
    "annoyance": 0.15,
    "disgust": 0.1,
    "disapproval": 0.1,
    "embarrassment": 0.25,
    "fear": 0.1,
    "nervousness": 0.2,
    "confusion": 0.3,
}


class SimTracker:
    """Attach to an engine and record everything needed for the analytics report."""

    def __init__(self, engine: "SimEngine"):
        self._engine = engine

        # time-series data
        self.ticks: list[int] = []
        self.hours: list[int] = []
        self.venues: list[str] = []

        # per-sim per-tick  {sim_name: [value_per_tick]}
        self.emotions: dict[str, list[float]] = defaultdict(list)
        self.emotion_labels: dict[str, list[str]] = defaultdict(list)
        self.simoleons: dict[str, list[float]] = defaultdict(list)
        self.career_perf: dict[str, list[float]] = defaultdict(list)
        self.ocean_history: dict[str, list[dict]] = defaultdict(list)
        self.portfolio_history: dict[str, list[dict]] = defaultdict(list)

        # arc state per-sim per-tick  (0=none, 1=active, 2=burnout, 3=grief)
        self.arc_states: dict[str, list[int]] = defaultdict(list)

        # per-pair per-tick  {(a,b): [friendship, ...]}
        self.friendship: dict[tuple, list[float]] = defaultdict(list)
        self.romance: dict[tuple, list[float]] = defaultdict(list)

        # events
        self.interactions: list[dict] = []
        self.career_events: list[dict] = []
        self.life_events: list[dict] = []
        self.economy_events: list[dict] = []

        # baseline OCEAN (captured on first tick)
        self.ocean_baseline: dict[str, dict] = {}

        # subscribe
        engine._bus.on("interaction_resolved", self._on_interaction)
        engine._bus.on("career_event", self._on_career)
        engine._bus.on("life_event", self._on_life)
        engine._bus.on("economy.purchase", self._on_economy)
        engine._bus.on("economy.trade", self._on_economy)
        engine._bus.on("economy.rent_income", self._on_economy)
        engine._bus.on("economy.gift", self._on_economy)
        engine._bus.on("economy.contract_settlement", self._on_economy)
        engine._bus.on("economy.contract_breach", self._on_economy)

    # ── Tick snapshot ─────────────────────────────────────────────────────────

    def snapshot(self, tick: int) -> None:
        engine = self._engine
        from config import GAME_START_HOUR

        hour = (GAME_START_HOUR + tick) % 24

        self.ticks.append(tick)
        self.hours.append(hour)
        self.venues.append(engine._venue.get("name", ""))

        for sim in engine.sims:
            name = sim.name

            # baseline snapshot once
            if name not in self.ocean_baseline:
                self.ocean_baseline[name] = dict(sim.profile["ocean"])

            emo = sim.emotion.dominant
            self.emotion_labels[name].append(emo)
            self.emotions[name].append(_EMOTION_VALENCE.get(emo, 0.5))
            self.simoleons[name].append(round(sim.simoleons, 1))
            self.career_perf[name].append(round(sim.career_performance, 1))
            self.ocean_history[name].append(dict(sim.profile["ocean"]))
            self.portfolio_history[name].append(
                dict(
                    getattr(
                        sim,
                        "_portfolio_view",
                        {"net_worth": round(float(sim.simoleons), 2)},
                    )
                )
            )

            # arc state: grief=3, burnout=2, lonely=1, none=0
            arc = 0
            if getattr(sim, "_burnout_active", False):
                arc = 2
            if getattr(sim, "grief_stage", -1) >= 0:
                arc = 3
            from core.arcs import is_lonely

            if arc == 0 and is_lonely(sim):
                arc = 1
            self.arc_states[name].append(arc)

        # relationship snapshots
        seen: set[tuple] = set()
        for a in engine.sims:
            for b in engine.sims:
                if a is b:
                    continue
                key = (min(a.name, b.name), max(a.name, b.name))
                if key in seen:
                    continue
                seen.add(key)
                rec = engine.relationships.get(a.sim_id, b.sim_id)
                self.friendship[key].append(round(rec.friendship, 1))
                self.romance[key].append(round(rec.romance, 1))

    # ── Event callbacks ───────────────────────────────────────────────────────

    def _on_interaction(self, **kw) -> None:
        res = kw.get("result", {})
        self.interactions.append(
            {
                "tick": kw.get("tick", 0),
                "sim_a": kw["sim_a"].name,
                "sim_b": kw["sim_b"].name,
                "action": kw.get("interaction", ""),
                "valence": round(float(kw.get("valence", 0)), 3),
                "fd": round(float(res.get("friendship_delta", 0)), 2),
                "rd": round(float(res.get("romance_delta", 0)), 2),
                "emotion_a": res.get("emotion_a", ""),
                "emotion_b": res.get("emotion_b", ""),
                "memory": res.get("memory_tag", ""),
            }
        )

    def _on_career(self, **kw) -> None:
        res = kw.get("result", {})
        self.career_events.append(
            {
                "tick": kw.get("tick", 0),
                "sim": kw["sim"].name,
                "event": res.get("event_type", ""),
                "perf_d": float(res.get("performance_delta", 0)),
                "sim_d": float(res.get("simoleon_delta", 0)),
            }
        )

    def _on_life(self, **kw) -> None:
        res = kw.get("result", {})
        self.life_events.append(
            {
                "tick": kw.get("tick", 0),
                "sim": kw["sim_a"].name if kw.get("sim_a") else "?",
                "event": res.get("event_type", ""),
                "narrative": res.get("narrative", "")[:120],
            }
        )

    def _on_economy(self, **kw) -> None:
        evt = dict(kw)
        self.economy_events.append(evt)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def serialise(self) -> dict:
        return {
            "ticks": self.ticks,
            "hours": self.hours,
            "venues": self.venues,
            "emotions": dict(self.emotion_labels),
            "emotion_vals": {k: list(v) for k, v in self.emotions.items()},
            "simoleons": dict(self.simoleons),
            "career_perf": dict(self.career_perf),
            "ocean_baseline": self.ocean_baseline,
            "ocean_history": dict(self.ocean_history),
            "portfolio_history": dict(self.portfolio_history),
            "arc_states": dict(self.arc_states),
            "friendship": {str(k): list(v) for k, v in self.friendship.items()},
            "romance": {str(k): list(v) for k, v in self.romance.items()},
            "interactions": self.interactions,
            "career_events": self.career_events,
            "life_events": self.life_events,
            "economy_events": self.economy_events,
        }
