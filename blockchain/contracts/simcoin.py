"""
blockchain/contracts/simcoin.py — ERC-20-like SimCoin ($SIM) token.

1 simoleon (game currency) = 1 $SIM = 10^18 wei.

Minted at engine startup per sim proportional to starting simoleons.
The bridge reconciles simoleons ↔ $SIM at each block commit — the
chain balance is authoritative; simoleons on the Sim object are a mirror.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from blockchain.contracts.base import SmartContract

if TYPE_CHECKING:
    from blockchain.chain import SimChain
    from blockchain.transaction import SimTransaction

logger = logging.getLogger(__name__)

SIM_DECIMALS = 18
SIM_WEI      = 10 ** SIM_DECIMALS


def to_wei(sim: float) -> int:
    return int(sim * SIM_WEI)


def from_wei(wei: int) -> float:
    return wei / SIM_WEI


class SimCoin(SmartContract):
    contract_id = "simcoin"

    def __init__(self) -> None:
        self._total_supply: int = 0
        # (owner, spender) → approved amount in wei
        self._allowances: dict[tuple[str, str], int] = {}

    # ── ERC-20 surface ────────────────────────────────────────────────────────

    @property
    def total_supply(self) -> int:
        return self._total_supply

    @property
    def total_supply_sim(self) -> float:
        return from_wei(self._total_supply)

    def balance_of(self, address: str, chain: "SimChain") -> float:
        return chain.balance_sim(address)

    def mint(self, to_addr: str, amount_wei: int, chain: "SimChain") -> None:
        chain._balances[to_addr] = chain.balance_of(to_addr) + amount_wei
        self._total_supply += amount_wei
        logger.debug("[SimCoin] Minted %.4f $SIM → %s", from_wei(amount_wei), to_addr[:12])

    def burn(self, from_addr: str, amount_wei: int, chain: "SimChain") -> bool:
        if chain.balance_of(from_addr) < amount_wei:
            return False
        chain._balances[from_addr] -= amount_wei
        self._total_supply -= amount_wei
        return True

    def transfer(
        self, from_addr: str, to_addr: str, amount_wei: int, chain: "SimChain"
    ) -> bool:
        return chain._transfer(from_addr, to_addr, amount_wei)

    def approve(self, owner: str, spender: str, amount_wei: int) -> None:
        self._allowances[(owner, spender)] = amount_wei

    def allowance(self, owner: str, spender: str) -> int:
        return self._allowances.get((owner, spender), 0)

    def transfer_from(
        self,
        spender: str,
        from_addr: str,
        to_addr: str,
        amount_wei: int,
        chain: "SimChain",
    ) -> bool:
        allowed = self.allowance(from_addr, spender)
        if allowed < amount_wei:
            return False
        ok = chain._transfer(from_addr, to_addr, amount_wei)
        if ok:
            self._allowances[(from_addr, spender)] = allowed - amount_wei
        return ok

    # ── Contract dispatch ─────────────────────────────────────────────────────

    def on_mint(self, tx: "SimTransaction", chain: "SimChain") -> None:
        self.mint(tx.to_addr, tx.amount, chain)

    def on_burn(self, tx: "SimTransaction", chain: "SimChain") -> None:
        self.burn(tx.from_addr, tx.amount, chain)

    def on_transfer(self, tx: "SimTransaction", chain: "SimChain") -> None:
        self.transfer(tx.from_addr, tx.to_addr, tx.amount, chain)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_supply_sim": self.total_supply_sim,
            "allowances":       len(self._allowances),
        }
