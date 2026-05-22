"""
world/property.py — Investment property and passive income simulation.

Phases covered:
- Phase 1: ownership, partnership, passive income, dividends, ROI
- Phase 2: upgrades, employee control, remote acquisition actions, synergies,
  background simulation
- Phase 3: corporate expansion, district modifiers, taxes/maintenance,
  criminal-economy hooks, city-scale investment pressure
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine


OWNERSHIP_NONE = "none"
OWNERSHIP_PARTNER = "partner"
OWNERSHIP_OWNER = "owner"
OWNERSHIP_CONTROLLING = "controlling_owner"

RESTRICTED_TYPES = {"school", "police_station", "military_base", "city_hall"}


@dataclass
class InvestmentProperty:
    property_id: str
    name: str
    category: str
    ownership_state: str
    owner_id: str
    ownership_percentage: float
    purchase_cost: float
    passive_income_weekly: float
    maintenance_cost_weekly: float
    upgrade_level: int = 1
    reputation: float = 0.5
    cleanliness: float = 0.5
    service_quality: float = 0.5
    district: str = "central"
    employees: list[str] = field(default_factory=list)
    branding: str = ""
    deed_transferability: bool = True
    stolen_flag: bool = False
    destroyed_flag: bool = False

    def income_multiplier(self) -> float:
        return {1: 1.0, 2: 1.25, 3: 1.55}.get(self.upgrade_level, 1.0)

    def current_value(self) -> float:
        base = self.purchase_cost * (0.85 + self.upgrade_level * 0.22)
        rep = 0.85 + self.reputation * 0.5
        return max(100.0, base * rep)


INVESTMENT_CATALOGUE = {
    "diner": ("restaurants", 6000.0, 850.0, 180.0, "low"),
    "bookstore": ("retail", 7000.0, 1000.0, 160.0, "low"),
    "grocery": ("retail", 7000.0, 1000.0, 170.0, "low"),
    "bistro": ("restaurants", 12500.0, 2100.0, 420.0, "medium"),
    "spa": ("entertainment", 15000.0, 2500.0, 450.0, "medium"),
    "theater": ("entertainment", 18000.0, 3000.0, 530.0, "medium"),
    "office": ("technology", 18000.0, 3000.0, 550.0, "medium"),
    "hospital": ("science", 30000.0, 6000.0, 1100.0, "high"),
    "stadium": ("sports", 35000.0, 6500.0, 1300.0, "high"),
    "science_lab": ("science", 40000.0, 7900.0, 1400.0, "high"),
    "observatory": ("science", 50000.0, 12000.0, 1800.0, "very_high"),
    "nightclub": ("nightlife", 26000.0, 4700.0, 800.0, "high"),
    "gaming_facility": ("technology", 28000.0, 5200.0, 900.0, "high"),
}

SYNERGIES = {
    ("grocery", "diner"): 1.08,
    ("bookstore", "spa"): 1.10,
    ("office", "bistro"): 1.09,
    ("hospital", "science_lab"): 1.12,
    ("nightclub", "diner"): 1.06,
}

DISTRICT_MODIFIERS = {
    "central": {
        "tourism": 1.05,
        "crime": 0.95,
        "wealth": 1.05,
        "density": 1.0,
        "supernatural": 1.0,
    },
    "tourist": {
        "tourism": 1.2,
        "crime": 0.9,
        "wealth": 1.0,
        "density": 1.1,
        "supernatural": 1.0,
    },
    "industrial": {
        "tourism": 0.8,
        "crime": 0.95,
        "wealth": 1.0,
        "density": 1.15,
        "supernatural": 1.0,
    },
    "occult": {
        "tourism": 1.0,
        "crime": 1.05,
        "wealth": 0.95,
        "density": 0.95,
        "supernatural": 1.25,
    },
}


class PropertyManager:
    def __init__(self) -> None:
        self._properties: dict[str, InvestmentProperty] = {}
        self._owner_index: dict[str, set[str]] = {}
        self._last_buy_tick: dict[str, int] = {}
        self._last_collect_tick: dict[tuple[str, str], int] = {}
        self._district_cycle: int = 0
        self._sim_lookup: dict[str, Any] = {}

    def tick(self, engine: "SimEngine") -> None:
        self._sim_lookup = dict(getattr(engine, "_sim_lookup", {}))
        self._background_business_sim(engine)
        self._collect_dividends(engine)
        self._apply_maintenance_and_tax(engine)
        self._district_drift()
        self._try_auto_invest(engine)
        self._corporate_expansion(engine)
        self._criminal_economy_hooks(engine)

    def _background_business_sim(self, engine: "SimEngine") -> None:
        hour = (8 + engine.tick_count) % 24
        weather = str(
            getattr(getattr(engine, "weather", None), "current", "clear")
        ).lower()
        for prop in self._properties.values():
            if prop.destroyed_flag:
                continue
            traffic = (
                1.0
                + (0.15 if 18 <= hour <= 23 else 0.0)
                + (0.1 if 10 <= hour <= 14 else 0.0)
            )
            if "rain" in weather:
                traffic *= 0.95
            if "storm" in weather:
                traffic *= 0.88
            if "nightclub" in prop.name and 20 <= hour <= 2:
                traffic *= 1.15
            prop.reputation = max(
                0.0, min(1.0, prop.reputation + random.uniform(-0.02, 0.03))
            )
            prop.cleanliness = max(
                0.0, min(1.0, prop.cleanliness + random.uniform(-0.03, 0.02))
            )
            prop.service_quality = max(
                0.0, min(1.0, prop.service_quality + random.uniform(-0.02, 0.03))
            )
            # store transient multiplier as hidden attr
            setattr(prop, "_traffic_mult", traffic)

    def _collect_dividends(self, engine: "SimEngine") -> None:
        for prop in self._properties.values():
            owner = engine._sim_lookup.get(prop.owner_id)
            if owner is None or prop.destroyed_flag:
                continue
            collect_key = (owner.sim_id, prop.property_id)
            if engine.tick_count - self._last_collect_tick.get(collect_key, -9999) < 14:
                continue
            weekly = self._weekly_income(prop, owner)
            payout = weekly * (
                prop.ownership_percentage
                if prop.ownership_state == OWNERSHIP_PARTNER
                else 1.0
            )
            _eng = getattr(owner, '_engine_ref', None)
            if _eng:
                from persistence.ledger import TX_PROPERTY_DIVIDEND
                _eng._tx(owner, payout / 14.0, TX_PROPERTY_DIVIDEND,
                         counterpart=prop.property_id, description=f'property dividend: {prop.name}')
            else:
                owner.simoleons += payout / 14.0
            self._last_collect_tick[collect_key] = engine.tick_count

    def _apply_maintenance_and_tax(self, engine: "SimEngine") -> None:
        for prop in self._properties.values():
            owner = engine._sim_lookup.get(prop.owner_id)
            if owner is None or prop.destroyed_flag:
                continue
            maint = prop.maintenance_cost_weekly / 7.0
            tax = self._tax_rate(owner, prop) * (self._weekly_income(prop, owner) / 7.0)
            _eng = getattr(owner, '_engine_ref', None)
            if _eng:
                from persistence.ledger import TX_PROPERTY_MAINTENANCE, TX_PROPERTY_TAX
                if maint > 0:
                    _eng._tx(owner, -maint, TX_PROPERTY_MAINTENANCE,
                             counterpart=prop.property_id, description=f'maintenance: {prop.name}')
                if tax > 0:
                    _eng._tx(owner, -tax, TX_PROPERTY_TAX,
                             counterpart=prop.property_id, description=f'property tax: {prop.name}')
            else:
                owner.simoleons = max(0.0, owner.simoleons - maint - tax)
            if owner.simoleons <= 1.0 and random.random() < 0.03:
                self._mark_repossession(owner, prop)

    def _district_drift(self) -> None:
        self._district_cycle += 1
        if self._district_cycle % 20 != 0:
            return
        for key, mods in DISTRICT_MODIFIERS.items():
            mods["tourism"] = max(
                0.75, min(1.35, mods["tourism"] + random.uniform(-0.03, 0.03))
            )
            mods["crime"] = max(
                0.75, min(1.35, mods["crime"] + random.uniform(-0.03, 0.03))
            )
            mods["wealth"] = max(
                0.80, min(1.30, mods["wealth"] + random.uniform(-0.02, 0.02))
            )

    def _try_auto_invest(self, engine: "SimEngine") -> None:
        for sim in engine.sims:
            last = self._last_buy_tick.get(sim.sim_id, -999)
            if engine.tick_count - last < 24:
                continue
            if sim.simoleons < 2500:
                continue
            invest_bias = 0.005 + (
                0.01 if sim.profile.get("aspiration") in {"Fortune", "Career"} else 0.0
            )
            if random.random() > invest_bias:
                continue
            self._auto_buy(sim, engine)

    def _auto_buy(self, sim: "Sim", engine: "SimEngine") -> None:
        venue = self._pick_affordable_venue(sim.simoleons)
        if not venue:
            return
        name, (cat, cost, weekly, maint, _risk) = venue
        state = OWNERSHIP_PARTNER if sim.simoleons < cost * 1.25 else OWNERSHIP_OWNER
        buy_in = cost * (0.35 if state == OWNERSHIP_PARTNER else 1.0)
        if sim.simoleons < buy_in:
            return
        sim.simoleons -= buy_in
        pid = uuid.uuid4().hex[:8]
        district = random.choice(list(DISTRICT_MODIFIERS.keys()))
        prop = InvestmentProperty(
            property_id=pid,
            name=name,
            category=cat,
            ownership_state=state,
            owner_id=sim.sim_id,
            ownership_percentage=0.35 if state == OWNERSHIP_PARTNER else 1.0,
            purchase_cost=buy_in,
            passive_income_weekly=weekly
            * (0.45 if state == OWNERSHIP_PARTNER else 1.0),
            maintenance_cost_weekly=maint,
            district=district,
            employees=[f"npc_emp_{i}" for i in range(random.randint(1, 4))],
        )
        self._properties[pid] = prop
        self._owner_index.setdefault(sim.sim_id, set()).add(pid)
        sim.properties.append(pid)
        self._last_buy_tick[sim.sim_id] = engine.tick_count

    def _corporate_expansion(self, engine: "SimEngine") -> None:
        for sim in engine.sims:
            owned = self._owner_index.get(sim.sim_id, set())
            if len(owned) < 3:
                continue
            if random.random() < 0.03:
                sim.perks.add("corporate_chain")
                sim.reputation_score = min(100.0, sim.reputation_score + 0.4)

    def _criminal_economy_hooks(self, engine: "SimEngine") -> None:
        for sim in engine.sims:
            traits = set(str(t).lower() for t in sim.profile.get("traits", []))
            if (
                "evil" not in traits
                and "criminal" not in str(sim.profile.get("job", "")).lower()
            ):
                continue
            owned = self._owner_index.get(sim.sim_id, set())
            if not owned:
                continue
            if random.random() < 0.02:
                target = self._properties[random.choice(list(owned))]
                target.stolen_flag = True
                _eng = getattr(sim, '_engine_ref', None)
                if _eng:
                    from persistence.ledger import TX_BURGLAR_TAKE
                    _eng._tx(sim, 120.0, TX_BURGLAR_TAKE, description='criminal property income')
                else:
                    sim.simoleons += 120.0

    def upgrade_property(self, sim_id: str, property_id: str) -> dict:
        prop = self._properties.get(property_id)
        if prop is None:
            return {"ok": False, "reason": "property_not_found"}
        if prop.owner_id != sim_id:
            return {"ok": False, "reason": "not_owner"}
        if prop.ownership_state not in {OWNERSHIP_OWNER, OWNERSHIP_CONTROLLING}:
            return {"ok": False, "reason": "insufficient_ownership_state"}
        if prop.upgrade_level >= 3:
            return {"ok": False, "reason": "max_upgrade"}
        owner_funds = self._owner_simoleons(sim_id)
        cost = prop.purchase_cost * (0.25 + prop.upgrade_level * 0.2)
        if owner_funds < cost:
            return {"ok": False, "reason": "insufficient_funds"}
        self._charge_owner(sim_id, cost)
        prop.upgrade_level += 1
        prop.passive_income_weekly *= 1.22
        prop.maintenance_cost_weekly *= 1.1
        return {"ok": True, "upgrade_level": prop.upgrade_level, "cost": round(cost, 2)}

    def purchase_property(
        self,
        sim_id: str,
        venue_type: str,
        ownership_state: str = OWNERSHIP_PARTNER,
        district: str = "central",
    ) -> dict:
        row = INVESTMENT_CATALOGUE.get(venue_type)
        if row is None:
            return {"ok": False, "reason": "unknown_venue_type"}
        if venue_type in RESTRICTED_TYPES:
            return {"ok": False, "reason": "restricted_property"}
        state = ownership_state
        if state not in {OWNERSHIP_PARTNER, OWNERSHIP_OWNER, OWNERSHIP_CONTROLLING}:
            return {"ok": False, "reason": "invalid_ownership_state"}
        cat, cost, weekly, maint, _risk = row
        pct = (
            0.35
            if state == OWNERSHIP_PARTNER
            else (0.75 if state == OWNERSHIP_OWNER else 1.0)
        )
        buy_in = cost * pct
        funds = self._owner_simoleons(sim_id)
        if funds < buy_in:
            return {"ok": False, "reason": "insufficient_funds"}
        self._charge_owner(sim_id, buy_in)
        pid = uuid.uuid4().hex[:8]
        prop = InvestmentProperty(
            property_id=pid,
            name=venue_type,
            category=cat,
            ownership_state=state,
            owner_id=sim_id,
            ownership_percentage=pct,
            purchase_cost=buy_in,
            passive_income_weekly=weekly * pct,
            maintenance_cost_weekly=maint,
            district=district if district in DISTRICT_MODIFIERS else "central",
            employees=[f"npc_emp_{i}" for i in range(random.randint(1, 4))],
        )
        self._properties[pid] = prop
        self._owner_index.setdefault(sim_id, set()).add(pid)
        sim = self._sim_lookup.get(sim_id)
        if sim is not None and pid not in getattr(sim, "properties", []):
            sim.properties.append(pid)
        return {"ok": True, "property_id": pid, "buy_in": round(buy_in, 2)}

    def collect_income(self, sim_id: str, property_id: str) -> dict:
        prop = self._properties.get(property_id)
        if prop is None:
            return {"ok": False, "reason": "property_not_found"}
        if prop.owner_id != sim_id:
            return {"ok": False, "reason": "not_owner"}
        owner = self._sim_lookup.get(sim_id)
        if owner is None:
            return {"ok": False, "reason": "sim_not_found"}
        weekly = self._weekly_income(prop, owner)
        payout = weekly * (
            prop.ownership_percentage
            if prop.ownership_state == OWNERSHIP_PARTNER
            else 1.0
        )
        amount = payout / 7.0
        self._credit_owner(sim_id, amount)
        self._last_collect_tick[(sim_id, property_id)] = -9999
        return {"ok": True, "collected": round(amount, 2)}

    def rename_business(self, sim_id: str, property_id: str, new_name: str) -> dict:
        prop = self._properties.get(property_id)
        if prop is None:
            return {"ok": False, "reason": "property_not_found"}
        if prop.owner_id != sim_id:
            return {"ok": False, "reason": "not_owner"}
        clean = (new_name or "").strip()
        if not clean:
            return {"ok": False, "reason": "invalid_name"}
        prop.branding = clean
        prop.name = clean
        return {"ok": True}

    def manage_employee(
        self, sim_id: str, property_id: str, action: str, employee_id: str = ""
    ) -> dict:
        prop = self._properties.get(property_id)
        if prop is None:
            return {"ok": False, "reason": "property_not_found"}
        if prop.owner_id != sim_id:
            return {"ok": False, "reason": "not_owner"}
        if prop.ownership_state not in {OWNERSHIP_OWNER, OWNERSHIP_CONTROLLING}:
            return {"ok": False, "reason": "insufficient_ownership_state"}
        if action == "hire":
            eid = employee_id or f"npc_emp_{uuid.uuid4().hex[:6]}"
            if eid not in prop.employees:
                prop.employees.append(eid)
            return {"ok": True, "employees": list(prop.employees)}
        if action == "fire":
            if len(prop.employees) <= 1:
                return {"ok": False, "reason": "cannot_remove_last_required_employee"}
            if employee_id in prop.employees:
                prop.employees.remove(employee_id)
            return {"ok": True, "employees": list(prop.employees)}
        return {"ok": False, "reason": "unknown_action"}

    def sell_property(self, sim_id: str, property_id: str) -> dict:
        prop = self._properties.get(property_id)
        if prop is None:
            return {"ok": False, "reason": "property_not_found"}
        if prop.owner_id != sim_id:
            return {"ok": False, "reason": "not_owner"}
        payout = prop.current_value() * (0.9 if not prop.destroyed_flag else 0.25)
        self._credit_owner(sim_id, payout)
        self._owner_index.get(sim_id, set()).discard(property_id)
        self._properties.pop(property_id, None)
        return {"ok": True, "payout": round(payout, 2)}

    def portfolio(self, sim_id: str) -> list[dict]:
        out: list[dict] = []
        for pid in self._owner_index.get(sim_id, set()):
            p = self._properties.get(pid)
            if p is None:
                continue
            weekly = self._weekly_income(p)
            roi = weekly / max(1.0, p.purchase_cost)
            out.append(
                {
                    "property_id": p.property_id,
                    "name": p.name,
                    "category": p.category,
                    "ownership_state": p.ownership_state,
                    "ownership_percentage": round(p.ownership_percentage, 3),
                    "upgrade_level": p.upgrade_level,
                    "weekly_income": round(weekly, 2),
                    "maintenance_weekly": round(p.maintenance_cost_weekly, 2),
                    "value": round(p.current_value(), 2),
                    "roi": round(roi, 4),
                    "payoff_weeks": round(p.purchase_cost / max(1.0, weekly), 2),
                    "district": p.district,
                    "employees": list(p.employees),
                    "branding": p.branding,
                    "flags": {"stolen": p.stolen_flag, "destroyed": p.destroyed_flag},
                }
            )
        return out

    def investment_dashboard(self, sim_id: str) -> dict:
        portfolio = self.portfolio(sim_id)
        total_weekly = sum(float(p["weekly_income"]) for p in portfolio)
        total_value = sum(float(p["value"]) for p in portfolio)
        total_cost = sum(
            float(self._properties[p["property_id"]].purchase_cost)
            for p in portfolio
            if p["property_id"] in self._properties
        )
        return {
            "owned_properties": portfolio,
            "available_properties": sorted(INVESTMENT_CATALOGUE.keys()),
            "income_projection_weekly": round(total_weekly, 2),
            "portfolio_value": round(total_value, 2),
            "total_roi": round(total_weekly / max(1.0, total_cost), 4),
            "district_cycle": self._district_cycle,
        }

    def total_portfolio_value(self, sim_id: str) -> float:
        return sum(
            self._properties[pid].current_value()
            for pid in self._owner_index.get(sim_id, set())
            if pid in self._properties
        )

    def _weekly_income(self, prop: InvestmentProperty, owner=None) -> float:
        district = DISTRICT_MODIFIERS.get(prop.district, DISTRICT_MODIFIERS["central"])
        district_mult = (
            district["tourism"] * district["wealth"] / max(0.75, district["crime"])
        )
        traffic_mult = float(getattr(prop, "_traffic_mult", 1.0))
        rep_mult = 0.8 + prop.reputation * 0.5
        clean_mult = 0.9 + prop.cleanliness * 0.25
        service_mult = 0.9 + prop.service_quality * 0.25
        synergy_mult = self._synergy_multiplier(prop.owner_id, prop.name)
        perk_mult = 1.0
        if owner is not None and "corporate_chain" in getattr(owner, "perks", set()):
            perk_mult += 0.06
        gross = prop.passive_income_weekly * prop.income_multiplier()
        return (
            gross
            * district_mult
            * traffic_mult
            * rep_mult
            * clean_mult
            * service_mult
            * synergy_mult
            * perk_mult
        )

    def _synergy_multiplier(self, owner_id: str, venue_name: str) -> float:
        owned_names = {
            self._properties[pid].name
            for pid in self._owner_index.get(owner_id, set())
            if pid in self._properties
        }
        mult = 1.0
        for (a, b), bonus in SYNERGIES.items():
            if a in owned_names and b in owned_names:
                mult *= bonus
        if "science_lab" in owned_names and "observatory" in owned_names:
            mult *= 1.08
        if "nightclub" in owned_names and "diner" in owned_names:
            mult *= 1.03
        return max(1.0, min(1.6, mult))

    def _tax_rate(self, owner: "Sim", prop: InvestmentProperty) -> float:
        wealth = 0.012 if owner.simoleons < 20000 else 0.02
        luxury = 0.005 if prop.purchase_cost > 30000 else 0.0
        corruption = (
            -0.004
            if "evil" in set(str(t).lower() for t in owner.profile.get("traits", []))
            else 0.0
        )
        return max(0.004, wealth + luxury + corruption)

    def _mark_repossession(self, owner: "Sim", prop: InvestmentProperty) -> None:
        prop.destroyed_flag = True
        prop.reputation = max(0.0, prop.reputation - 0.3)
        owner.emotion.add("nervousness", 0.7, duration=6, source="property_repossessed")

    def _pick_affordable_venue(self, funds: float):
        affordable = [
            row for row in INVESTMENT_CATALOGUE.items() if funds >= row[1][1] * 0.35
        ]
        if not affordable:
            return None
        return random.choice(affordable)

    def _owner_simoleons(self, sim_id: str) -> float:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return 0.0
        return float(getattr(sim, "simoleons", 0.0))

    def _charge_owner(self, sim_id: str, amount: float) -> None:
        sim = self._sim_lookup.get(sim_id)
        if sim is not None:
            cur = float(getattr(sim, "simoleons", 0.0))
            setattr(sim, "simoleons", max(0.0, cur - float(amount)))

    def _credit_owner(self, sim_id: str, amount: float) -> None:
        sim = self._sim_lookup.get(sim_id)
        if sim is not None:
            cur = float(getattr(sim, "simoleons", 0.0))
            setattr(sim, "simoleons", cur + float(amount))
