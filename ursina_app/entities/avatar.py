"""
ursina_app/entities/avatar.py — SimAvatar Ursina entity.

A blocky, Habbo-Hotel-style avatar:
  - Rounded shadow disc on the ground
  - Body: a slightly rectangular cube in the player's chosen color
  - Head: a smaller cube in a lighter shade, bobbing gently
  - Name tag: white Text always facing the camera
  - Emotion bubble: small colored dot above the name
  - Click → triggers on_click callback (for future interaction menu)
"""
from __future__ import annotations

from ursina import (
    Entity, Text, Vec3, color, lerp, time,
    held_keys, mouse, destroy,
)

# Habbo-style avatar palette — index 0-7
AVATAR_COLORS = [
    color.cyan,
    color.magenta,
    color.orange,
    color.lime,
    color.azure,
    color.yellow,
    color.violet,
    color.pink,
]

# Emotion → small colored dot on name tag
EMOTION_COLORS = {
    "joy":          color.yellow,
    "sadness":      color.azure,
    "anger":        color.red,
    "fear":         color.violet,
    "love":         color.pink,
    "excitement":   color.orange,
    "neutral":      color.white,
    "pride":        color.gold,
    "gratitude":    color.lime,
    "grief":        color.gray,
    "nervousness":  color.orange,
    "optimism":     color.yellow,
    "relief":       color.cyan,
}


class SimAvatar(Entity):
    """
    3D blocky avatar.  Created for both the local player and remote peers.
    is_local=True enables WASD movement.
    """

    def __init__(
        self,
        name: str,
        avatar_color: color = color.cyan,
        position: Vec3 = Vec3(0, 0, 0),
        is_local: bool = False,
        on_click=None,
        **kwargs,
    ):
        super().__init__(
            model="cube",
            color=avatar_color,
            position=position + Vec3(0, 0.5, 0),   # offset up — pivot is center of cube
            scale=Vec3(0.65, 1.0, 0.65),
            collider="box",
            **kwargs,
        )
        self.sim_name   = name
        self.is_local   = is_local
        self._target    = Vec3(position.x, 0, position.z)
        self._move_speed = 4.0
        self._bob_t      = 0.0

        # Shadow disc on ground
        self._shadow = Entity(
            model="circle",
            color=color.black33,
            scale=Vec3(0.7, 0.7, 0.7),
            position=position + Vec3(0, 0.01, 0),
            rotation_x=90,
            parent=self.parent,
        )

        # Head (lighter shade, smaller)
        lighter = color.color(
            avatar_color.h, avatar_color.s * 0.6, min(1.0, avatar_color.v * 1.3)
        )
        self._head = Entity(
            model="cube",
            color=lighter,
            scale=Vec3(0.55, 0.55, 0.55),
            position=self.position + Vec3(0, 0.78, 0),
            parent=self.parent,
        )

        # Name tag
        self._label = Text(
            text=name,
            position=self.position + Vec3(0, 1.4, 0),
            scale=7,
            color=color.white,
            background=True,
            parent=self.parent,
        )
        self._label.background.color = color.black50

        # Emotion bubble dot
        self._emotion_dot = Entity(
            model="circle",
            color=color.yellow,
            scale=Vec3(0.15, 0.15, 0.15),
            position=self.position + Vec3(0.25, 1.55, 0),
            rotation_x=90,
            parent=self.parent,
        )

        if on_click:
            self.on_click = on_click

    # ── Public ─────────────────────────────────────────────────────────────────

    def move_to(self, target: Vec3) -> None:
        """Set a movement target (avatar lerps toward it)."""
        self._target = Vec3(target.x, 0, target.z)

    def set_emotion(self, emotion: str) -> None:
        dot_col = EMOTION_COLORS.get(emotion, color.white)
        self._emotion_dot.color = dot_col

    def set_name(self, name: str) -> None:
        self.sim_name = name
        self._label.text = name

    def set_color(self, col: color) -> None:
        self.color = col
        lighter = color.color(col.h, col.s * 0.6, min(1.0, col.v * 1.3))
        self._head.color = lighter

    # ── Ursina update ──────────────────────────────────────────────────────────

    def update(self) -> None:
        # Smooth movement toward target
        current_xz = Vec3(self.x, 0, self.z)
        target_xz  = Vec3(self._target.x, 0, self._target.z)
        dist = (target_xz - current_xz).length()

        if dist > 0.05:
            direction = (target_xz - current_xz).normalized()
            step = min(dist, self._move_speed * time.dt)
            self.x += direction.x * step
            self.z += direction.z * step
            # Gentle Y bob while walking
            self._bob_t += time.dt * 8
            self.y = 0.5 + abs(0.04 * __import__("math").sin(self._bob_t))
        else:
            self._bob_t = 0
            self.y = 0.5

        # Sync head and label to body position
        self._head.position          = self.position + Vec3(0, 0.78, 0)
        self._label.position         = self.position + Vec3(0, 1.4,  0)
        self._emotion_dot.position   = self.position + Vec3(0.25, 1.55, 0)
        self._shadow.x, self._shadow.z = self.x, self.z

        # WASD movement for local player
        if self.is_local:
            speed = 3.5
            if held_keys["w"] or held_keys["up arrow"]:
                self._target.z += speed * time.dt
            if held_keys["s"] or held_keys["down arrow"]:
                self._target.z -= speed * time.dt
            if held_keys["a"] or held_keys["left arrow"]:
                self._target.x -= speed * time.dt
            if held_keys["d"] or held_keys["right arrow"]:
                self._target.x += speed * time.dt

            # Clamp to room bounds (0..ROOM_SIZE-1)
            self._target.x = max(0.0, min(9.0, self._target.x))
            self._target.z = max(0.0, min(9.0, self._target.z))

    def destroy_all(self) -> None:
        """Clean up all sub-entities."""
        for e in (self._shadow, self._head, self._label, self._emotion_dot):
            destroy(e)
        destroy(self)
