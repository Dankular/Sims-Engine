"""blockchain/block.py — Block data structure for SimChain (PoA)."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

GENESIS_PREV_HASH = "0" * 64


@dataclass
class Block:
    index: int
    timestamp: float
    transactions: list[dict]
    prev_hash: str
    validator: str      # 0x address of the PoA validator
    block_hash: str = ""

    # ── Hashing ───────────────────────────────────────────────────────────────

    def _content(self) -> str:
        return json.dumps(
            {
                "index": self.index,
                "timestamp": self.timestamp,
                "transactions": self.transactions,
                "prev_hash": self.prev_hash,
                "validator": self.validator,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self._content().encode()).hexdigest()

    def seal(self) -> "Block":
        """Set block_hash. Called by the validator before broadcasting."""
        self.block_hash = self.compute_hash()
        return self

    def is_valid(self) -> bool:
        return bool(self.block_hash) and self.block_hash == self.compute_hash()

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "transactions": self.transactions,
            "prev_hash": self.prev_hash,
            "validator": self.validator,
            "block_hash": self.block_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        b = cls(
            index=d["index"],
            timestamp=d["timestamp"],
            transactions=d["transactions"],
            prev_hash=d["prev_hash"],
            validator=d["validator"],
        )
        b.block_hash = d.get("block_hash", "")
        return b

    # ── Genesis ───────────────────────────────────────────────────────────────

    @classmethod
    def genesis(cls, validator: str) -> "Block":
        b = cls(
            index=0,
            timestamp=time.time(),
            transactions=[],
            prev_hash=GENESIS_PREV_HASH,
            validator=validator,
        )
        return b.seal()

    def __repr__(self) -> str:
        return (
            f"Block(#{self.index} txs={len(self.transactions)} "
            f"hash={self.block_hash[:12]}…)"
        )
