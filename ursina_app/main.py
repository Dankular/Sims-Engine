"""
ursina_app/main.py — Ursina 3D frontend entry point.

Usage:
    python -m ursina_app                         # no NATS (solo)
    python -m ursina_app --nats nats://localhost:4222
    python ursina_app/main.py --nats nats://host:4222

Screens:
  1. CustomizationScreen — name, colour, aspiration
  2. GlobalRoomScreen    — isometric multiplayer room via NATS
"""
from __future__ import annotations

import sys
import uuid

from ursina import Ursina, window, color, camera

# Parse CLI args before Ursina initialises (it consumes sys.argv)
_args = sys.argv[1:]
_nats_url  = "nats://localhost:4222"
_room_id   = "global"
_client_id = str(uuid.uuid4())

for i, a in enumerate(_args):
    if a == "--nats"      and i + 1 < len(_args): _nats_url  = _args[i + 1]
    if a == "--room"      and i + 1 < len(_args): _room_id   = _args[i + 1]
    if a == "--client-id" and i + 1 < len(_args): _client_id = _args[i + 1]

# ── App setup ─────────────────────────────────────────────────────────────────

app = Ursina(
    title        = "Sims Engine — Global Room",
    borderless   = False,
    fullscreen   = False,
    size         = (1280, 720),
    development_mode = False,
)
window.color = color.rgb(15, 15, 25)
window.fps_counter.enabled   = True
window.cog_button.enabled    = False
window.exit_button.visible   = True

# ── Screen manager ────────────────────────────────────────────────────────────

_current_screen = None

from ursina_app.screens.customization import CustomizationScreen
from ursina_app.screens.room          import GlobalRoomScreen


def _show_customization() -> None:
    global _current_screen
    if _current_screen:
        _current_screen.disable()
    _current_screen = CustomizationScreen(on_confirm=_show_room)


def _show_room(profile: dict, avatar_color: color) -> None:
    global _current_screen
    if _current_screen:
        _current_screen.disable()
    _current_screen = GlobalRoomScreen(
        local_profile = profile,
        local_color   = avatar_color,
        nats_url      = _nats_url,
        client_id     = _client_id,
    )


# ── Ursina update hook ────────────────────────────────────────────────────────

def update() -> None:
    if _current_screen and hasattr(_current_screen, "update"):
        _current_screen.update()


# ── Boot into customization screen ────────────────────────────────────────────

_show_customization()
app.run()
