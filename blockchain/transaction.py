"""blockchain/transaction.py — Signed transaction record."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field

# ── Transaction types ─────────────────────────────────────────────────────────
TX_TRANSFER  = "transfer"       # SimCoin peer-to-peer transfer
TX_MINT      = "mint"           # Token mint (genesis / bridge reconciliation)
TX_BURN      = "burn"           # Token burn
TX_SHOP      = "shop"           # Shop / gig / service purchase
TX_CONTRACT  = "contract"       # Generic smart-contract call
TX_AGREEMENT = "agreement"      # Sim-to-sim contract creation
TX_STOCK_BUY = "stock_buy"      # Buy shares
TX_STOCK_SEL = "stock_sell"     # Sell shares


@dataclass
class SimTransaction:
    tx_type:   str
    from_addr: str          # sender's 0x address (or "system" for mints)
    to_addr:   str          # recipient / contract address
    amount:    int          # SimCoin in wei (1 SIM = 10^18 wei); 0 for non-value calls
    data:      dict         # contract args / metadata

    timestamp: float = field(default_factory=time.time)
    tx_id:     str   = field(default_factory=lambda: uuid.uuid4().hex)
    signature: str   = ""
    tx_hash:   str   = ""

    # ── Hashing & signing ─────────────────────────────────────────────────────

    def _signable(self) -> str:
        return json.dumps(
            {
                "tx_type":   self.tx_type,
                "from_addr": self.from_addr,
                "to_addr":   self.to_addr,
                "amount":    self.amount,
                "data":      self.data,
                "timestamp": self.timestamp,
                "tx_id":     self.tx_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self._signable().encode()).hexdigest()

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "tx_type":   self.tx_type,
            "from_addr": self.from_addr,
            "to_addr":   self.to_addr,
            "amount":    self.amount,
            "data":      self.data,
            "timestamp": self.timestamp,
            "tx_id":     self.tx_id,
            "signature": self.signature,
            "tx_hash":   self.tx_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SimTransaction":
        tx = cls(
            tx_type=d["tx_type"],
            from_addr=d["from_addr"],
            to_addr=d["to_addr"],
            amount=d["amount"],
            data=d.get("data", {}),
            timestamp=d.get("timestamp", time.time()),
            tx_id=d.get("tx_id", uuid.uuid4().hex),
        )
        tx.signature = d.get("signature", "")
        tx.tx_hash   = d.get("tx_hash", "")
        return tx

    def __repr__(self) -> str:
        return (
            f"SimTransaction({self.tx_type} {self.from_addr[:8]}→"
            f"{self.to_addr[:8]} {self.amount/10**18:.4f} SIM)"
        )
