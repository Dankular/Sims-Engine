"""
core/consequences_hard.py — Hard / irreversible consequence framework.

Soft outcomes dominate current simulation — most negative events are
recoverable within a few ticks. This module enforces hard state transitions:
  BLACKLISTED, BANKRUPT, EVICTED, CAREER_LOCKED_OUT, CUSTODY_LOST,
  PERMANENT_RIVALRY

Repair paths exist but are expensive (simoleons + time + specific interactions).
All transitions emit bus events for drama cascade + institutional response.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.events import EventBus

logger = logging.getLogger(__name__)


class HardState(str, Enum):
    BLACKLISTED       = "blacklisted"        # banned from venue/club/institution
    BANKRUPT          = "bankrupt"           # simoleons below 0, assets seized
    EVICTED           = "evicted"            # lost household, no home lot
    CAREER_LOCKED_OUT = "career_locked_out"  # fired + cannot rejoin sector
    CUSTODY_LOST      = "custody_lost"       # lost custody of child
    PERMANENT_RIVALRY = "permanent_rivalry"  # irreversible enemy bond


@dataclass
class HardConsequenceRecord:
    state:        HardState
    sim_id:       str
    caused_by:    str        # sim_id or system that triggered it
    tick_imposed: int
    context:      str = ""   # narrative description
    # Repair requirements
    repair_cost_simoleons: float = 0.0
    repair_ticks_required: int  = 0
    repair_interactions:   list[str] = field(default_factory=list)
    repair_progress:       float = 0.0   # 0..1
    repaired:              bool  = False


class HardConsequenceEngine:
    """
    Tracks and enforces hard consequence states across sims.
    Called by engine._apply_resolved() and run_tick().
    """

    # Thresholds that auto-trigger hard consequences
    BANKRUPTCY_SIMOLEONS   = -50.0
    EVICTION_MISSED_RENT   = 3          # consecutive missed rent payments
    BLACKLIST_REP_FLOOR    = -80.0

    def __init__(self) -> None:
        # sim_id → list of active hard consequences
        self._records: dict[str, list[HardConsequenceRecord]] = {}

    # ── Imposition ────────────────────────────────────────────────────────────

    def impose(
        self,
        sim: "Sim",
        state: HardState,
        caused_by: str,
        tick: int,
        context: str = "",
        bus: "EventBus | None" = None,
    ) -> HardConsequenceRecord:
        rec = HardConsequenceRecord(
            state=state, sim_id=sim.sim_id,
            caused_by=caused_by, tick_imposed=tick, context=context,
        )
        # Set repair requirements by type
        _REPAIR: dict[HardState, dict] = {
            HardState.BLACKLISTED:      {"cost": 500.0,  "ticks": 50,  "interactions": ["apologise", "bribe"]},
            HardState.BANKRUPT:         {"cost": 1000.0, "ticks": 100, "interactions": ["earn_income", "borrow"]},
            HardState.EVICTED:          {"cost": 800.0,  "ticks": 60,  "interactions": ["find_housing"]},
            HardState.CAREER_LOCKED_OUT:{"cost": 0.0,    "ticks": 150, "interactions": ["retraining", "reputation_rebuild"]},
            HardState.CUSTODY_LOST:     {"cost": 200.0,  "ticks": 200, "interactions": ["legal_petition", "character_witness"]},
            HardState.PERMANENT_RIVALRY:{"cost": 0.0,    "ticks": 300, "interactions": ["public_reconciliation", "mediation"]},
        }
        r = _REPAIR.get(state, {})
        rec.repair_cost_simoleons = r.get("cost", 0.0)
        rec.repair_ticks_required = r.get("ticks", 50)
        rec.repair_interactions   = list(r.get("interactions", []))

        self._records.setdefault(sim.sim_id, []).append(rec)

        # Mark flag on the sim
        setattr(sim, f"_hc_{state.value}", True)

        logger.warning(
            "[HardConsequence] %s imposed on %s (caused by %s)",
            state, sim.name, caused_by[:12],
        )
        if bus:
            bus.emit(
                "hard_consequence_imposed",
                sim=sim, state=state.value, caused_by=caused_by, tick=tick,
            )
        return rec

    # ── Auto-detection ────────────────────────────────────────────────────────

    def check_auto_triggers(self, sim: "Sim", tick: int,
                            bus: "EventBus | None" = None) -> list[HardState]:
        triggered: list[HardState] = []

        # Bankruptcy
        if sim.simoleons < self.BANKRUPTCY_SIMOLEONS and not self.has(sim, HardState.BANKRUPT):
            self.impose(sim, HardState.BANKRUPT, "economy", tick,
                        "simoleons went deeply negative", bus)
            _eng = getattr(sim, '_engine_ref', None)
            if _eng and sim.simoleons != 0.0:
                from persistence.ledger import TX_BANKRUPTCY_SEIZURE
                _eng._tx(sim, -sim.simoleons, TX_BANKRUPTCY_SEIZURE,
                         description='bankruptcy asset seizure', allow_overdraft=True)
            else:
                sim.simoleons = 0.0
            triggered.append(HardState.BANKRUPT)

        # Blacklist from reputation floor
        if (sim.reputation_score < self.BLACKLIST_REP_FLOOR
                and not self.has(sim, HardState.BLACKLISTED)):
            self.impose(sim, HardState.BLACKLISTED, "reputation_system", tick,
                        "reputation fell below social floor", bus)
            triggered.append(HardState.BLACKLISTED)

        return triggered

    # ── Repair progress ───────────────────────────────────────────────────────

    def advance_repair(
        self,
        sim: "Sim", state: HardState,
        simoleons_paid: float = 0.0,
        interaction_completed: str = "",
        tick: int = 0,
    ) -> bool:
        """Advance repair for a consequence. Returns True when fully repaired."""
        records = [r for r in self._records.get(sim.sim_id, [])
                   if r.state == state and not r.repaired]
        if not records:
            return False
        rec = records[0]

        # Money contribution
        if rec.repair_cost_simoleons > 0:
            paid_fraction = simoleons_paid / rec.repair_cost_simoleons
            rec.repair_progress = min(1.0, rec.repair_progress + paid_fraction * 0.4)

        # Interaction contribution
        if interaction_completed in rec.repair_interactions:
            rec.repair_progress = min(1.0, rec.repair_progress + 0.2)

        # Time contribution
        ticks_served = tick - rec.tick_imposed
        time_fraction = ticks_served / max(1, rec.repair_ticks_required)
        rec.repair_progress = min(1.0,
            rec.repair_progress + time_fraction * 0.3)

        if rec.repair_progress >= 1.0:
            rec.repaired = True
            setattr(sim, f"_hc_{state.value}", False)
            logger.info("[HardConsequence] %s repaired for %s", state, sim.name)
            return True
        return False

    # ── Queries ───────────────────────────────────────────────────────────────

    def has(self, sim: "Sim", state: HardState) -> bool:
        return getattr(sim, f"_hc_{state.value}", False)

    def active_for(self, sim: "Sim") -> list[HardConsequenceRecord]:
        return [r for r in self._records.get(sim.sim_id, []) if not r.repaired]

    def blocks_interaction(self, sim: "Sim", interaction_type: str) -> bool:
        """Return True if a hard consequence blocks this interaction type."""
        if self.has(sim, HardState.BANKRUPT) and "buy" in interaction_type:
            return True
        if self.has(sim, HardState.BLACKLISTED) and "club" in interaction_type:
            return True
        if self.has(sim, HardState.CAREER_LOCKED_OUT) and "work" in interaction_type:
            return True
        return False

    def summary(self) -> dict:
        active = sum(
            len([r for r in recs if not r.repaired])
            for recs in self._records.values()
        )
        return {"active_hard_consequences": active, "sims_affected": len(self._records)}
