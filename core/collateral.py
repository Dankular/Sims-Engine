"""
core/collateral.py — Asset collateral evaluation and credit extension.

When a sim's simoleons approach COLLATERAL_TRIGGER_BALANCE (-50 by default),
the CollateralEngine evaluates all their assets and extends credit proportional
to their net collateral value.

Asset classes accepted as collateral:
  Properties     — current_value() from PropertyManager
  Businesses     — estimated market value from engine._run_business_system config
  Bank deposits  — principal of active term deposits (locked but guaranteed)
  Stock portfolio — current market value from WorldStockMarket

Credit line = SUM(asset_values) * COLLATERAL_CREDIT_RATIO (70%)

If simoleons fall below COLLATERAL_MARGIN_CALL (-500), forced liquidation
begins: assets are sold at a 30% haircut, proceeds credited to the ledger.

Repair path: pay down the balance above the trigger threshold and the
collateral record is released automatically.

Engine integration:
  engine._tx() checks balance after every transaction and calls
  engine.collateral.evaluate(sim, engine) when threshold is crossed.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

from config import (
    COLLATERAL_TRIGGER_BALANCE,
    COLLATERAL_CREDIT_RATIO,
    COLLATERAL_MARGIN_CALL,
)

logger = logging.getLogger(__name__)

_BUSINESS_VALUATIONS = {
    "retail":      5200.0 * 1.2,
    "restaurant":  6800.0 * 1.2,
    "vet":         6100.0 * 1.2,
}
_LIQUIDATION_HAIRCUT = 0.30   # forced sale recovers 70% of market value


@dataclass
class CollateralAsset:
    asset_type:  str     # "property", "business", "bank_deposit", "stock"
    asset_id:    str
    description: str
    market_value: float
    collateral_value: float    # market_value * CREDIT_RATIO
    pledged_at:  float = field(default_factory=time.time)


@dataclass
class CollateralRecord:
    record_id:     str
    sim_id:        str
    balance_at_trigger: float
    credit_extended:    float
    assets:             list[CollateralAsset]
    created_at:    float = field(default_factory=time.time)
    released_at:   float = 0.0
    liquidated:    bool  = False

    @property
    def total_collateral_value(self) -> float:
        return sum(a.collateral_value for a in self.assets)

    def to_dict(self) -> dict:
        return {
            "record_id":           self.record_id,
            "sim_id":              self.sim_id,
            "balance_at_trigger":  round(self.balance_at_trigger, 2),
            "credit_extended":     round(self.credit_extended, 2),
            "total_collateral":    round(self.total_collateral_value, 2),
            "assets":              [
                {"type": a.asset_type, "id": a.asset_id,
                 "desc": a.description, "market_value": round(a.market_value, 2),
                 "collateral_value": round(a.collateral_value, 2)}
                for a in self.assets
            ],
            "created_at": self.created_at,
            "released_at": self.released_at,
            "liquidated": self.liquidated,
        }


class CollateralEngine:
    """
    Evaluates sim assets and extends credit when balance crosses the trigger.
    Called from engine._tx() after every ledger write.
    """

    def __init__(self) -> None:
        # sim_id → active CollateralRecord
        self._active:  dict[str, CollateralRecord] = {}
        self._history: list[CollateralRecord]      = []

    # ── Main evaluation ───────────────────────────────────────────────────────

    def evaluate(self, sim: "Sim", engine: "SimEngine") -> CollateralRecord | None:
        """
        Evaluate assets and extend or revoke credit.
        Called whenever sim.simoleons < COLLATERAL_TRIGGER_BALANCE.
        Returns the CollateralRecord if credit was extended, None if already covered.
        """
        # Release if balance recovered
        if sim.simoleons >= COLLATERAL_TRIGGER_BALANCE:
            self._release(sim, engine)
            return None

        # Already has an active record — check for margin call
        existing = self._active.get(sim.sim_id)
        if existing:
            if sim.simoleons <= COLLATERAL_MARGIN_CALL:
                self._margin_call(sim, existing, engine)
            return existing

        # New evaluation
        assets = self._collect_assets(sim, engine)
        total_collateral = sum(a.collateral_value for a in assets)

        if total_collateral <= 0:
            # No assets — trigger bankruptcy path
            logger.warning(
                "[Collateral] %s has no assets to pledge (balance=%.2f) — bankruptcy",
                sim.name, sim.simoleons,
            )
            from core.consequences_hard import HardState
            engine.hard_consequences.impose(
                sim, HardState.BANKRUPT, "collateral_engine",
                tick=getattr(engine, "_tick_count", 0),
                context="No collateral assets; balance below floor",
                bus=engine._bus,
            )
            return None

        # Credit extended = min(total_collateral, abs(deficit) * 1.5)
        deficit        = abs(sim.simoleons)
        credit_extended = min(total_collateral, deficit * 1.5)
        record = CollateralRecord(
            record_id=uuid.uuid4().hex[:10],
            sim_id=sim.sim_id,
            balance_at_trigger=sim.simoleons,
            credit_extended=credit_extended,
            assets=assets,
        )
        self._active[sim.sim_id] = record

        # Log credit extension via ledger
        try:
            from persistence.ledger import TX_CORRECTION
            engine._tx(
                sim, credit_extended, TX_CORRECTION,
                counterpart="collateral_engine",
                description=(
                    f"Collateral credit extended: §{credit_extended:.2f} "
                    f"against §{total_collateral:.2f} asset value"
                ),
                metadata={"record_id": record.record_id,
                           "asset_count": len(assets)},
                allow_overdraft=True,
            )
        except Exception as exc:
            logger.warning("[Collateral] ledger credit extension failed: %s", exc)

        engine._bus.emit(
            "collateral_posted",
            sim_id=sim.sim_id,
            credit=credit_extended,
            assets=[a.asset_type for a in assets],
        )
        logger.info(
            "[Collateral] %s posted §%.2f collateral, credit extended §%.2f",
            sim.name, total_collateral, credit_extended,
        )
        return record

    # ── Asset collection ──────────────────────────────────────────────────────

    def _collect_assets(self, sim: "Sim", engine: "SimEngine") -> list[CollateralAsset]:
        assets: list[CollateralAsset] = []

        # 1. Properties
        try:
            owned_prop_ids = getattr(sim, "properties", [])
            for pid in owned_prop_ids:
                prop = engine.properties._properties.get(pid)
                if prop and not prop.destroyed_flag:
                    mv = prop.current_value()
                    assets.append(CollateralAsset(
                        asset_type="property",
                        asset_id=pid,
                        description=prop.name,
                        market_value=mv,
                        collateral_value=mv * COLLATERAL_CREDIT_RATIO,
                    ))
        except Exception:
            pass

        # 2. Businesses
        try:
            for biz in getattr(sim, "owned_businesses", []):
                mv = _BUSINESS_VALUATIONS.get(biz, 5000.0)
                assets.append(CollateralAsset(
                    asset_type="business",
                    asset_id=f"biz:{biz}",
                    description=f"{biz.title()} business",
                    market_value=mv,
                    collateral_value=mv * COLLATERAL_CREDIT_RATIO,
                ))
        except Exception:
            pass

        # 3. Bank deposits (principal — guaranteed return)
        try:
            if hasattr(engine, "bank"):
                locked = engine.bank.total_locked(sim.sim_id)
                if locked > 0:
                    assets.append(CollateralAsset(
                        asset_type="bank_deposit",
                        asset_id="bank_deposits",
                        description="Term deposits (locked principal)",
                        market_value=locked,
                        collateral_value=locked * 0.95,  # 95% — nearly risk-free
                    ))
        except Exception:
            pass

        # 4. Stock portfolio
        try:
            wallet = engine.web3._wallets.get(sim.sim_id)
            sm     = engine.stocks
            if wallet and sm:
                holdings = sm._holdings.get(sim.sim_id, {})
                portfolio_value = sum(
                    sm._stocks[t].price * qty
                    for t, qty in holdings.items()
                    if t in sm._stocks and qty > 0
                )
                if portfolio_value > 0:
                    assets.append(CollateralAsset(
                        asset_type="stock",
                        asset_id="portfolio",
                        description="Stock portfolio",
                        market_value=portfolio_value,
                        collateral_value=portfolio_value * COLLATERAL_CREDIT_RATIO,
                    ))
        except Exception:
            pass

        # 5. On-chain SimCoin balance (if player has MetaMask wallet separate from game wallet)
        try:
            if hasattr(engine, "web3"):
                chain_bal = engine.web3.simcoin_balance(sim.sim_id)
                if chain_bal > 0:
                    assets.append(CollateralAsset(
                        asset_type="simcoin",
                        asset_id="on_chain",
                        description="On-chain $SIM balance",
                        market_value=chain_bal,
                        collateral_value=chain_bal * COLLATERAL_CREDIT_RATIO,
                    ))
        except Exception:
            pass

        return assets

    # ── Release ───────────────────────────────────────────────────────────────

    def _release(self, sim: "Sim", engine: "SimEngine") -> None:
        rec = self._active.pop(sim.sim_id, None)
        if rec:
            rec.released_at = time.time()
            self._history.append(rec)
            engine._bus.emit("collateral_released", sim_id=sim.sim_id)
            logger.info("[Collateral] %s collateral released (balance recovered)", sim.name)

    # ── Margin call ───────────────────────────────────────────────────────────

    def _margin_call(
        self, sim: "Sim", record: CollateralRecord, engine: "SimEngine"
    ) -> None:
        """Forced liquidation at margin call threshold."""
        if record.liquidated:
            return

        record.liquidated = True
        total_recovered = 0.0

        for asset in record.assets:
            # Force-sell at liquidation price
            recovery = asset.market_value * (1 - _LIQUIDATION_HAIRCUT)

            if asset.asset_type == "property":
                try:
                    sell_out = engine.properties.sell_property(
                        sim.sim_id, asset.asset_id, force=True
                    )
                    recovery = float(sell_out.get("proceeds", recovery))
                except Exception:
                    pass

            elif asset.asset_type == "stock":
                try:
                    holdings = engine.stocks._holdings.get(sim.sim_id, {})
                    for ticker, qty in list(holdings.items()):
                        engine.stocks.sell(sim.sim_id, ticker, qty, engine)
                except Exception:
                    pass

            try:
                from persistence.ledger import TX_CORRECTION
                engine._tx(
                    sim, recovery, TX_CORRECTION,
                    counterpart="collateral_liquidation",
                    description=f"Forced liquidation: {asset.description}",
                    metadata={"asset_id": asset.asset_id, "haircut": _LIQUIDATION_HAIRCUT},
                    allow_overdraft=True,
                )
            except Exception:
                pass
            total_recovered += recovery

        self._active.pop(sim.sim_id, None)
        record.released_at = time.time()
        self._history.append(record)

        engine._bus.emit(
            "margin_call",
            sim_id=sim.sim_id,
            recovered=total_recovered,
            balance_after=sim.simoleons,
        )
        logger.warning(
            "[Collateral] MARGIN CALL on %s — recovered §%.2f from %d assets",
            sim.name, total_recovered, len(record.assets),
        )

    # ── Queries ───────────────────────────────────────────────────────────────

    def active_for(self, sim_id: str) -> CollateralRecord | None:
        return self._active.get(sim_id)

    def history_for(self, sim_id: str) -> list[CollateralRecord]:
        return [r for r in self._history if r.sim_id == sim_id]

    def stats(self) -> dict:
        return {
            "active_collateral_sims":   len(self._active),
            "total_historical_records": len(self._history),
            "liquidations":             sum(1 for r in self._history if r.liquidated),
        }
