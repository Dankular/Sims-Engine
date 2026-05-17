"""
engine/network.py — NATS messaging layer for the distributed Sims Engine.

Subject layout (Habbo Hotel room model):

  room.<room_id>.state              fanout   — periodic sim state broadcast
  room.<room_id>.interact.<sim_id>  req/rep  — cross-client social interaction
  room.<room_id>.relationship       fanout   — friendship/romance delta sync
  room.<room_id>.gossip             fanout   — gossip spread
  room.<room_id>.join               fanout   — client entering the room
  room.<room_id>.leave              fanout   — client leaving the room

Usage:
    net = NATSNetwork(
        url="nats://localhost:4222",
        client_id="abc123",
        owned_sim_ids={"uuid-1", "uuid-2"},
        starting_room="global",
    )
    engine.attach_network(net, "global")

Install:
    pip install nats-py
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
import uuid
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.world_registry import WorldRegistry

logger = logging.getLogger(__name__)


class NATSNetwork:
    """
    Bridges the synchronous SimEngine with a NATS broker.

    Owns a dedicated asyncio event loop running on a daemon thread.
    All public methods are called from the engine (sync) thread and
    schedule coroutines on the NATS loop via run_coroutine_threadsafe.

    Adjudication (serving incoming interaction requests) runs in the
    engine's thread-pool executor so LLM calls don't block the NATS loop.
    """

    def __init__(
        self,
        url: str,
        client_id: str,
        owned_sim_ids: set[str],
        starting_room: str = "global",
    ) -> None:
        self.client_id = client_id
        self.owned_sim_ids: set[str] = set(owned_sim_ids)
        self.current_room = starting_room

        from engine.world_registry import WorldRegistry
        self._registry = WorldRegistry()

        self._url = url
        self._nc = None            # nats.aio.client.Client, set in _start()
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._subs: list = []      # active subscriptions, for clean shutdown

        # Callbacks wired by SimEngine.attach_network()
        self._adjudicate_cb: Callable | None = None    # (sim_id, request) -> dict
        self._relationship_cb: Callable | None = None  # (delta_dict) -> None
        self._gossip_cb: Callable | None = None        # (gossip_dict) -> None
        self._client_left_cb: Callable | None = None   # (client_id) -> None

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="nats-net"
        )
        self._thread.start()
        if not self._ready.wait(timeout=10.0):
            logger.warning("[NATS] Connection did not complete in 10s — offline mode")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def registry(self) -> "WorldRegistry":
        return self._registry

    @property
    def connected(self) -> bool:
        return self._nc is not None and not self._nc.is_closed

    # ── Callback setters (called by engine before the tick loop) ──────────────

    def set_adjudicator(self, fn: Callable) -> None:
        """fn(our_sim_id: str, request_data: dict) -> dict"""
        self._adjudicate_cb = fn

    def set_relationship_handler(self, fn: Callable) -> None:
        """fn(delta: dict) -> None  — apply incoming rel delta to local graph"""
        self._relationship_cb = fn

    def set_gossip_handler(self, fn: Callable) -> None:
        """fn(data: dict) -> None  — apply incoming gossip to local graph"""
        self._gossip_cb = fn

    def set_client_left_handler(self, fn: Callable) -> None:
        """fn(client_id: str) -> None  — prune registry when a peer disconnects"""
        self._client_left_cb = fn

    # ── Public API (engine thread → NATS loop, fire-and-forget) ──────────────

    def publish_states(self, room_id: str, states: list[dict]) -> None:
        payload = {"client_id": self.client_id, "room": room_id, "sims": states}
        self._fire(self._publish(f"room.{room_id}.state", payload))

    def publish_relationship(
        self,
        room_id: str,
        sim_a_id: str,
        sim_b_id: str,
        fd: float,
        rd: float,
        memory_tag: str,
        valence: float,
        tick: int,
    ) -> None:
        payload = {
            "sim_a_id": sim_a_id, "sim_b_id": sim_b_id,
            "friendship_delta": fd, "romance_delta": rd,
            "memory_tag": memory_tag, "valence": valence, "tick": tick,
        }
        self._fire(self._publish(f"room.{room_id}.relationship", payload))

    def publish_gossip(
        self,
        room_id: str,
        spreader_id: str,
        receiver_id: str,
        subject_id: str,
        memory_tag: str,
    ) -> None:
        payload = {
            "spreader_id": spreader_id, "receiver_id": receiver_id,
            "subject_id": subject_id, "memory_tag": memory_tag,
        }
        self._fire(self._publish(f"room.{room_id}.gossip", payload))

    def add_owned_sim(self, sim_id: str) -> None:
        """Register a newly-spawned sim and subscribe to its interaction subject."""
        self.owned_sim_ids.add(sim_id)
        self._fire(self._subscribe_interact(self.current_room, sim_id))

    def join_room(self, room_id: str) -> None:
        old = self.current_room
        self.current_room = room_id
        self._fire(self._do_join_room(room_id))
        if old != room_id:
            self._fire(self._publish(f"room.{old}.leave", {
                "client_id": self.client_id,
                "sim_ids": list(self.owned_sim_ids),
            }))

    def shutdown(self) -> None:
        self._fire(self._close())

    # ── Blocking request-reply (engine thread waits for LLM result) ───────────

    def request_interaction(
        self,
        room_id: str,
        sim_a_state: dict,
        target_sim_id: str,
        action: str,
        venue: dict,
        tick: int,
        timeout: float = 25.0,
    ) -> dict | None:
        """
        Publish an interaction request to the target sim's owner and block
        until the adjudicated result arrives (or timeout).
        Returns the result dict, or None on timeout/error.
        """
        bridge: concurrent.futures.Future = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(
            self._async_request(
                room_id, sim_a_state, target_sim_id, action, venue, tick, bridge
            ),
            self._loop,
        )
        try:
            return bridge.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "[NATS] interact request to %s timed out after %.0fs",
                target_sim_id[:8], timeout,
            )
            return None
        except Exception as exc:
            logger.warning("[NATS] interact request failed: %s", exc)
            return None

    # ── Async internals ────────────────────────────────────────────────────────

    def _fire(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start())

    async def _start(self) -> None:
        try:
            import nats
            self._nc = await nats.connect(
                self._url,
                name=f"sims-{self.client_id[:8]}",
                reconnect_time_wait=2,
                max_reconnect_attempts=-1,
                error_cb=self._on_error,
                disconnected_cb=self._on_disconnected,
                reconnected_cb=self._on_reconnected,
            )
            logger.info(
                "[NATS] Connected → %s  client=%s", self._url, self.client_id[:8]
            )
        except Exception as exc:
            logger.error("[NATS] Connection failed: %s", exc)
            self._ready.set()
            return

        await self._do_join_room(self.current_room)
        self._ready.set()

        # Keep the async loop alive indefinitely
        while True:
            await asyncio.sleep(3600.0)

    async def _do_join_room(self, room_id: str) -> None:
        if not self._nc:
            return

        sub_state = await self._nc.subscribe(
            f"room.{room_id}.state", cb=self._on_state
        )
        sub_rel = await self._nc.subscribe(
            f"room.{room_id}.relationship", cb=self._on_relationship
        )
        sub_gossip = await self._nc.subscribe(
            f"room.{room_id}.gossip", cb=self._on_gossip
        )
        sub_leave = await self._nc.subscribe(
            f"room.{room_id}.leave", cb=self._on_leave
        )
        self._subs += [sub_state, sub_rel, sub_gossip, sub_leave]

        for sim_id in self.owned_sim_ids:
            await self._subscribe_interact(room_id, sim_id)

        await self._publish(f"room.{room_id}.join", {
            "client_id": self.client_id,
            "sim_ids": list(self.owned_sim_ids),
        })
        logger.info("[NATS] Joined room '%s'  sims=%d", room_id, len(self.owned_sim_ids))

    async def _subscribe_interact(self, room_id: str, sim_id: str) -> None:
        if not self._nc:
            return
        sub = await self._nc.subscribe(
            f"room.{room_id}.interact.{sim_id}",
            cb=self._on_interaction_request,
        )
        self._subs.append(sub)

    async def _publish(self, subject: str, payload: dict) -> None:
        if self._nc and not self._nc.is_closed:
            await self._nc.publish(subject, json.dumps(payload).encode())

    async def _async_request(
        self,
        room_id: str,
        sim_a_state: dict,
        target_sim_id: str,
        action: str,
        venue: dict,
        tick: int,
        bridge: concurrent.futures.Future,
    ) -> None:
        try:
            payload = json.dumps({
                "interaction_id": uuid.uuid4().hex[:8],
                "initiator_sim_id": sim_a_state["id"],
                "initiator_state": sim_a_state,
                "action": action,
                "venue": venue,
                "tick": tick,
            }).encode()
            response = await self._nc.request(
                f"room.{room_id}.interact.{target_sim_id}",
                payload,
                timeout=24.0,
            )
            bridge.set_result(json.loads(response.data))
        except Exception as exc:
            if not bridge.done():
                bridge.set_exception(exc)

    async def _close(self) -> None:
        for sub in self._subs:
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        if self._nc:
            await self._nc.drain()
            await self._nc.close()

    # ── Message handlers (run on NATS async loop) ─────────────────────────────

    async def _on_state(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            if data.get("client_id") == self.client_id:
                return  # ignore own broadcasts
            self._registry.update_states(
                data["client_id"],
                data.get("room", self.current_room),
                data.get("sims", []),
            )
        except Exception as exc:
            logger.debug("[NATS] bad state msg: %s", exc)

    async def _on_interaction_request(self, msg) -> None:
        """
        A remote sim wants to interact with one of our local sims.
        Adjudicate synchronously (LLM call), then reply.
        The subject tail is our sim's ID: room.<r>.interact.<our_sim_id>
        """
        try:
            data = json.loads(msg.data)
            our_sim_id = msg.subject.rsplit(".", 1)[-1]

            if self._adjudicate_cb is None:
                await msg.respond(json.dumps({"error": "no adjudicator"}).encode())
                return

            # Run the blocking LLM call in the executor so the async loop is free
            result = await self._loop.run_in_executor(
                None, self._adjudicate_cb, our_sim_id, data
            )
            await msg.respond(json.dumps(result or {}).encode())
            logger.info(
                "[NATS] served interact  sim=%s  ← %s  action=%s",
                our_sim_id[:8],
                data.get("initiator_sim_id", "?")[:8],
                data.get("action", "?"),
            )
        except Exception as exc:
            logger.warning("[NATS] interaction serve error: %s", exc)
            try:
                await msg.respond(json.dumps({"error": str(exc)}).encode())
            except Exception:
                pass

    async def _on_relationship(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            if self._relationship_cb:
                self._relationship_cb(data)
        except Exception as exc:
            logger.debug("[NATS] bad rel msg: %s", exc)

    async def _on_gossip(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            if self._gossip_cb:
                self._gossip_cb(data)
        except Exception as exc:
            logger.debug("[NATS] bad gossip msg: %s", exc)

    async def _on_leave(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            client_id = data.get("client_id", "")
            if client_id and client_id != self.client_id:
                self._registry.remove_client(client_id)
                logger.info("[NATS] client %s left", client_id[:8])
                if self._client_left_cb:
                    self._client_left_cb(client_id)
        except Exception as exc:
            logger.debug("[NATS] bad leave msg: %s", exc)

    async def _on_error(self, exc) -> None:
        logger.warning("[NATS] error: %s", exc)

    async def _on_disconnected(self) -> None:
        logger.warning("[NATS] disconnected — will reconnect automatically")

    async def _on_reconnected(self) -> None:
        logger.info("[NATS] reconnected to %s", self._url)
