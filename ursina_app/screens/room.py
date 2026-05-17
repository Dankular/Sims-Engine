"""
ursina_app/screens/room.py — Global multiplayer isometric room.

Layout (Habbo Hotel / Neopets style):
  ┌────────────────────────────────────────────────────────────┐
  │ [Room name + player count]          [Your sim: Alice ●joy] │
  │                                                            │
  │        I S O M E T R I C   R O O M   V I E W              │
  │   (floor tiles, walls, furniture, walking sim avatars)     │
  │                                                            │
  │ ┌─────────────────────────────────────┐  ┌──────────────┐ │
  │ │ Event log (last 6 events, scrolling)│  │  PLAYERS     │ │
  │ │ Alice said hi to Bob                │  │  ● Alice     │ │
  │ │ Carol joined the room               │  │  ● Bob       │ │
  │ └─────────────────────────────────────┘  └──────────────┘ │
  │ [Chat: ________________________] [Send]                    │
  └────────────────────────────────────────────────────────────┘

Controls:
  WASD / Arrow keys — move your avatar
  Click on floor tile — move your avatar there
  Click on another avatar — show info
  T — open chat input
"""
from __future__ import annotations

import math
import time as _time
import uuid
from typing import Callable

from ursina import (
    Entity, Text, Button, InputField, Vec2, Vec3,
    color, camera, window, destroy, scene, time,
    mouse, held_keys,
)
from ursina.prefabs.panel import Panel

from ursina_app.entities.avatar   import SimAvatar, AVATAR_COLORS
from ursina_app.entities.room_builder import build_room, destroy_room
from ursina_app.network           import RoomNetwork

# ── Constants ──────────────────────────────────────────────────────────────────
ROOM_SIZE      = 10
BROADCAST_RATE = 0.4   # seconds between NATS state broadcasts
MAX_LOG_LINES  = 8

# UI palette
_BG_DARK   = color.rgba( 15,  15,  25, 200)
_PANEL_BG  = color.rgba( 25,  25,  45, 220)
_ACCENT    = color.rgb(  90, 160, 255)
_TEXT      = color.white
_TEXT_DIM  = color.rgb(160, 160, 200)
_BTN_COL   = color.rgb( 60, 140, 255)


