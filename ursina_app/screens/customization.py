"""
ursina_app/screens/customization.py — Sim customization screen.

Layout:
  ┌─────────────────────────────────────────────────────┐
  │              CUSTOMIZE YOUR SIM                     │
  │                                                     │
  │         [Big 3D avatar preview, rotates]            │
  │                                                     │
  │  Name:  [________________________]                  │
  │                                                     │
  │  Colour:  o o o o o o o o                           │
  │                                                     │
  │  Aspiration: [Fortune ▼]                            │
  │                                                     │
  │         [ ENTER GLOBAL ROOM ]                       │
  └─────────────────────────────────────────────────────┘

Calls on_confirm(profile: dict, avatar_color: Color) when the player
clicks the enter button.
"""
from __future__ import annotations

import random
import uuid
from typing import Callable

from ursina import (
    Entity, Text, Button, InputField, Vec2, Vec3,
    color, camera, window, destroy, scene, time,
    mouse,
)
from ursina.prefabs.panel import Panel

from ursina_app.entities.avatar import AVATAR_COLORS, SimAvatar

ASPIRATIONS = ["Fortune", "Family", "Popularity", "Knowledge", "Romance", "Creative"]

_PALE_BG   = color.rgb( 20,  20,  35)
_PANEL_BG  = color.rgb( 35,  35,  55)
_ACCENT    = color.rgb( 90, 160, 255)
_TITLE_COL = color.rgb(255, 220, 100)
_TEXT_COL  = color.white
_BTN_COL   = color.rgb( 60, 140, 255)
_BTN_HOV   = color.rgb( 90, 170, 255)


