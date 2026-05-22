"""
engine/aoi.py — Per-client Area-of-Interest subscription manager.

Clients register interest in one or more shard_ids (lots / venues).
The manager filters outgoing state diffs and events before they hit the
wire so each WebSocket connection only receives nearby sim data.

Empty shard_ids means "subscribe to everything" (backward-compatible
global view used by existing server.py clients).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class AOISubscription:
    client_id: str
    shard_ids: set[str] = field(default_factory=set)
    callback: Callable[[dict], None] | None = None

    @property
    def global_view(self) -> bool:
        return not self.shard_ids


class AOIManager:
    """
    Per-client AOI subscription registry.

    Server-side WebSocket handler:
      1. Call subscribe(client_id, shard_ids={lot_id}, callback=ws.send)
      2. Engine calls dispatch(shard_id, event) after each tick.
      3. Only subscribed clients receive the event.
      4. Call unsubscribe(client_id) on disconnect.
    """

    def __init__(self) -> None:
        self._subs: dict[str, AOISubscription] = {}

    def subscribe(
        self,
        client_id: str,
        shard_ids: set[str] | None = None,
        callback: Callable[[dict], None] | None = None,
    ) -> AOISubscription:
        sub = AOISubscription(
            client_id=client_id,
            shard_ids=set(shard_ids) if shard_ids else set(),
            callback=callback,
        )
        self._subs[client_id] = sub
        logger.debug("[AOI] %s subscribed to %s", client_id[:8], shard_ids or "global")
        return sub

    def unsubscribe(self, client_id: str) -> None:
        self._subs.pop(client_id, None)

    def update_interest(self, client_id: str, shard_ids: set[str]) -> None:
        if client_id in self._subs:
            self._subs[client_id].shard_ids = set(shard_ids)

    def is_interested(self, client_id: str, shard_id: str) -> bool:
        sub = self._subs.get(client_id)
        if sub is None:
            return False
        return sub.global_view or shard_id in sub.shard_ids

    def interested_clients(self, shard_id: str) -> list[str]:
        return [
            cid for cid, sub in self._subs.items()
            if sub.global_view or shard_id in sub.shard_ids
        ]

    def dispatch(self, shard_id: str, event: dict) -> None:
        """Push event to all clients whose AOI includes shard_id."""
        for cid, sub in self._subs.items():
            if sub.global_view or shard_id in sub.shard_ids:
                if sub.callback:
                    try:
                        sub.callback(event)
                    except Exception as exc:
                        logger.debug("[AOI] dispatch error for %s: %s", cid[:8], exc)

    def broadcast(self, event: dict) -> None:
        """Push event to every subscribed client regardless of AOI."""
        for sub in self._subs.values():
            if sub.callback:
                try:
                    sub.callback(event)
                except Exception:
                    pass

    @property
    def client_count(self) -> int:
        return len(self._subs)
