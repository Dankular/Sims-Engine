"""
blockchain/node.py — ChainNode: PoA block producer + NATS broadcaster.

Produces a block every CHAIN_BLOCK_INTERVAL ticks.  Broadcasts sealed
blocks over NATS (simchain.blocks) when a network is attached.
Remote read-only replicas can subscribe and replay the block stream.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from blockchain.chain import SimChain
from blockchain.wallet import SimWallet

if TYPE_CHECKING:
    from blockchain.block import Block
    from engine.events import EventBus

logger = logging.getLogger(__name__)

NATS_SUBJECT = "simchain.blocks"


class ChainNode:
    """
    PoA validator node — one per server / shard.

    tick() must be called every engine tick.  It drives block production
    and emits "chain_block" on the EventBus when a block is sealed.
    """

    def __init__(
        self,
        chain: SimChain,
        wallet: SimWallet,
        bus: "EventBus",
        block_interval: int = 5,
    ) -> None:
        self.chain          = chain
        self.wallet         = wallet
        self._bus           = bus
        self.block_interval = block_interval
        self._tick          = 0
        self._nats          = None
        self._stats = {
            "blocks_produced":  0,
            "txs_confirmed":    0,
            "blocks_broadcast": 0,
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    def tick(self) -> None:
        self._tick += 1
        if self._tick % self.block_interval == 0:
            block = self.chain.produce_block()
            if block:
                self._stats["blocks_produced"] += 1
                self._stats["txs_confirmed"]   += len(block.transactions)
                self._broadcast(block)

    # ── Broadcasting ──────────────────────────────────────────────────────────

    def _broadcast(self, block: "Block") -> None:
        # In-process event bus (always)
        self._bus.emit(
            "chain_block",
            block=block,
            height=block.index,
            tx_count=len(block.transactions),
        )
        # NATS (when networked)
        if self._nats:
            try:
                payload = json.dumps(block.to_dict()).encode()
                self._nats.publish(NATS_SUBJECT, payload)
                self._stats["blocks_broadcast"] += 1
            except Exception as exc:
                logger.debug("[ChainNode] NATS broadcast failed: %s", exc)

    def attach_nats(self, nats_conn) -> None:
        self._nats = nats_conn
        logger.info("[ChainNode] NATS attached — broadcasting on %s", NATS_SUBJECT)

    # ── Stats / debug ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            **self._stats,
            "validator":     self.wallet.address,
            "block_interval": self.block_interval,
            **self.chain.summary(),
        }
