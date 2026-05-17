"""
engine/rooms.py — Room ID helpers for the NATS network layer.

Room types (Habbo Hotel model):
  global              — everyone; default room on connect
  friends.<key>       — private room shared by a fixed set of client IDs
  personal.<client>   — your sims only; no cross-client interactions
"""
from __future__ import annotations

GLOBAL_ROOM = "global"


def personal_room(client_id: str) -> str:
    return f"personal.{client_id}"


def friends_room(*client_ids: str) -> str:
    """Stable ID for a friends room — sorted so A+B == B+A."""
    key = "_".join(sorted(client_ids))
    return f"friends.{key}"


def room_label(room_id: str) -> str:
    if room_id == GLOBAL_ROOM:
        return "Global Room"
    if room_id.startswith("personal."):
        return f"Personal Room ({room_id[9:17]}…)"
    if room_id.startswith("friends."):
        return f"Friends Room ({room_id[8:20]}…)"
    return room_id
