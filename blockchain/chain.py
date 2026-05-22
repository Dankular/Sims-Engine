"""
blockchain/chain.py — SimChain (Proof of Authority).

One authoritative validator produces blocks at a configurable tick interval.
Finality is immediate — no forks, no confirmations needed.

Smart contracts are Python objects (not EVM bytecode) registered via deploy().
They execute synchronously inside produce_block() as transactions are applied.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from blockchain.block import Block
from blockchain.transaction import SimTransaction, TX_TRANSFER, TX_MINT, TX_BURN

logger = logging.getLogger(__name__)


class SimChain:
    """
    PoA blockchain for the sim economy.

    State:
      _chain      — append-only list of sealed blocks
      _pending    — transaction pool (cleared on each block)
      _balances   — address → wei balance (authoritative)
      _contracts  — contract_id → SmartContract instance
    """

    def __init__(self, validator_address: str) -> None:
        from blockchain.eip712 import CHAIN_ID
        self.chain_id          = CHAIN_ID
        self.validator_address = validator_address
        self._lock             = threading.RLock()
        self._chain: list[Block]            = [Block.genesis(validator_address)]
        self._pending: list[SimTransaction] = []
        self._balances: dict[str, int]      = {}
        self._nonces:   dict[str, int]      = {}   # address.lower() → tx count
        self._contracts: dict[str, Any]     = {}
        self._block_listeners: list[Callable[[Block], None]] = []
        self._tx_listeners:    list[Callable[[SimTransaction], None]] = []

    # ── Chain queries ─────────────────────────────────────────────────────────

    @property
    def head(self) -> Block:
        return self._chain[-1]

    @property
    def height(self) -> int:
        return len(self._chain) - 1

    def balance_of(self, address: str) -> int:
        return self._balances.get(address, 0)

    def balance_sim(self, address: str) -> float:
        return self.balance_of(address) / 10**18

    def get_block(self, index: int) -> Block | None:
        if 0 <= index < len(self._chain):
            return self._chain[index]
        return None

    # ── Transaction submission ─────────────────────────────────────────────────

    def submit(self, tx: SimTransaction) -> bool:
        """Add a signed transaction to the pending pool. Non-blocking."""
        if not tx.tx_hash:
            logger.warning("[Chain] Rejected tx with no hash: %s", tx.tx_id[:8])
            return False
        if tx.tx_type == TX_TRANSFER and self.balance_of(tx.from_addr) < tx.amount:
            logger.debug(
                "[Chain] Insufficient balance: %s has %d wei, needs %d",
                tx.from_addr[:10], self.balance_of(tx.from_addr), tx.amount,
            )
            return False
        with self._lock:
            self._pending.append(tx)
        return True

    def submit_unsigned(self, tx: SimTransaction) -> bool:
        """Submit a system transaction (mint/burn) without signature check."""
        with self._lock:
            self._pending.append(tx)
        return True

    # ── Block production ──────────────────────────────────────────────────────

    def produce_block(self) -> Block | None:
        """
        Drain pending pool → build block → execute transactions → seal → append.
        Called by ChainNode every CHAIN_BLOCK_INTERVAL ticks.
        Returns None if the pool was empty (no block produced).
        """
        with self._lock:
            if not self._pending:
                return None
            txs = list(self._pending)
            self._pending.clear()

        block = Block(
            index=self.height + 1,
            timestamp=time.time(),
            transactions=[tx.to_dict() for tx in txs],
            prev_hash=self.head.block_hash,
            validator=self.validator_address,
        ).seal()

        # Execute each transaction and increment nonce
        for tx in txs:
            self._execute(tx)
            addr = tx.from_addr.lower()
            self._nonces[addr] = self._nonces.get(addr, 0) + 1
            for ln in self._tx_listeners:
                try:
                    ln(tx)
                except Exception:
                    pass

        with self._lock:
            self._chain.append(block)

        logger.info(
            "[Chain] Block #%d — %d txs | %s",
            block.index, len(txs), block.block_hash[:16],
        )
        for ln in self._block_listeners:
            try:
                ln(block)
            except Exception as exc:
                logger.warning("[Chain] block listener error: %s", exc)

        return block

    # ── Transaction execution ─────────────────────────────────────────────────

    def _execute(self, tx: SimTransaction) -> None:
        if tx.tx_type == TX_TRANSFER:
            self._transfer(tx.from_addr, tx.to_addr, tx.amount)

        elif tx.tx_type == TX_MINT:
            self._balances[tx.to_addr] = self.balance_of(tx.to_addr) + tx.amount

        elif tx.tx_type == TX_BURN:
            bal = self.balance_of(tx.from_addr)
            self._balances[tx.from_addr] = max(0, bal - tx.amount)

        else:
            # Route to registered contracts: first matching handler wins
            for contract in self._contracts.values():
                handler = getattr(contract, f"on_{tx.tx_type}", None)
                if handler:
                    try:
                        handler(tx, self)
                    except Exception as exc:
                        logger.warning(
                            "[Chain] Contract %s handler error for %s: %s",
                            type(contract).__name__, tx.tx_type, exc,
                        )
                    break  # one handler per tx_type

    def _transfer(self, from_addr: str, to_addr: str, amount: int) -> bool:
        if amount == 0:
            return True
        if self.balance_of(from_addr) < amount:
            return False
        self._balances[from_addr] = self.balance_of(from_addr) - amount
        self._balances[to_addr]   = self.balance_of(to_addr)   + amount
        return True

    # ── Contract registry ─────────────────────────────────────────────────────

    def deploy(self, contract: Any) -> str:
        cid = contract.contract_id
        self._contracts[cid] = contract
        logger.info("[Chain] Deployed: %s (%s)", cid, type(contract).__name__)
        return cid

    def get_contract(self, contract_id: str) -> Any | None:
        return self._contracts.get(contract_id)

    # ── Event hooks ───────────────────────────────────────────────────────────

    def on_block(self, fn: Callable[[Block], None]) -> None:
        self._block_listeners.append(fn)

    def on_transaction(self, fn: Callable[[SimTransaction], None]) -> None:
        self._tx_listeners.append(fn)

    # ── Chain validation ──────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        for i in range(1, len(self._chain)):
            b, prev = self._chain[i], self._chain[i - 1]
            if not b.is_valid():
                return False
            if b.prev_hash != prev.block_hash:
                return False
            if b.validator != self.validator_address:
                return False
        return True

    # ── Serialisation (for /chain endpoint) ───────────────────────────────────

    def summary(self) -> dict:
        with self._lock:
            return {
                "height":        self.height,
                "head_hash":     self.head.block_hash,
                "pending_txs":   len(self._pending),
                "total_wallets": len(self._balances),
                "contracts":     list(self._contracts.keys()),
                "is_valid":      self.is_valid(),
            }
