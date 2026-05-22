"""
world/web3_bridge.py — Async bridge between SimEngine and SimChain.

Responsibilities:
  • One SimWallet per sim, derived deterministically from sim_id.
  • Translates sim-world actions (shop purchase, career event, gig payout)
    into signed SimTransactions submitted to the chain's pending pool.
  • On each engine tick: drives stock price discovery from world events,
    processes agreement installments, auto-invests idle SimCoin.
  • Syncs SimCoin balances → sim.simoleons at each block commit.

The chain is non-blocking from the engine's POV: submit() queues the tx;
the ChainNode seals it into a block every CHAIN_BLOCK_INTERVAL ticks.
"""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from blockchain.chain import SimChain
from blockchain.transaction import (
    SimTransaction,
    TX_TRANSFER, TX_SHOP, TX_STOCK_BUY, TX_STOCK_SEL, TX_AGREEMENT, TX_MINT,
)
from blockchain.wallet import SimWallet

if TYPE_CHECKING:
    from blockchain.contracts.stock_market import StockMarket
    from blockchain.contracts.sim_agreement import AgreementEngine, AgreementType
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

SIM_WEI = 10 ** 18


class Web3Bridge:
    """
    Non-blocking connector between the sim engine and the on-chain economy.

    All public methods return immediately — transactions are queued in the
    chain's pending pool and confirmed at the next block.
    """

    def __init__(self, chain: SimChain) -> None:
        self.chain = chain

        # Game wallets — deterministic secp256k1, server holds private key.
        # Used for ALL automatic game transactions (shops, gigs, taxes, contracts).
        # NEVER replaced after registration.
        self._wallets: dict[str, SimWallet] = {}           # sim_id → SimWallet

        # MetaMask identity layer — player-owned addresses, server never has the key.
        # Stored separately so it can never corrupt the game wallet.
        # Used for: SIWE auth, EIP-712 player-initiated tx signing, off-chain proofs.
        self._metamask_addresses: dict[str, str] = {}      # sim_id → checksum 0x address
        self._metamask_map:       dict[str, str] = {}      # lower(0x addr) → sim_id

        self._pending_balance_sync: dict[str, float] = {}  # sim_id → on-chain balance

        # Subscribe to block commits to sync balances
        chain.on_block(self._on_block_committed)

    # ── Wallet management ─────────────────────────────────────────────────────

    def register_sim(self, sim_id: str, initial_simoleons: float = 0.0) -> SimWallet:
        wallet = SimWallet.from_sim_id(sim_id)
        self._wallets[sim_id] = wallet
        if initial_simoleons > 0:
            simcoin = self.chain.get_contract("simcoin")
            if simcoin:
                simcoin.mint(wallet.address, int(initial_simoleons * SIM_WEI), self.chain)
        logger.debug(
            "[Bridge] Registered sim %s → %s (%.0f $SIM)",
            sim_id[:8], wallet.address[:12], initial_simoleons,
        )
        return wallet

    def wallet_for(self, sim_id: str) -> SimWallet | None:
        return self._wallets.get(sim_id)

    def address_for(self, sim_id: str) -> str | None:
        w = self._wallets.get(sim_id)
        return w.address if w else None

    def simcoin_balance(self, sim_id: str) -> float:
        w = self._wallets.get(sim_id)
        return self.chain.balance_sim(w.address) if w else 0.0

    # ── Transaction builders ──────────────────────────────────────────────────

    def submit_shop_purchase(
        self,
        buyer_sim_id: str,
        shop_name: str,
        item: str,
        cost_simoleons: float,
        tick: int,
        category: str = "retail",
    ) -> bool:
        wallet = self._wallets.get(buyer_sim_id)
        if not wallet:
            return False
        amount_wei = int(cost_simoleons * SIM_WEI)
        if self.chain.balance_of(wallet.address) < amount_wei:
            return False
        tx = SimTransaction(
            tx_type=TX_SHOP,
            from_addr=wallet.address,
            to_addr=f"shop:{shop_name}",
            amount=amount_wei,
            data={
                "shop_name": shop_name,
                "item":      item,
                "tick":      tick,
                "category":  category,
            },
        )
        wallet.sign_transaction(tx)
        ok = self.chain.submit(tx)
        if ok:
            # Immediate stock event (price moves before block commit)
            self.on_world_event(f"shop_visit_{shop_name.lower().split()[0]}")
        return ok

    def submit_transfer(
        self,
        from_sim_id: str,
        to_sim_id: str,
        amount_simoleons: float,
        reason: str = "transfer",
    ) -> bool:
        from_w = self._wallets.get(from_sim_id)
        to_w   = self._wallets.get(to_sim_id)
        if not from_w or not to_w:
            return False
        amount_wei = int(amount_simoleons * SIM_WEI)
        tx = SimTransaction(
            tx_type=TX_TRANSFER,
            from_addr=from_w.address,
            to_addr=to_w.address,
            amount=amount_wei,
            data={"reason": reason},
        )
        from_w.sign_transaction(tx)
        return self.chain.submit(tx)

    def submit_stock_buy(
        self, sim_id: str, ticker: str, shares: int
    ) -> bool:
        wallet = self._wallets.get(sim_id)
        if not wallet:
            return False
        stock_market: "StockMarket | None" = self.chain.get_contract("stock_market")
        if not stock_market:
            return False
        stock = stock_market._stocks.get(ticker)
        if not stock:
            return False
        amount_wei = int(stock.price * SIM_WEI * shares)
        tx = SimTransaction(
            tx_type=TX_STOCK_BUY,
            from_addr=wallet.address,
            to_addr="contract:stock_market",
            amount=amount_wei,
            data={"ticker": ticker, "shares": shares},
        )
        wallet.sign_transaction(tx)
        return self.chain.submit(tx)

    def submit_stock_sell(
        self, sim_id: str, ticker: str, shares: int
    ) -> bool:
        wallet = self._wallets.get(sim_id)
        if not wallet:
            return False
        tx = SimTransaction(
            tx_type=TX_STOCK_SEL,
            from_addr=wallet.address,
            to_addr="contract:stock_market",
            amount=0,
            data={"ticker": ticker, "shares": shares},
        )
        wallet.sign_transaction(tx)
        return self.chain.submit(tx)

    def create_loan(
        self,
        lender_sim_id: str,
        borrower_sim_id: str,
        amount_simoleons: float,
        interest_rate: float = 0.05,
        duration_ticks: int = 50,
        tick: int = 0,
    ) -> bool:
        lender_w   = self._wallets.get(lender_sim_id)
        borrower_w = self._wallets.get(borrower_sim_id)
        if not lender_w or not borrower_w:
            return False
        amount_wei = int(amount_simoleons * SIM_WEI)
        tx = SimTransaction(
            tx_type=TX_AGREEMENT,
            from_addr=lender_w.address,
            to_addr=borrower_w.address,
            amount=amount_wei,
            data={
                "agreement_type": "loan",
                "duration_ticks": duration_ticks,
                "tick": tick,
                "terms": {
                    "interest_rate":      interest_rate,
                    "repay_period_ticks": 10,
                    "max_breaches":       2,
                },
            },
        )
        lender_w.sign_transaction(tx)
        return self.chain.submit(tx)

    def create_employment_contract(
        self,
        employer_sim_id: str,
        employee_sim_id: str,
        salary_simoleons: float,
        period_ticks: int = 5,
        duration_ticks: int = 100,
        tick: int = 0,
    ) -> bool:
        emp_w = self._wallets.get(employer_sim_id)
        ee_w  = self._wallets.get(employee_sim_id)
        if not emp_w or not ee_w:
            return False
        tx = SimTransaction(
            tx_type=TX_AGREEMENT,
            from_addr=emp_w.address,
            to_addr=ee_w.address,
            amount=int(salary_simoleons * SIM_WEI),
            data={
                "agreement_type": "employment",
                "duration_ticks": duration_ticks,
                "tick": tick,
                "terms": {"period_ticks": period_ticks, "max_breaches": 2},
            },
        )
        emp_w.sign_transaction(tx)
        return self.chain.submit(tx)

    # ── Engine tick integration ───────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        """
        Called every engine tick by SimEngine.run_tick().

        1. Random-walk noise on all stock prices.
        2. Agreement installments (salary, rent, loan repayment).
        3. Autonomous investing for high-openness sims.
        """
        stock_market: "StockMarket | None" = self.chain.get_contract("stock_market")
        if stock_market:
            stock_market.tick_market()

        agreement_engine = self.chain.get_contract("sim_agreement")
        if agreement_engine:
            events = agreement_engine.tick_agreements(engine.tick_count, self.chain)
            for evt in events:
                etype = evt.pop("type", "agreement_event")
                try:
                    engine._bus.emit(etype, **evt)
                except Exception:
                    pass

        # Autonomous investing — every 15 ticks
        if stock_market and engine.tick_count % 15 == 0:
            self._auto_invest(engine, stock_market)

    def on_world_event(self, event_type: str, context: dict | None = None) -> None:
        """Drive stock prices from notable sim events. Call from engine hooks."""
        stock_market: "StockMarket | None" = self.chain.get_contract("stock_market")
        if stock_market:
            stock_market.apply_event(event_type, context or {})

    def _auto_invest(self, engine: "SimEngine", stock_market) -> None:
        """High-openness sims with surplus SimCoin buy small stock positions."""
        for sim in engine.sims:
            wallet = self._wallets.get(sim.sim_id)
            if not wallet:
                continue
            balance_sim = self.chain.balance_sim(wallet.address)
            openness = sim.ocean.get("openness", 0.5)
            # Only invest if: high openness, surplus > 500 $SIM, not sleeping
            if openness > 0.65 and balance_sim > 500 and not getattr(sim, "_sleeping", False):
                ticker = random.choice(list(stock_market._stocks.keys()))
                price  = stock_market._stocks[ticker].price
                shares = max(1, int(balance_sim * 0.05 / max(price, 0.01)))
                if shares > 0:
                    self.submit_stock_buy(sim.sim_id, ticker, shares)

    # ── MetaMask identity layer ───────────────────────────────────────────────
    #
    # The game wallet (deterministic, server-managed) and the MetaMask address
    # (player-owned) are SEPARATE concerns:
    #
    #   _wallets[sim_id]            – game wallet, correct secp256k1 key,
    #                                 signs automatic transactions, holds $SIM.
    #   _metamask_addresses[sim_id] – player's MetaMask 0x address,
    #                                 server never has its private key,
    #                                 used for SIWE auth + EIP-712 consent.
    #
    # Balance always stays at the game wallet address. The MetaMask address
    # is an identity/consent layer, not a replacement for the game wallet.

    def link_metamask_wallet(self, sim_id: str, metamask_address: str) -> bool:
        """
        Record a player's MetaMask address as their identity for this sim.

        The game wallet (deterministic) is UNCHANGED — it keeps its private key
        and continues signing automatic game transactions.
        MetaMask is used only for:
          • SIWE authentication (login without password)
          • Player-initiated EIP-712 signed transactions
          • Off-chain proof of sim ownership

        Call this AFTER verifying the SIWE signature via blockchain.siwe.
        Returns True on success, False if the sim has no registered game wallet.
        """
        if sim_id not in self._wallets:
            logger.warning(
                "[Bridge] link_metamask: sim %s has no game wallet registered", sim_id[:8]
            )
            return False

        # Reject if this MetaMask address is already tied to a different sim
        existing_sim = self._metamask_map.get(metamask_address.lower())
        if existing_sim and existing_sim != sim_id:
            logger.warning(
                "[Bridge] MetaMask %s already linked to sim %s, rejecting link to %s",
                metamask_address[:12], existing_sim[:8], sim_id[:8],
            )
            return False

        game_wallet = self._wallets[sim_id]
        self._metamask_addresses[sim_id]           = metamask_address
        self._metamask_map[metamask_address.lower()] = sim_id

        logger.info(
            "[Bridge] MetaMask linked: sim=%s game_wallet=%s metamask=%s",
            sim_id[:8], game_wallet.address[:12], metamask_address[:12],
        )
        return True

    def restore_metamask_link(self, sim_id: str, metamask_address: str) -> None:
        """
        Restore a previously persisted MetaMask link after server restart.
        Does not re-verify the SIWE signature — assumes the auth DB is trusted.
        """
        if metamask_address:
            self._metamask_addresses[sim_id]             = metamask_address
            self._metamask_map[metamask_address.lower()]  = sim_id

    def metamask_address_for(self, sim_id: str) -> str | None:
        """Return the player's linked MetaMask address, or None if not linked."""
        return self._metamask_addresses.get(sim_id)

    def sim_id_for_metamask(self, metamask_address: str) -> str | None:
        return self._metamask_map.get(metamask_address.lower())

    def wallet_info(self, sim_id: str) -> dict:
        """
        Complete wallet picture for a sim — both game wallet and MetaMask identity.
        This is the canonical source of truth for the /auth/me endpoint.
        """
        game_wallet = self._wallets.get(sim_id)
        mm_address  = self._metamask_addresses.get(sim_id)
        return {
            "game_wallet":      game_wallet.address if game_wallet else None,
            "game_balance_sim": self.chain.balance_sim(game_wallet.address) if game_wallet else 0.0,
            "metamask_address": mm_address,
            "metamask_linked":  mm_address is not None,
            "chain_id":         self.chain.chain_id,
        }

    # ── Block commit hook ─────────────────────────────────────────────────────

    def _on_block_committed(self, block) -> None:
        """
        Called by SimChain after every sealed block.
        Reconciles on-chain $SIM balances → sim.simoleons for all registered sims.
        The chain is the authoritative ledger; simoleons on Sim is a read cache.
        """
        for sim_id, wallet in self._wallets.items():
            on_chain = self.chain.balance_sim(wallet.address)
            # Update is deferred — we don't hold an engine reference here.
            # The engine calls sync_balances() after ChainNode.tick() instead.
            self._pending_balance_sync[sim_id] = on_chain

    def sync_balances(self, engine: "SimEngine") -> None:
        """
        Apply pending balance syncs from the last committed block.
        Call once per engine tick after chain_node.tick().
        """
        for sim_id, on_chain_balance in list(self._pending_balance_sync.items()):
            sim = engine._sim_lookup.get(sim_id)
            if sim and abs(on_chain_balance - sim.simoleons) > 0.5:
                sim.simoleons = on_chain_balance
        self._pending_balance_sync.clear()

    # ── Stats / API surface ───────────────────────────────────────────────────

    def stats(self) -> dict:
        stock_market   = self.chain.get_contract("stock_market")
        agreement_eng  = self.chain.get_contract("sim_agreement")
        shop_registry  = self.chain.get_contract("shop_registry")
        return {
            "wallets":       len(self._wallets),
            "chain":         self.chain.summary(),
            "stocks":        stock_market.prices() if stock_market else {},
            "agreements":    agreement_eng.stats() if agreement_eng else {},
            "shop_ledger":   shop_registry.stats() if shop_registry else {},
        }

    def sim_portfolio(self, sim_id: str) -> dict:
        wallet = self._wallets.get(sim_id)
        stock_market: "StockMarket | None" = self.chain.get_contract("stock_market")
        if not wallet or not stock_market:
            return {}
        return {
            "address":         wallet.address,
            "simcoin_balance": self.chain.balance_sim(wallet.address),
            "portfolio":       stock_market.portfolio(wallet.address),
            "portfolio_value": stock_market.portfolio_value(wallet.address),
        }