class CustomizationScreen:
    """
    Full-screen sim customization UI.
    All entities are tracked in self._entities for clean disable().
    """

    def __init__(self, on_confirm: Callable[[dict, color], None]) -> None:
        self._on_confirm  = on_confirm
        self._entities: list[Entity] = []
        self._selected_color_idx = 0
        self._selected_aspiration_idx = 0
        self._color_btns: list[Button] = []
        self._asp_btns:   list[Button] = []
        self._preview:    SimAvatar | None = None
        self._bg_root = Entity(parent=scene)   # world-space root for preview
        self._ui_root = Entity(parent=camera.ui)  # UI-space root for controls
        self._entities += [self._bg_root, self._ui_root]

        self._setup_camera()
        self._build_background()
        self._build_preview()
        self._build_ui()

    # ── Camera ─────────────────────────────────────────────────────────────────

    def _setup_camera(self) -> None:
        camera.orthographic = False
        camera.fov          = 60
        camera.position     = Vec3(0, 0, -8)
        camera.rotation     = Vec3(0, 0, 0)

    # ── Background ─────────────────────────────────────────────────────────────

    def _build_background(self) -> None:
        bg = Entity(
            model="quad",
            color=_PALE_BG,
            scale=(100, 100),
            z=10,
            parent=camera.ui,
        )
        self._entities.append(bg)

        # Decorative floating orbs
        for i in range(8):
            orb = Entity(
                model="sphere",
                color=color.rgba(
                    random.randint(60, 120),
                    random.randint(80, 160),
                    random.randint(160, 255),
                    30,
                ),
                scale=random.uniform(0.3, 1.2),
                position=Vec3(
                    random.uniform(-6, 6),
                    random.uniform(-3, 3),
                    random.uniform(5, 9),
                ),
                parent=self._bg_root,
            )
            self._entities.append(orb)

    # ── Avatar preview ─────────────────────────────────────────────────────────

    def _build_preview(self) -> None:
        preview_root = Entity(
            position=Vec3(-2.5, 0, 0),
            parent=self._bg_root,
        )
        self._entities.append(preview_root)

        self._preview = SimAvatar(
            name="Your Sim",
            avatar_color=AVATAR_COLORS[0],
            position=Vec3(0, -1, 5),
            is_local=False,
            parent=preview_root,
        )
        # Gentle spin
        self._preview_root = preview_root
        self._preview_t    = 0.0

    # ── Controls panel ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:

        # ── Panel background ──────────────────────────────────────────────
        panel = Panel(
            model="quad",
            color=color.rgba(35, 35, 55, 220),
            scale=(0.6, 0.9),
            position=(0.18, 0),
            parent=camera.ui,
        )
        self._entities.append(panel)

        # ── Title ─────────────────────────────────────────────────────────
        title = Text(
            "*  CUSTOMIZE YOUR SIM  *",
            color=_TITLE_COL,
            scale=1.4,
            position=(0.18, 0.38),
            origin=(0, 0),
            parent=camera.ui,
        )
        self._entities.append(title)

        # ── Name input ────────────────────────────────────────────────────
        lbl_name = Text("Name:", color=_TEXT_COL, scale=1.1,
                         position=(0.01, 0.24), parent=camera.ui)
        self._entities.append(lbl_name)

        self._name_field = InputField(
            default_value=self._random_name(),
            position=(0.18, 0.22),
            scale=(0.42, 0.045),
            color=color.rgb(50, 50, 75),
            text_color=_TEXT_COL,
            parent=camera.ui,
        )
        self._entities.append(self._name_field)

        # ── Colour swatches ────────────────────────────────────────────────
        lbl_col = Text("Colour:", color=_TEXT_COL, scale=1.1,
                        position=(0.01, 0.12), parent=camera.ui)
        self._entities.append(lbl_col)

        swatch_x_start = 0.01
        swatch_gap     = 0.058
        for i, col_val in enumerate(AVATAR_COLORS):
            idx = i
            btn = Button(
                model="circle",
                color=col_val,
                scale=(0.044, 0.044 * (window.aspect_ratio or 1.78)),
                position=(swatch_x_start + idx * swatch_gap, 0.09),
                parent=camera.ui,
                on_click=lambda i=idx: self._pick_color(i),
            )
            self._entities.append(btn)
            self._color_btns.append(btn)

        # Highlight selected
        self._swatch_ring = Entity(
            model="circle",
            color=color.white,
            scale=(0.054, 0.054 * (window.aspect_ratio or 1.78)),
            position=(swatch_x_start, 0.09),
            z=-0.01,
            parent=camera.ui,
        )
        self._entities.append(self._swatch_ring)

        # ── Aspiration ────────────────────────────────────────────────────
        lbl_asp = Text("Aspiration:", color=_TEXT_COL, scale=1.1,
                        position=(0.01, -0.02), parent=camera.ui)
        self._entities.append(lbl_asp)

        asp_x_start = 0.01
        asp_gap     = 0.075
        for i, asp in enumerate(ASPIRATIONS):
            idx = i
            btn = Button(
                text=asp,
                scale=(0.07, 0.038),
                position=(asp_x_start + i * asp_gap, -0.06),
                color=color.rgb(50, 70, 110),
                text_color=_TEXT_COL,
                parent=camera.ui,
                on_click=lambda i=idx: self._pick_aspiration(i),
            )
            self._entities.append(btn)
            self._asp_btns.append(btn)

        self._pick_aspiration(0)

        # ── Traits display (auto-generated note) ──────────────────────────
        trait_note = Text(
            "Traits & personality will be generated automatically.",
            color=color.rgb(160, 160, 200),
            scale=0.8,
            position=(0.18, -0.16),
            origin=(0, 0),
            parent=camera.ui,
        )
        self._entities.append(trait_note)

        # ── Enter room button ─────────────────────────────────────────────
        enter_btn = Button(
            text="*  ENTER GLOBAL ROOM  *",
            scale=(0.44, 0.06),
            position=(0.18, -0.28),
            color=_BTN_COL,
            highlight_color=_BTN_HOV,
            text_color=color.white,
            parent=camera.ui,
            on_click=self._confirm,
        )
        enter_btn.text_entity.scale *= 1.1
        self._entities.append(enter_btn)

        # Sub-text
        sub = Text(
            "Join the global room and meet other sims",
            color=color.rgb(140, 160, 200),
            scale=0.85,
            position=(0.18, -0.34),
            origin=(0, 0),
            parent=camera.ui,
        )
        self._entities.append(sub)

    # ── Interaction ────────────────────────────────────────────────────────────

    def _pick_color(self, idx: int) -> None:
        self._selected_color_idx = idx
        col = AVATAR_COLORS[idx]
        if self._preview:
            self._preview.set_color(col)
        swatch_x_start = 0.01
        swatch_gap     = 0.058
        self._swatch_ring.x = swatch_x_start + idx * swatch_gap

    def _pick_aspiration(self, idx: int) -> None:
        self._selected_aspiration_idx = idx
        for i, btn in enumerate(self._asp_btns):
            btn.color = color.rgb(70, 100, 160) if i == idx else color.rgb(50, 70, 110)

    def _confirm(self) -> None:
        name = (self._name_field.text or "").strip() or self._random_name()

        # Build a minimal profile — engine fields the room needs
        aspiration = ASPIRATIONS[self._selected_aspiration_idx]
        avatar_color = AVATAR_COLORS[self._selected_color_idx]

        # Try to generate a full engine profile; fall back to minimal dict
        try:
            from identity.profile_factory import generate_sim_profile
            profile = generate_sim_profile()
            profile["name"] = name
            profile["aspiration"] = aspiration
        except Exception:
            profile = {
                "id": str(uuid.uuid4()),
                "name": name,
                "aspiration": aspiration,
                "traits": [],
                "interests": [],
                "ocean": {"openness": 0.5, "conscientiousness": 0.5,
                          "extraversion": 0.5, "agreeableness": 0.5, "neuroticism": 0.5},
            }

        profile.setdefault("id", str(uuid.uuid4()))
        self._on_confirm(profile, avatar_color)

    @staticmethod
    def _random_name() -> str:
        first = random.choice(["Alex", "Sam", "Jordan", "Taylor", "Morgan",
                                "Casey", "Riley", "Drew", "Quinn", "Avery"])
        last  = random.choice(["Storm", "River", "Moon", "Star", "Blaze",
                                "Frost", "Vale", "Cruz", "West", "Dawn"])
        return f"{first} {last}"

    # ── Ursina update ──────────────────────────────────────────────────────────

    def update(self) -> None:
        """Called by Ursina every frame if this screen is the active one."""
        # Slowly rotate the preview avatar
        if self._preview:
            self._preview_t += time.dt * 0.4
            import math
            self._preview.x = math.sin(self._preview_t) * 0.5 - 2.5
            self._preview.rotation_y = self._preview_t * 57.3  # rad→deg

        # Update preview name from input field
        if self._preview and self._name_field.text:
            self._preview.set_name(self._name_field.text or "Your Sim")

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def disable(self) -> None:
        for e in self._entities:
            destroy(e)
        if self._preview:
            self._preview.destroy_all()
        self._entities.clear()
