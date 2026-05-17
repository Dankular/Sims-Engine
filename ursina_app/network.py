"""
ursina_app/network.py — Lightweight NATS room client for the Ursina frontend.

Handles only what the visual layer needs:
  - Publish local avatar state (name, color, position, emotion)
  - Receive and cache peer avatar states
  - Join / leave notifications

Subject layout:
  room.<room_id>.avatar   — avatar state broadcast (position, name, color, emotion)
  room.<room_id>.join     — client joined
  room.<room_id>.leave    — client left
  room.<room_id>.chat     — chat message

This is intentionally simpler than engine/network.py — no LLM adjudication,
no relationship sync. It's the visual/social layer only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

BROADCAST_INTERVAL = 0.5   # seconds between avatar state broadcasts


class RoomNetwork:
    """
    Async NATS client running in a dedicated daemon thread.
    Thread-safe: all public methods can be called from the Ursina main thread.
    """

    def __init__(
        self,
        url: str,
        client_id: str,
        room_id: str = "global",
        on_peer_update: Callable[[str, dict], None] | None = None,
        on_peer_leave:  Callable[[str], None]          | None = None,
        on_chat:        Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.client_id = client_id
        self.room_id   = room_id

        self._url   = url
        self._nc    = None
        self._loop  = asyncio.new_event_loop()
        self._ready = threading.Event()

        # Latest state of each peer: client_id → state dict
        self._peers: dict[str, dict] = {}
        self._lock  = threading.Lock()

        # Callbacks — called on NATS thread, must be thread-safe
        self._on_peer_update = on_peer_update
        self._on_peer_leave  = on_peer_leave
        self._on_chat        = on_chat

        self._connected = False
        self._thread    = threading.Thread(target=self._run, daemon=True, name="room-nats")
        self._thread.start()
        if not self._ready.wait(timeout=8.0):
            logger.warning("[RoomNet] NATS not reachable — running offline")

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    def publish_avatar(self, state: dict) -> None:
        """Broadcast this client's avatar state to the room."""
        payload = {"client_id": self.client_id, "room": self.room_id, **state}
        self._fire(self._publish(f"room.{self.room_id}.avatar", payload))

    def send_chat(self, name: str, message: str) -> None:
        payload = {"client_id": self.client_id, "name": name, "message": message}
        self._fire(self._publish(f"room.{self.room_id}.chat", payload))

    def get_peers(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._peers)

    def disconnect(self) -> None:
        self._fire(self._do_leave())

    # ── Async internals ────────────────────────────────────────────────────────

    def _fire(self, coro) -> None:
        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start())

    async def _start(self) -> None:
        try:
            import nats
            self._nc = await nats.connect(
                self._url,
                name=f"ursina-{self.client_id[:8]}",
                max_reconnect_attempts=3,
            )
            self._connected = True
        except Exception as exc:
            logger.warning("[RoomNet] Could not connect to NATS: %s", exc)
            self._ready.set()
            return

        await self._nc.subscribe(f"room.{self.room_id}.avatar", cb=self._on_avatar)
        await self._nc.subscribe(f"room.{self.room_id}.leave",  cb=self._on_leave_msg)
        await self._nc.subscribe(f"room.{self.room_id}.chat",   cb=self._on_chat_msg)

        # Announce arrival
        await self._publish(f"room.{self.room_id}.join",
                            {"client_id": self.client_id})

        self._ready.set()
        logger.info("[RoomNet] Joined room '%s'", self.room_id)

        while True:
            await asyncio.sleep(3600.0)

    async def _publish(self, subject: str, payload: dict) -> None:
        if self._nc and not self._nc.is_closed:
            await self._nc.publish(subject, json.dumps(payload).encode())

    async def _do_leave(self) -> None:
        await self._publish(f"room.{self.room_id}.leave",
                            {"client_id": self.client_id})
        if self._nc:
            await self._nc.drain()

    # ── Message handlers ───────────────────────────────────────────────────────

    async def _on_avatar(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            cid = data.get("client_id", "")
            if not cid or cid == self.client_id:
                return
            with self._lock:
                self._peers[cid] = data
            if self._on_peer_update:
                self._on_peer_update(cid, data)
        except Exception as exc:
            logger.debug("[RoomNet] bad avatar msg: %s", exc)

    async def _on_leave_msg(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            cid  = data.get("client_id", "")
            if not cid or cid == self.client_id:
                return
            with self._lock:
                self._peers.pop(cid, None)
            if self._on_peer_leave:
                self._on_peer_leave(cid)
        except Exception as exc:
            logger.debug("[RoomNet] bad leave msg: %s", exc)

    async def _on_chat_msg(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            cid  = data.get("client_id", "")
            if cid == self.client_id:
                return
            if self._on_chat:
                self._on_chat(
                    data.get("client_id", "?"),
                    data.get("name", "?"),
                    data.get("message", ""),
                )
        except Exception as exc:
            logger.debug("[RoomNet] bad chat msg: %s", exc)