class GlobalRoomScreen:
    """
    The isometric multiplayer room — entry point after customization.
    """

    def __init__(
        self,
        local_profile: dict,
        local_color:   color,
        nats_url:      str   = "nats://localhost:4222",
        client_id:     str   = "",
    ) -> None:
        self._profile     = local_profile
        self._local_color = local_color
        self._client_id   = client_id or str(uuid.uuid4())
        self._name        = local_profile.get("name", "Player")

        self._entities: list[Entity] = []
        self._room_entities: list[Entity] = []
        self._peer_avatars: dict[str, SimAvatar] = {}  # client_id → avatar
        self._event_log: list[str] = []
        self._last_broadcast = 0.0

        self._world_root = Entity(parent=scene)
        self._ui_root    = Entity(parent=camera.ui)
        self._entities  += [self._world_root, self._ui_root]

        self._setup_camera()
        self._build_room_visuals()
        self._build_local_avatar()
        self._build_ui()
        self._connect_nats(nats_url)

    # ── Camera (isometric) ────────────────────────────────────────────────────

    def _setup_camera(self) -> None:
        camera.orthographic = True
        camera.fov          = 18           # zoom — lower = more zoomed in
        camera.position     = Vec3(ROOM_SIZE / 2, 16, ROOM_SIZE / 2 - 6)
        camera.rotation     = Vec3(45, 0, 0)   # isometric tilt

    # ── Room visuals ──────────────────────────────────────────────────────────

    def _build_room_visuals(self) -> None:
        self._room_entities = build_room(self._world_root)

        # Invisible floor collider for click-to-move
        self._floor_collider = Entity(
            model="quad",
            color=color.clear,
            scale=(ROOM_SIZE, ROOM_SIZE),
            position=Vec3(ROOM_SIZE / 2 - 0.5, 0.02, ROOM_SIZE / 2 - 0.5),
            rotation_x=90,
            collider="box",
            parent=self._world_root,
        )
        self._entities.append(self._floor_collider)

    # ── Local avatar ──────────────────────────────────────────────────────────

    def _build_local_avatar(self) -> None:
        spawn = Vec3(ROOM_SIZE / 2, 0, ROOM_SIZE / 2)
        self._local_avatar = SimAvatar(
            name=self._name,
            avatar_color=self._local_color,
            position=spawn,
            is_local=True,
            parent=self._world_root,
        )
        self._entities.append(self._local_avatar)
        self._add_log(f"You entered the room as {self._name}.")

    # ── UI panels ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        ar = window.aspect_ratio or 1.78

        # ── Top bar ───────────────────────────────────────────────────────
        top_bar = Entity(
            model="quad",
            color=_BG_DARK,
            scale=(2.0, 0.07),
            position=(0, 0.465),
            parent=camera.ui,
        )
        self._entities.append(top_bar)

        self._room_label = Text(
            "✦  Global Room  ✦",
            scale=1.0,
            color=color.rgb(255, 220, 100),
            position=(-0.72, 0.455),
            parent=camera.ui,
        )
        self._entities.append(self._room_label)

        self._player_count_lbl = Text(
            "1 player online",
            scale=0.85,
            color=_TEXT_DIM,
            position=(0.0, 0.455),
            parent=camera.ui,
        )
        self._entities.append(self._player_count_lbl)

        self._own_info = Text(
            f"You:  {self._name}",
            scale=0.85,
            color=_TEXT,
            position=(0.4, 0.455),
            parent=camera.ui,
        )
        self._entities.append(self._own_info)

        # ── Event log (bottom-left) ───────────────────────────────────────
        log_panel = Panel(
            color=_PANEL_BG,
            scale=(0.55, 0.22),
            position=(-0.23, -0.37),
            parent=camera.ui,
        )
        self._entities.append(log_panel)

        lbl = Text("EVENT LOG", scale=0.75, color=_ACCENT,
                   position=(-0.49, -0.265), parent=camera.ui)
        self._entities.append(lbl)

        self._log_text = Text(
            "",
            scale=0.72,
            color=_TEXT_DIM,
            position=(-0.495, -0.285),
            parent=camera.ui,
            wordwrap=55,
        )
        self._entities.append(self._log_text)

        # ── Player list (bottom-right) ────────────────────────────────────
        pl_panel = Panel(
            color=_PANEL_BG,
            scale=(0.22, 0.22),
            position=(0.40, -0.37),
            parent=camera.ui,
        )
        self._entities.append(pl_panel)

        pl_lbl = Text("PLAYERS", scale=0.75, color=_ACCENT,
                      position=(0.295, -0.265), parent=camera.ui)
        self._entities.append(pl_lbl)

        self._players_text = Text(
            f"● {self._name} (you)",
            scale=0.72,
            color=_TEXT,
            position=(0.295, -0.285),
            parent=camera.ui,
            wordwrap=22,
        )
        self._entities.append(self._players_text)

        # ── Chat input (bottom) ───────────────────────────────────────────
        self._chat_field = InputField(
            default_value="Press T to chat...",
            position=(-0.05, -0.455),
            scale=(0.7, 0.045),
            color=color.rgb(30, 30, 50),
            text_color=_TEXT,
            parent=camera.ui,
        )
        self._entities.append(self._chat_field)

        send_btn = Button(
            text="Send",
            scale=(0.10, 0.045),
            position=(0.40, -0.455),
            color=_BTN_COL,
            text_color=_TEXT,
            parent=camera.ui,
            on_click=self._send_chat,
        )
        self._entities.append(send_btn)

        # ── Hint ──────────────────────────────────────────────────────────
        hint = Text(
            "WASD / Arrows to move  •  Click floor to walk there  •  T to chat",
            scale=0.7,
            color=color.rgb(100, 100, 140),
            position=(0.0, -0.487),
            origin=(0, 0),
            parent=camera.ui,
        )
        self._entities.append(hint)

    # ── NATS ──────────────────────────────────────────────────────────────────

    def _connect_nats(self, url: str) -> None:
        try:
            self._network = RoomNetwork(
                url=url,
                client_id=self._client_id,
                room_id="global",
                on_peer_update=self._on_peer_update,
                on_peer_leave=self._on_peer_leave,
                on_chat=self._on_chat_received,
            )
            if self._network.connected:
                self._add_log("Connected to NATS — global room is live.")
            else:
                self._add_log("NATS offline — running solo.")
        except Exception as e:
            self._network = None
            self._add_log("Could not reach NATS server — solo mode.")

    def _broadcast_state(self) -> None:
        if not self._network or not self._network.connected:
            return
        av = self._local_avatar
        self._network.publish_avatar({
            "name":     self._name,
            "color_idx": AVATAR_COLORS.index(self._local_color)
                          if self._local_color in AVATAR_COLORS else 0,
            "position": [round(av.x, 2), round(av.z, 2)],
            "emotion":  "neutral",
            "aspiration": self._profile.get("aspiration", "Fortune"),
        })

    # ── Peer callbacks (called from NATS thread) ───────────────────────────────

    def _on_peer_update(self, client_id: str, data: dict) -> None:
        """NATS thread → update peer avatar position/state."""
        # Store for processing on main thread via _apply_peer_updates
        self._pending_peer_updates = getattr(self, "_pending_peer_updates", {})
        self._pending_peer_updates[client_id] = data

    def _on_peer_leave(self, client_id: str) -> None:
        self._pending_peer_leaves = getattr(self, "_pending_peer_leaves", set())
        self._pending_peer_leaves.add(client_id)

    def _on_chat_received(self, client_id: str, name: str, message: str) -> None:
        self._pending_chats = getattr(self, "_pending_chats", [])
        self._pending_chats.append((name, message))

    def _apply_peer_updates(self) -> None:
        """Called on main thread every frame to sync NATS peer data."""
        updates = getattr(self, "_pending_peer_updates", {})
        leaves  = getattr(self, "_pending_peer_leaves", set())
        chats   = getattr(self, "_pending_chats", [])

        for client_id, data in updates.items():
            pos = data.get("position", [5, 5])
            col_idx = data.get("color_idx", 0)
            name    = data.get("name", "?")
            emotion = data.get("emotion", "neutral")
            col     = AVATAR_COLORS[col_idx % len(AVATAR_COLORS)]

            if client_id not in self._peer_avatars:
                # New peer — create avatar
                av = SimAvatar(
                    name=name,
                    avatar_color=col,
                    position=Vec3(pos[0], 0, pos[1]),
                    is_local=False,
                    parent=self._world_root,
                )
                self._peer_avatars[client_id] = av
                self._add_log(f"{name} joined the room.")
            else:
                av = self._peer_avatars[client_id]
                av.move_to(Vec3(pos[0], 0, pos[1]))
                av.set_emotion(emotion)

        for client_id in leaves:
            if client_id in self._peer_avatars:
                av = self._peer_avatars.pop(client_id)
                name = av.sim_name
                av.destroy_all()
                self._add_log(f"{name} left the room.")

        for name, message in chats:
            self._add_log(f"[chat] {name}: {message}")

        if updates:
            self._pending_peer_updates.clear()
        if leaves:
            self._pending_peer_leaves.clear()
        if chats:
            self._pending_chats.clear()

        # Update player list
        self._refresh_player_list()

    # ── Chat ──────────────────────────────────────────────────────────────────

    def _send_chat(self) -> None:
        msg = (self._chat_field.text or "").strip()
        if not msg or msg == "Press T to chat...":
            return
        if self._network and self._network.connected:
            self._network.send_chat(self._name, msg)
        self._add_log(f"[chat] {self._name}: {msg}")
        self._chat_field.text = ""

    # ── Event log ─────────────────────────────────────────────────────────────

    def _add_log(self, line: str) -> None:
        self._event_log.insert(0, line)
        self._event_log = self._event_log[:MAX_LOG_LINES]
        self._log_text.text = "\n".join(reversed(self._event_log))

    # ── Player list ───────────────────────────────────────────────────────────

    def _refresh_player_list(self) -> None:
        lines = [f"● {self._name} (you)"]
        for av in self._peer_avatars.values():
            lines.append(f"● {av.sim_name}")
        self._players_text.text = "\n".join(lines[:10])
        total = 1 + len(self._peer_avatars)
        self._player_count_lbl.text = (
            f"{total} player{'s' if total != 1 else ''} online"
        )

    # ── Ursina update ──────────────────────────────────────────────────────────

    def update(self) -> None:
        # Floor click-to-move
        if mouse.left and mouse.hovered_entity == self._floor_collider:
            wp = mouse.world_point
            self._local_avatar.move_to(Vec3(wp.x, 0, wp.z))

        # T key opens chat
        if input("t") and not self._chat_field.active:
            self._chat_field.active = True

        # Enter sends chat
        if input("enter") and self._chat_field.active:
            self._send_chat()
            self._chat_field.active = False

        # NATS broadcast on interval
        now = _time.monotonic()
        if now - self._last_broadcast >= BROADCAST_RATE:
            self._last_broadcast = now
            self._broadcast_state()

        # Apply pending peer updates (from NATS thread)
        self._apply_peer_updates()

        # Camera follows local avatar (smooth)
        av = self._local_avatar
        target_cam = Vec3(av.x, 16, av.z - 6)
        camera.position = Vec3(
            camera.x + (target_cam.x - camera.x) * time.dt * 3,
            16,
            camera.z + (target_cam.z - camera.z) * time.dt * 3,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def disable(self) -> None:
        if self._network:
            self._network.disconnect()
        for av in self._peer_avatars.values():
            av.destroy_all()
        self._peer_avatars.clear()
        self._local_avatar.destroy_all()
        destroy_room(self._room_entities)
        for e in self._entities:
            destroy(e)
        self._entities.clear()
