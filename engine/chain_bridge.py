"""
engine/chain_bridge.py — ChainBridge: single financial chokepoint.

All simoleon mutations in the engine route through here so every income,
expense, and transfer produces a corresponding on-chain SimCoin transaction.

Usage (via SimEngine helpers):
    engine._pay(sim, 120.0, "gig_payout")          # income  → mint on-chain
    engine._charge(sim, 30.0, "shop_purchase")      # expense → burn on-chain
    engine._transfer(from_sim, to_sim, 50.0, "gift") # peer   → transfer on-chain

The chain call is non-blocking (queued in pending pool); the simoleon mutation
is immediate so game state stays consistent even if a block hasn't been produced.

Sims without a registered wallet silently skip the chain path — game correctness
is never blocked by blockchain availability.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from world.web3_bridge import Web3Bridge

logger = logging.getLogger(__name__)

SIM_WEI = 10 ** 18

# Reasons that classify as shop-category for ShopRegistry
_SHOP_REASONS = frozenset({
    "shop_purchase", "restaurant", "gym", "spa", "convenience_store",
    "clothing", "electronics", "pharmacy",
})

# Reasons that classify as gig payouts
_GIG_REASONS = frozenset({
    "gig_payout", "gig_pay", "gig_reward", "odd_job",
})

# Reasons that are salary/employment
_SALARY_REASONS = frozenset({
    "salary", "paycheck", "wage", "employment_income",
})


class ChainBridge:
    """
    Thin adapter between SimEngine financial operations and Web3Bridge.

    Does NOT own any state — reads from web3 and chain. The engine passes
    `self.web3` at construction time.
    """

    def __init__(self, web3: "Web3Bridge") -> None:
        self._web3 = web3

    # ── Income (sim receives money) ───────────────────────────────────────────

    # ── Chain-only mirrors (simoleons already mutated by ACID ledger) ──────────
    # These methods ONLY update on-chain $SIM — sim.simoleons must NOT be
    # mutated here. The ledger owns that mutation for ACID correctness.

    def pay(self, sim: "Sim", amount: float, reason: str) -> None:
        """Mirror an income event to the chain ($SIM mint). simoleons already updated."""
        if amount <= 0:
            return
        self._mint(sim.sim_id, amount, reason)

    def charge(self, sim: "Sim", amount: float, reason: str,
               shop_name: str = "", item: str = "", tick: int = 0) -> bool:
        """Mirror an expense to the chain ($SIM burn/shop tx). simoleons already updated."""
        if amount <= 0:
            return True
        if reason in _SHOP_REASONS:
            self._shop_tx(sim.sim_id, shop_name or reason, item or reason, amount, tick)
        else:
            self._burn(sim.sim_id, amount, reason)
        return True

    # ── Transfer (sim → sim) ─────────────────────────────────────────────────

    def transfer(self, from_sim: "Sim", to_sim: "Sim",
                 amount: float, reason: str) -> bool:
        """Mirror a transfer to the chain. simoleons already updated by ledger."""
        if amount <= 0:
            return False
        self._transfer_chain(from_sim.sim_id, to_sim.sim_id, amount, reason)
        return True

    # ── Internal chain calls (fire-and-forget) ────────────────────────────────

    def _mint(self, sim_id: str, amount: float, reason: str) -> None:
        w3 = self._web3
        wallet = w3.wallet_for(sim_id)
        if not wallet:
            return
        simcoin = w3.chain.get_contract("simcoin")
        if simcoin:
            try:
                simcoin.mint(wallet.address, int(amount * SIM_WEI), w3.chain)
                logger.debug("[Bridge] mint %.2f $SIM → %s (%s)", amount, sim_id[:8], reason)
            except Exception as exc:
                logger.debug("[Bridge] mint error: %s", exc)

    def _burn(self, sim_id: str, amount: float, reason: str) -> None:
        w3 = self._web3
        wallet = w3.wallet_for(sim_id)
        if not wallet:
            return
        simcoin = w3.chain.get_contract("simcoin")
        if simcoin:
            try:
                simcoin.burn(wallet.address, int(amount * SIM_WEI), w3.chain)
                logger.debug("[Bridge] burn %.2f $SIM ← %s (%s)", amount, sim_id[:8], reason)
            except Exception as exc:
                logger.debug("[Bridge] burn error: %s", exc)

    def _shop_tx(self, sim_id: str, shop_name: str, item: str,
                 amount: float, tick: int) -> None:
        try:
            self._web3.submit_shop_purchase(sim_id, shop_name, item, amount, tick)
        except Exception as exc:
            logger.debug("[Bridge] shop tx error: %s", exc)

    def _transfer_chain(self, from_id: str, to_id: str,
                        amount: float, reason: str) -> None:
        try:
            self._web3.submit_transfer(from_id, to_id, amount, reason)
        except Exception as exc:
            logger.debug("[Bridge] transfer chain error: %s", exc)
