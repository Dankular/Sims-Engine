"""
world/institutions.py — Social institutions that push back automatically.

Institutions detect violations of their norms and apply formal sanctions
without requiring explicit LLM adjudication. They run as a background system
each tick, scanning for threshold violations.

Institutions:
  HR (workplace)       — performance reviews, misconduct reports, termination
  Legal                — debt defaults, custody disputes, property fraud
  Union                — salary protections, strike support, collective bargaining
  NeighborhoodWatch    — burglar deterrence, noise complaints, HOA fines
  TaxAuthority         — income tax, property tax, audit triggers
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)


@dataclass
class SanctionRecord:
    institution: str
    sim_id:      str
    sanction_type: str
    amount:      float = 0.0
    tick:        int   = 0
    context:     str   = ""


class HRDepartment:
    """Workplace misconduct detection and termination."""

    PERFORMANCE_REVIEW_INTERVAL = 20
    LOW_PERFORMANCE_THRESHOLD   = 25.0
    MISCONDUCT_REP_THRESHOLD    = -30.0

    def tick(self, engine: "SimEngine") -> list[SanctionRecord]:
        sanctions: list[SanctionRecord] = []
        if engine.tick_count % self.PERFORMANCE_REVIEW_INTERVAL != 0:
            return sanctions

        for sim in engine.sims:
            job = sim.profile.get("job", "")
            if not job:
                continue

            # Performance improvement plan
            if (sim.career_performance < self.LOW_PERFORMANCE_THRESHOLD
                    and random.random() < 0.3):
                sim.career_performance -= 5
                if hasattr(sim, "moodlets"):
                    sim.moodlets.add("stressed", source="HR_PIP")
                sanctions.append(SanctionRecord(
                    "HR", sim.sim_id, "performance_warning",
                    tick=engine.tick_count,
                    context=f"{sim.name} placed on performance improvement plan",
                ))
                engine._bus.emit(
                    "hr_sanction", sim=sim, sanction="performance_warning",
                    tick=engine.tick_count,
                )

            # Misconduct termination
            if (sim.reputation_score < self.MISCONDUCT_REP_THRESHOLD
                    and sim.career_performance < 40
                    and random.random() < 0.2):
                old_job = sim.profile.get("job", "")
                sim.profile["job"] = "Unemployed"
                sim.career_performance = 0
                sim.reputation_score   = max(sim.reputation_score, -50)
                if hasattr(sim, "moodlets"):
                    sim.moodlets.add("devastated", source="HR_fired")

                # Hard consequence
                from core.consequences_hard import HardState
                engine.hard_consequences.impose(
                    sim, HardState.CAREER_LOCKED_OUT,
                    "HR_department", engine.tick_count,
                    f"terminated from {old_job} for misconduct",
                    bus=engine._bus,
                )
                engine._stock_event("career_fired", 1.0)
                sanctions.append(SanctionRecord(
                    "HR", sim.sim_id, "termination",
                    tick=engine.tick_count,
                    context=f"{sim.name} terminated from {old_job}",
                ))

        return sanctions


class LegalSystem:
    """Debt defaults, custody disputes, and fraud investigation."""

    DEBT_REVIEW_INTERVAL = 15

    def tick(self, engine: "SimEngine") -> list[SanctionRecord]:
        sanctions: list[SanctionRecord] = []
        if engine.tick_count % self.DEBT_REVIEW_INTERVAL != 0:
            return sanctions

        for sim in engine.sims:
            # Debt spiral → legal action
            if sim.simoleons < -100 and random.random() < 0.25:
                fine = min(abs(sim.simoleons) * 0.1, 200.0)
                sim.simoleons -= fine
                sim.reputation_score -= 5
                sanctions.append(SanctionRecord(
                    "Legal", sim.sim_id, "debt_collection_fee",
                    amount=fine, tick=engine.tick_count,
                ))
                if hasattr(engine, "_tx"):
                    from persistence.ledger import TX_DEBT_COLLECTION
                    engine._tx(sim, -fine, TX_DEBT_COLLECTION, counterpart="legal_system",
                               description="debt collection fee")
                else:
                    sim.simoleons -= fine

        return sanctions


class UnionOffice:
    """Collective worker protections — salary floor, strike support."""

    MIN_SALARY_PER_TICK = 30.0
    UNION_COVERED_JOBS  = {"factory_worker", "teacher", "nurse", "driver", "cleaner"}

    def tick(self, engine: "SimEngine") -> list[SanctionRecord]:
        sanctions: list[SanctionRecord] = []
        for sim in engine.sims:
            job = sim.profile.get("job", "").lower()
            if job not in self.UNION_COVERED_JOBS:
                continue
            # Protect salary floor
            if sim.simoleons < 100 and random.random() < 0.15:
                payout = self.MIN_SALARY_PER_TICK * 2
                if hasattr(engine, "_tx"):
                    from persistence.ledger import TX_UNION_SUPPORT
                    engine._tx(sim, payout, TX_UNION_SUPPORT,
                               counterpart="union", description="union hardship payment")
                else:
                    sim.simoleons += payout
                if hasattr(sim, "moodlets"):
                    sim.moodlets.add("grateful", source="union_support")
                sanctions.append(SanctionRecord(
                    "Union", sim.sim_id, "hardship_payment",
                    amount=payout, tick=engine.tick_count,
                ))
        return sanctions


class NeighborhoodWatch:
    """Community surveillance — deters burglars, issues noise fines."""

    WATCH_INTERVAL = 10

    def tick(self, engine: "SimEngine") -> list[SanctionRecord]:
        sanctions: list[SanctionRecord] = []
        if engine.tick_count % self.WATCH_INTERVAL != 0:
            return sanctions

        # Reduce burglar success rate when watch is active (passive deterrent)
        if hasattr(engine, "burglar"):
            engine.burglar._watch_active = True

        # Noise violations for high-social sims in late-hour venues
        hour = (engine._tick_count) % 24
        if 22 <= hour or hour < 6:
            for sim in engine.sims:
                if sim.needs.social > 80 and random.random() < 0.05:
                    fine = 25.0
                    if hasattr(engine, "_charge"):
                        if hasattr(engine, "_tx"):
                            from persistence.ledger import TX_NOISE_FINE
                            engine._tx(sim, -fine, TX_NOISE_FINE, counterpart="neighborhood_watch",
                                       description="noise violation fine")
                        else:
                            engine._charge(sim, fine, "noise_fine")
                    else:
                        sim.simoleons -= fine
                    sim.reputation_score -= 2
                    sanctions.append(SanctionRecord(
                        "NeighborhoodWatch", sim.sim_id, "noise_fine",
                        amount=fine, tick=engine.tick_count,
                    ))
        return sanctions


class TaxAuthority:
    """Income tax and property tax collection."""

    TAX_INTERVAL    = 25
    INCOME_TAX_RATE = 0.12
    AUDIT_THRESHOLD = 2000.0

    def tick(self, engine: "SimEngine") -> list[SanctionRecord]:
        sanctions: list[SanctionRecord] = []
        if engine.tick_count % self.TAX_INTERVAL != 0:
            return sanctions

        for sim in engine.sims:
            if sim.simoleons < 50:
                continue
            # Income tax
            tax = sim.simoleons * self.INCOME_TAX_RATE * 0.1  # marginal 10%
            tax = min(tax, sim.simoleons * 0.05)               # cap at 5% wealth
            if tax > 0:
                if hasattr(engine, "_tx"):
                    from persistence.ledger import TX_INCOME_TAX
                    engine._tx(sim, -tax, TX_INCOME_TAX, counterpart="tax_authority",
                               description="income tax")
                else:
                    sim.simoleons -= tax
                sanctions.append(SanctionRecord(
                    "Tax", sim.sim_id, "income_tax",
                    amount=round(tax, 2), tick=engine.tick_count,
                ))

            # Random audit for high earners
            if sim.simoleons > self.AUDIT_THRESHOLD and random.random() < 0.03:
                penalty = sim.simoleons * 0.05
                if hasattr(engine, "_tx"):
                    from persistence.ledger import TX_INCOME_TAX
                    engine._tx(sim, -penalty, TX_INCOME_TAX, counterpart="tax_authority",
                               description="tax audit penalty")
                else:
                    sim.simoleons -= penalty
                sim.reputation_score -= 8
                sanctions.append(SanctionRecord(
                    "Tax", sim.sim_id, "audit_penalty",
                    amount=round(penalty, 2), tick=engine.tick_count,
                ))

        return sanctions


class InstitutionalSanctions:
    """
    Umbrella system: runs all institutions each tick, collects sanctions,
    emits events on the engine bus.
    """

    def __init__(self) -> None:
        self.hr      = HRDepartment()
        self.legal   = LegalSystem()
        self.union   = UnionOffice()
        self.watch   = NeighborhoodWatch()
        self.tax     = TaxAuthority()
        self._history: list[SanctionRecord] = []

    def tick(self, engine: "SimEngine") -> None:
        for system in (self.hr, self.legal, self.union, self.watch, self.tax):
            try:
                records = system.tick(engine)
                self._history.extend(records)
            except Exception as exc:
                logger.debug("[Institutions] %s error: %s",
                             type(system).__name__, exc)

        # Keep last 500 records
        self._history = self._history[-500:]

    def recent(self, n: int = 20) -> list[dict]:
        return [
            {
                "institution":   r.institution,
                "sim_id":        r.sim_id,
                "sanction":      r.sanction_type,
                "amount":        r.amount,
                "tick":          r.tick,
            }
            for r in self._history[-n:]
        ]

    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        for r in self._history:
            by_type[r.sanction_type] = by_type.get(r.sanction_type, 0) + 1
        return {"total_sanctions": len(self._history), "by_type": by_type}
