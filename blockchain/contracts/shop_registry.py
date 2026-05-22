"""
blockchain/contracts/shop_registry.py — On-chain shop/gig/property ledger.

Every shop purchase, gig payment, and property sale writes a tamper-evident
record to this contract.  The StockMarket reads volume data from here to
drive retail/service-sector stock price movements.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from blockchain.contracts.base import SmartContract

if TYPE_CHECKING:
    from blockchain.chain import SimChain
    from blockchain.transaction import SimTransaction

SIM_WEI = 10 ** 18


@dataclass
class ShopRecord:
    tx_hash:     str
    buyer_addr:  str
    seller_addr: str
    item:        str
    price_wei:   int
    shop_name:   str
    tick:        int
    category:    str = ""
    timestamp:   float = field(default_factory=time.time)

    @property
    def price_sim(self) -> float:
        return self.price_wei / SIM_WEI


class ShopRegistry(SmartContract):
    """
    Tamper-evident ledger for all commercial transactions.

    Provides:
      recent_records(n)     — last N transactions
      volume_for(shop_name) — total wei traded at a shop
      category_volume(cat)  — total wei by category (retail, food, wellness…)
    """
    contract_id = "shop_registry"

    def __init__(self) -> None:
        self._records:         list[ShopRecord]    = []
        self._vol_by_shop:     dict[str, int]      = {}
        self._vol_by_category: dict[str, int]      = {}
        self._vol_by_tick:     dict[int, int]       = {}

    # ── On-chain handler ──────────────────────────────────────────────────────

    def on_shop(self, tx: "SimTransaction", chain: "SimChain") -> None:
        d = tx.data
        record = ShopRecord(
            tx_hash=tx.tx_hash,
            buyer_addr=tx.from_addr,
            seller_addr=tx.to_addr,
            item=d.get("item", "unknown"),
            price_wei=tx.amount,
            shop_name=d.get("shop_name", ""),
            tick=d.get("tick", 0),
            category=d.get("category", "retail"),
        )
        self._records.append(record)
        # Update volume indices
        self._vol_by_shop[record.shop_name] = (
            self._vol_by_shop.get(record.shop_name, 0) + tx.amount
        )
        self._vol_by_category[record.category] = (
            self._vol_by_category.get(record.category, 0) + tx.amount
        )
        self._vol_by_tick[record.tick] = (
            self._vol_by_tick.get(record.tick, 0) + tx.amount
        )
        # Execute SimCoin transfer: buyer → seller (shop address)
        chain._transfer(tx.from_addr, tx.to_addr, tx.amount)

    # ── Queries ───────────────────────────────────────────────────────────────

    def recent_records(self, n: int = 20) -> list[ShopRecord]:
        return self._records[-n:]

    def volume_for(self, shop_name: str) -> float:
        return self._vol_by_shop.get(shop_name, 0) / SIM_WEI

    def category_volume(self, category: str) -> float:
        return self._vol_by_category.get(category, 0) / SIM_WEI

    def total_volume_sim(self) -> float:
        return sum(self._vol_by_shop.values()) / SIM_WEI

    def stats(self) -> dict:
        return {
            "total_transactions": len(self._records),
            "total_volume_sim":   self.total_volume_sim(),
            "unique_shops":       len(self._vol_by_shop),
            "categories":         {k: v / SIM_WEI for k, v in self._vol_by_category.items()},
        }
