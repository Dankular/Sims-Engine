from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass


@dataclass
class LedgerTx:
    tx_id: str
    tx_type: str
    tick: int
    payload: dict
    ts: float


@dataclass
class LedgerBlock:
    index: int
    tick: int
    txs: list[LedgerTx]
    prev_hash: str
    block_hash: str
    ts: float


class SimLedger:
    """Hash-chained append-only ledger with periodic block commits."""

    def __init__(self, block_interval: int = 5) -> None:
        self.block_interval = max(1, int(block_interval))
        self._pending: list[LedgerTx] = []
        self._blocks: list[LedgerBlock] = []
        self._nonce = 0

    def record(self, tx_type: str, tick: int, payload: dict) -> str:
        self._nonce += 1
        tx_id = f"tx_{tick}_{self._nonce}"
        self._pending.append(
            LedgerTx(
                tx_id=tx_id,
                tx_type=str(tx_type),
                tick=int(tick),
                payload=dict(payload),
                ts=time.time(),
            )
        )
        return tx_id

    def tick(self, tick: int) -> LedgerBlock | None:
        if tick % self.block_interval != 0:
            return None
        if not self._pending:
            return None
        prev_hash = self._blocks[-1].block_hash if self._blocks else "genesis"
        index = len(self._blocks)
        txs = list(self._pending)
        self._pending.clear()
        blob = json.dumps(
            {
                "index": index,
                "tick": tick,
                "prev_hash": prev_hash,
                "txs": [
                    {
                        "tx_id": t.tx_id,
                        "tx_type": t.tx_type,
                        "tick": t.tick,
                        "payload": t.payload,
                        "ts": t.ts,
                    }
                    for t in txs
                ],
            },
            sort_keys=True,
        )
        block_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        block = LedgerBlock(
            index=index,
            tick=int(tick),
            txs=txs,
            prev_hash=prev_hash,
            block_hash=block_hash,
            ts=time.time(),
        )
        self._blocks.append(block)
        return block

    def recent_blocks(self, n: int = 10) -> list[dict]:
        out = []
        for b in self._blocks[-max(1, int(n)) :]:
            out.append(
                {
                    "index": b.index,
                    "tick": b.tick,
                    "tx_count": len(b.txs),
                    "prev_hash": b.prev_hash,
                    "block_hash": b.block_hash,
                    "ts": b.ts,
                }
            )
        return out

    def recent_txs(self, n: int = 30) -> list[dict]:
        committed = [tx for b in self._blocks for tx in b.txs]
        all_txs = committed + list(self._pending)
        return [
            {
                "tx_id": t.tx_id,
                "tx_type": t.tx_type,
                "tick": t.tick,
                "payload": t.payload,
                "ts": t.ts,
            }
            for t in all_txs[-max(1, int(n)) :]
        ]

    def verify(self) -> bool:
        prev_hash = "genesis"
        for b in self._blocks:
            blob = json.dumps(
                {
                    "index": b.index,
                    "tick": b.tick,
                    "prev_hash": b.prev_hash,
                    "txs": [
                        {
                            "tx_id": t.tx_id,
                            "tx_type": t.tx_type,
                            "tick": t.tick,
                            "payload": t.payload,
                            "ts": t.ts,
                        }
                        for t in b.txs
                    ],
                },
                sort_keys=True,
            )
            if b.prev_hash != prev_hash:
                return False
            if hashlib.sha256(blob.encode("utf-8")).hexdigest() != b.block_hash:
                return False
            prev_hash = b.block_hash
        return True

    def state(self) -> dict:
        return {
            "block_interval": self.block_interval,
            "height": len(self._blocks),
            "pending_txs": len(self._pending),
            "valid": self.verify(),
            "recent_blocks": self.recent_blocks(8),
        }
