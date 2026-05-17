"""
world/property.py — Property ownership and real estate.

Sims can purchase properties using accumulated simoleons.
Owned properties generate passive income and appreciate in value over time.
Owning property boosts reputation_score.

PropertyManager.tick() collects rent, appreciates values, and checks mortgage
payments. Expose in get_state() as property_portfolio per sim.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine


@dataclass
class Property:
    property_id: str
    prop_type: str          # "apartment", "house", "vacation_home", "business_lot"
    name: str
    owner_id: str
    purchase_price: float
    current_value: float
    income_per_tick: float  # rental / business income
    mortgage_per_tick: float = 0.0   # if purchased with mortgage
    appreciation_rate: float = 0.0005  # per tick value growth
    ticks_owned: int = 0


# ── Property catalogue ────────────────────────────────────────────────────────

PROPERTY_CATALOGUE = [
    # (type, name, price, income_per_tick, mortgage_pct_if_loan)
    ("apartment",     "Studio Apartment",      4_000,   8.0,  0.006),
    ("apartment",     "City Apartment",        8_000,  15.0,  0.005),
    ("house",         "Starter Home",         15_000,  25.0,  0.004),
    ("house",         "Family House",         28_000,  40.0,  0.004),
    ("vacation_home", "Holiday Cottage",      20_000,  30.0,  0.004),
    ("business_lot",  "Retail Space",         35_000,  55.0,  0.003),
    ("business_lot",  "Restaurant Unit",      50_000,  80.0,  0.003),
]

MORTGAGE_CHANCE   = 0.50    # chance that purchase is mortgaged rather than cash
PURCHASE_COOLDOWN = 25      # ticks between property purchases per sim
BUY_THRESHOLD     = 0.60    # sim must have this fraction of purchase price in simoleons


class PropertyManager:
    def __init__(self) -> None:
        self._properties: dict[str, Property] = {}   # property_id → Property
        self._last_buy: dict[str, int] = {}           # sim_id → tick

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        tick = engine.tick_count
        self._collect_income(engine)
        self._appreciate(tick)
        self._try_purchase(engine)

    def _collect_income(self, engine: "SimEngine") -> None:
        for prop in self._properties.values():
            owner = engine._sim_lookup.get(prop.owner_id)
            if owner is None:
                continue
            prop.ticks_owned += 1
            owner.simoleons  += prop.income_per_tick
            owner.simoleons  -= prop.mortgage_per_tick
            if owner.simoleons < 0:
                owner.simoleons = 0
                owner.emotion.add("nervousness", 0.5, duration=4, source="mortgage_overdue")

    def _appreciate(self, tick: int) -> None:
        for prop in self._properties.values():
            prop.current_value *= (1 + prop.appreciation_rate)

    def _try_purchase(self, engine: "SimEngine") -> None:
        tick = engine.tick_count
        for sim in engine.sims:
            # Only sims with Fortune or Family aspiration actively seek property
            if sim.profile.get("aspiration") not in ("Fortune", "Family"):
                if random.random() > 0.05:
                    continue

            last = self._last_buy.get(sim.sim_id, -PURCHASE_COOLDOWN)
            if tick - last < PURCHASE_COOLDOWN:
                continue
            if random.random() > 0.03:   # 3% chance per eligible tick
                continue

            self._try_buy(sim, engine, tick)

    def _try_buy(self, sim: "Sim", engine: "SimEngine", tick: int) -> None:
        # Find affordable properties
        owned_types = {self._properties[p].prop_type
                       for p in self._owned_by(sim.sim_id)}
        candidates = [
            row for row in PROPERTY_CATALOGUE
            if sim.simoleons >= row[2] * BUY_THRESHOLD
        ]
        if not candidates:
            return

        row = random.choice(candidates)
        prop_type, name, price, income, mort_pct = row
        use_mortgage = random.random() < MORTGAGE_CHANCE and sim.simoleons < price

        if not use_mortgage and sim.simoleons < price:
            return

        # Create property
        prop = Property(
            property_id      = uuid.uuid4().hex[:8],
            prop_type        = prop_type,
            name             = name,
            owner_id         = sim.sim_id,
            purchase_price   = float(price),
            current_value    = float(price),
            income_per_tick  = income,
            mortgage_per_tick= price * mort_pct if use_mortgage else 0.0,
        )
        self._properties[prop.property_id] = prop

        if not use_mortgage:
            sim.simoleons -= price

        # Add to sim's property list
        if not hasattr(sim, "properties") or sim.properties is None:
            sim.properties = []
        if prop.property_id not in sim.properties:
            sim.properties.append(prop.property_id)

        # Reputation boost for property ownership
        sim.reputation_score = min(100, sim.reputation_score + 5)

        sim.emotion.add("pride", 0.7, duration=8, source="bought_property")
        self._last_buy[sim.sim_id] = tick

        engine._bus.emit(
            "property_purchased",
            sim=sim,
            property_name=name,
            price=price,
            mortgaged=use_mortgage,
            tick=tick,
        )
        import logging
        logging.getLogger(__name__).info(
            "[Property] %s bought %s for §%.0f%s",
            sim.name, name, price, " (mortgage)" if use_mortgage else "",
        )

    def _owned_by(self, sim_id: str) -> list[str]:
        return [p.property_id for p in self._properties.values() if p.owner_id == sim_id]

    def portfolio(self, sim_id: str) -> list[dict]:
        return [
            {
                "name":          p.name,
                "type":          p.prop_type,
                "value":         round(p.current_value, 2),
                "income_tick":   p.income_per_tick,
                "mortgage_tick": round(p.mortgage_per_tick, 2),
                "ticks_owned":   p.ticks_owned,
            }
            for p in self._properties.values()
            if p.owner_id == sim_id
        ]

    def total_portfolio_value(self, sim_id: str) -> float:
        return sum(
            p.current_value
            for p in self._properties.values()
            if p.owner_id == sim_id
        )
