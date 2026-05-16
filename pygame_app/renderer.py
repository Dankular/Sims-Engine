"""
pygame_app/renderer.py — All pygame drawing logic.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pygame

from pygame_app import colors as C

if TYPE_CHECKING:
    from pygame_app.game import Game

# Layout constants
HUD_H       = 44
CARD_W      = 310
CARD_PAD    = 8
BOTTOM_H    = 220
GRAPH_X     = CARD_W + 1
GRAPH_Y     = HUD_H
STORY_W     = 400

NEED_NAMES  = ["hunger", "energy", "social", "fun", "hygiene", "comfort"]
NEED_SHORT  = ["HUN", "ENE", "SOC", "FUN", "HYG", "COM"]

EMOTION_EMOJI = {
    "joy": "😄", "love": "❤", "excitement": "★", "admiration": "✦",
    "amusement": "HA", "gratitude": "♥", "optimism": "☀", "pride": "↑",
    "relief": "~", "approval": "✓", "caring": "♡", "curiosity": "?",
    "surprise": "!", "neutral": "·", "sadness": "↓", "grief": "↓↓",
    "disappointment": ":(", "remorse": "✗", "anger": "⚡", "annoyance": "~!",
    "disgust": "≠", "disapproval": "✗", "embarrassment": "//", "fear": "!!",
    "nervousness": "~~", "discomfort": "≈", "confusion": "??",
}


class Renderer:
    def __init__(self, surface: pygame.Surface):
        self.surf = surface
        self.W, self.H = surface.get_size()
        self._init_fonts()
        self._node_positions: dict[str, tuple[int, int]] = {}
        self._card_rects: dict[str, pygame.Rect] = {}  # sim_id → card rect for click

    def _init_fonts(self) -> None:
        def sf(name, size):
            return pygame.font.SysFont(name, size)
        # Try nice fonts, fallback to any available
        for fname in ["Segoe UI", "Arial", "DejaVu Sans", None]:
            try:
                self.font_sm   = sf(fname, 13)
                self.font_md   = sf(fname, 15)
                self.font_lg   = sf(fname, 18)
                self.font_xl   = sf(fname, 22)
                self.font_hud  = sf(fname, 14)
                break
            except Exception:
                continue

    # ── Top-level draw ────────────────────────────────────────────────────────

    def draw(self, game: "Game") -> None:
        self.surf.fill(C.BG)
        state = game._state

        self._draw_hud(game, state)
        self._draw_sim_cards(game, state)
        self._draw_world_graph(game, state)
        self._draw_event_log(game, state)
        self._draw_story_panel(game, state)

    # ── HUD ───────────────────────────────────────────────────────────────────

    def _draw_hud(self, game: "Game", state: dict) -> None:
        pygame.draw.rect(self.surf, C.HUD_BG, (0, 0, self.W, HUD_H))
        pygame.draw.line(self.surf, C.BORDER, (0, HUD_H), (self.W, HUD_H), 1)

        tick  = state.get("tick", 0)
        hour  = state.get("hour", 0)
        tlbl  = state.get("time_label", "")
        venue = state.get("venue", {}).get("name", "?")
        speed = game.speed
        pause = "  ⏸ PAUSED" if game.paused else ""

        parts = [
            ("SIMS ENGINE", C.TEXT_GOLD, self.font_xl),
            (f"   Tick {tick:04d}", C.TEXT, self.font_hud),
            (f"  |  {hour:02d}:00  {tlbl}", C.TEXT_DIM, self.font_hud),
            (f"  |  {venue}", C.TEXT, self.font_hud),
            (f"  |  {speed:.2g}×{pause}", C.ACCENT if not game.paused else C.NEED_LOW, self.font_hud),
        ]

        controls = "SPACE=pause  N=tick  +/-=speed  S=story  ESC=quit"
        ctrl_surf = self.font_sm.render(controls, True, C.TEXT_DIM)
        self.surf.blit(ctrl_surf, (self.W - ctrl_surf.get_width() - 10, 14))

        x = 12
        for text, colour, font in parts:
            s = font.render(text, True, colour)
            self.surf.blit(s, (x, (HUD_H - s.get_height()) // 2))
            x += s.get_width()

    # ── Sim cards ─────────────────────────────────────────────────────────────

    def _draw_sim_cards(self, game: "Game", state: dict) -> None:
        sims = state.get("sims", [])
        area_h = self.H - HUD_H - BOTTOM_H
        card_h = max(140, min(200, area_h // max(1, len(sims))))

        self._card_rects.clear()
        for i, sim in enumerate(sims):
            y = HUD_H + i * card_h
            if y + card_h > self.H - BOTTOM_H:
                break
            rect = pygame.Rect(CARD_PAD, y + CARD_PAD, CARD_W - CARD_PAD * 2, card_h - CARD_PAD)
            self._card_rects[sim["id"]] = rect
            selected = (sim["id"] == game.selected_sim_id)
            self._draw_sim_card(sim, rect, selected)

        # Panel border
        pygame.draw.line(self.surf, C.BORDER, (CARD_W, HUD_H), (CARD_W, self.H - BOTTOM_H), 1)

    def _draw_sim_card(self, sim: dict, rect: pygame.Rect, selected: bool) -> None:
        bg  = C.PANEL_SEL if selected else C.PANEL
        bdr = C.BORDER_SEL if selected else C.BORDER
        pygame.draw.rect(self.surf, bg, rect, border_radius=6)
        pygame.draw.rect(self.surf, bdr, rect, 1, border_radius=6)

        x0, y0 = rect.x + 10, rect.y + 8

        # Avatar circle + emotion
        emotion = sim.get("emotion", "neutral")
        valence = sim.get("valence", 0.5)
        node_c  = C.VALENCE_POS if valence > 0.6 else C.VALENCE_NEG if valence < 0.4 else C.VALENCE_NEU
        pygame.draw.circle(self.surf, node_c, (x0 + 16, y0 + 16), 16)
        emo_sym = EMOTION_EMOJI.get(emotion, "·")
        es = self.font_sm.render(emo_sym[:2], True, C.WHITE)
        self.surf.blit(es, (x0 + 16 - es.get_width() // 2, y0 + 16 - es.get_height() // 2))

        # Name + job
        tx = x0 + 38
        name_s = self.font_md.render(sim["name"], True, C.TEXT_BRIGHT)
        self.surf.blit(name_s, (tx, y0))
        job_s = self.font_sm.render(f"{sim['job']}  §{sim['simoleons']:.0f}", True, C.TEXT_DIM)
        self.surf.blit(job_s, (tx, y0 + 17))

        # LOD + career
        lod = sim.get("lod_tier", "ACTIVE")
        lod_c = C.LOD_ACTIVE if lod == "ACTIVE" else C.LOD_BG_NODE if lod == "BACKGROUND" else C.LOD_DORMANT
        lod_s = self.font_sm.render(f"LOD:{lod[:3]}  perf:{sim.get('career_performance',0):.0f}", True, lod_c)
        self.surf.blit(lod_s, (tx, y0 + 32))

        # Parent info
        if sim.get("parent_ids"):
            pid_s = self.font_sm.render(f"child of: {sim['parent_ids'][0][:8]}…", True, C.TEXT_DIM)
            self.surf.blit(pid_s, (x0, y0 + 50))

        # Needs bars
        ny = y0 + (55 if sim.get("parent_ids") else 50)
        bar_w = (rect.width - 20) // len(NEED_NAMES)
        needs  = sim.get("needs", {})
        for j, (need, short) in enumerate(zip(NEED_NAMES, NEED_SHORT)):
            val = needs.get(need, 0)
            bx  = x0 + j * bar_w
            colour = C.NEED_OK if val >= 65 else C.NEED_LOW if val >= 35 else C.NEED_CRIT
            pygame.draw.rect(self.surf, C.NEED_BG, (bx, ny + 12, bar_w - 2, 8), border_radius=2)
            pygame.draw.rect(self.surf, colour, (bx, ny + 12, int((bar_w - 2) * val / 100), 8), border_radius=2)
            lbl = self.font_sm.render(short, True, colour)
            self.surf.blit(lbl, (bx, ny))

        # Wants
        wy = ny + 26
        wants = sim.get("active_wants", [])[:2]
        for want in wants:
            ws = self.font_sm.render(f"• {want[:38]}", True, C.TEXT_DIM)
            self.surf.blit(ws, (x0, wy))
            wy += 15

    # ── World graph ───────────────────────────────────────────────────────────

    def _draw_world_graph(self, game: "Game", state: dict) -> None:
        sims = state.get("sims", [])
        rels = state.get("relationships", [])
        area_w = self.W - CARD_W - STORY_W
        area_h = self.H - HUD_H - BOTTOM_H
        cx = GRAPH_X + area_w // 2
        cy = GRAPH_Y + area_h // 2
        radius = min(area_w, area_h) // 2 - 50

        # Position nodes in a circle
        self._node_positions.clear()
        n = len(sims)
        for i, sim in enumerate(sims):
            angle = -math.pi / 2 + 2 * math.pi * i / max(1, n)
            nx = int(cx + radius * math.cos(angle))
            ny = int(cy + radius * math.sin(angle))
            self._node_positions[sim["id"]] = (nx, ny)

        # Draw relationship lines
        sim_map = {s["id"]: s for s in sims}
        for rel in rels:
            aid, bid = rel["sim_a"], rel["sim_b"]
            if aid not in self._node_positions or bid not in self._node_positions:
                continue
            ax, ay = self._node_positions[aid]
            bx, by = self._node_positions[bid]
            f = rel.get("friendship", 0)
            r = rel.get("romance", 0)
            if r > 20:
                colour = C.REL_ROMANCE
                width = max(1, int(r / 20))
            elif f > 0:
                colour = C.REL_FRIEND
                width = max(1, int(f / 20))
            elif f < 0:
                colour = C.REL_ENEMY
                width = max(1, int(abs(f) / 20))
            else:
                colour = C.REL_NEUTRAL
                width = 1
            pygame.draw.line(self.surf, colour, (ax, ay), (bx, by), min(width, 6))

            # Relationship label at midpoint
            mx, my = (ax + bx) // 2, (ay + by) // 2
            state_lbl = rel.get("state", "")
            if state_lbl and state_lbl != "strangers":
                ls = self.font_sm.render(state_lbl, True, colour)
                self.surf.blit(ls, (mx - ls.get_width() // 2, my - 8))

        # Draw nodes
        for sim in sims:
            if sim["id"] not in self._node_positions:
                continue
            nx, ny = self._node_positions[sim["id"]]
            lod = sim.get("lod_tier", "ACTIVE")
            node_c = C.LOD_ACTIVE if lod == "ACTIVE" else C.LOD_BG_NODE if lod == "BACKGROUND" else C.LOD_DORMANT
            selected = (sim["id"] == game.selected_sim_id)
            r = 22 if selected else 18
            pygame.draw.circle(self.surf, C.PANEL, (nx, ny), r + 2)
            pygame.draw.circle(self.surf, node_c, (nx, ny), r)
            if selected:
                pygame.draw.circle(self.surf, C.BORDER_SEL, (nx, ny), r + 3, 2)

            # Initials
            parts = sim["name"].split()
            initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()
            init_s = self.font_md.render(initials, True, C.WHITE)
            self.surf.blit(init_s, (nx - init_s.get_width() // 2, ny - init_s.get_height() // 2))

            # Name below node
            name_s = self.font_sm.render(sim["name"].split()[0], True, C.TEXT)
            self.surf.blit(name_s, (nx - name_s.get_width() // 2, ny + r + 3))

            # Emotion dot
            valence = sim.get("valence", 0.5)
            dot_c = C.VALENCE_POS if valence > 0.6 else C.VALENCE_NEG if valence < 0.4 else C.VALENCE_NEU
            pygame.draw.circle(self.surf, dot_c, (nx + r - 4, ny - r + 4), 5)

            # Child indicator: draw small arc to parent node if parent visible
            for pid in sim.get("parent_ids", []):
                if pid in self._node_positions:
                    px, py = self._node_positions[pid]
                    pygame.draw.line(self.surf, C.TEXT_DIM, (nx, ny), (px, py), 1)

        # Venue label
        venue = state.get("venue", {}).get("name", "")
        ambient = state.get("venue", {}).get("ambient_sound", "")
        label = f"📍 {venue}"
        if ambient:
            label += f"  🔊 {ambient}"
        vl = self.font_sm.render(label, True, C.TEXT_DIM)
        self.surf.blit(vl, (GRAPH_X + 10, GRAPH_Y + 6))

        # Separator
        pygame.draw.line(self.surf, C.BORDER,
                         (self.W - STORY_W, HUD_H),
                         (self.W - STORY_W, self.H - BOTTOM_H), 1)

    # ── Event log ─────────────────────────────────────────────────────────────

    def _draw_event_log(self, game: "Game", state: dict) -> None:
        y0 = self.H - BOTTOM_H
        log_w = self.W - STORY_W
        pygame.draw.line(self.surf, C.BORDER, (0, y0), (self.W, y0), 1)
        pygame.draw.rect(self.surf, C.HUD_BG, (0, y0, log_w, BOTTOM_H))

        hdr = self.font_sm.render("EVENT LOG", True, C.ACCENT)
        self.surf.blit(hdr, (10, y0 + 6))

        y = y0 + 26
        row_h = 18
        text_x = 70
        max_text_w = log_w - text_x - 10

        for entry in game._event_log:
            if y + row_h > self.H - 4:
                break
            tick_s = self.font_sm.render(f"[{entry['tick']:04d}]", True, C.TEXT_DIM)
            icon_s = self.font_sm.render(entry["icon"], True, C.TEXT)
            self.surf.blit(tick_s, (8, y))
            self.surf.blit(icon_s, (52, y))

            # Truncate text to fit available width with ellipsis
            text = entry["text"]
            text_s = self.font_sm.render(text, True, C.TEXT)
            if text_s.get_width() > max_text_w:
                while text and self.font_sm.size(text + "…")[0] > max_text_w:
                    text = text[:-1]
                text += "…"
                text_s = self.font_sm.render(text, True, C.TEXT)
            self.surf.blit(text_s, (text_x, y))
            y += row_h

        pygame.draw.line(self.surf, C.BORDER, (log_w, y0), (log_w, self.H), 1)

    # ── Story panel ───────────────────────────────────────────────────────────

    def _draw_story_panel(self, game: "Game", state: dict) -> None:
        x0 = self.W - STORY_W
        y0 = self.H - BOTTOM_H
        pygame.draw.rect(self.surf, C.STORY_BG, (x0, y0, STORY_W, BOTTOM_H))

        hdr = self.font_sm.render("STORY", True, C.ACCENT)
        self.surf.blit(hdr, (x0 + 10, y0 + 6))

        y = y0 + 26
        row_h = 17
        for seg in game._story_segments:
            if y + row_h > self.H - 4:
                break
            speaker = seg.get("speaker", "narrator")
            text = seg.get("text", "")
            colour = C.NARRATOR_C if speaker == "narrator" else C.DIALOGUE_C
            prefix = "📣" if speaker == "narrator" else f"💬 {speaker[:10]}:"
            px = self.font_sm.render(prefix, True, colour)
            self.surf.blit(px, (x0 + 8, y))
            # Word-wrap text into STORY_W - prefix width
            wrap_x = x0 + 8 + px.get_width() + 4
            words = text.split()
            line = ""
            first = True
            for word in words:
                test = (line + " " + word).strip()
                ts = self.font_sm.render(test, True, colour)
                if ts.get_width() > STORY_W - (wrap_x - x0) - 8 and line:
                    ls = self.font_sm.render(line, True, colour)
                    self.surf.blit(ls, (wrap_x if first else x0 + 12, y))
                    y += row_h
                    if y + row_h > self.H - 4:
                        break
                    line = word
                    first = False
                else:
                    line = test
            if line:
                ls = self.font_sm.render(line, True, colour)
                self.surf.blit(ls, (wrap_x if first else x0 + 12, y))
            y += row_h + 4

    # ── Click handling ────────────────────────────────────────────────────────

    def handle_click(self, pos: tuple[int, int], game: "Game") -> None:
        for sim_id, rect in self._card_rects.items():
            if rect.collidepoint(pos):
                game.selected_sim_id = sim_id if game.selected_sim_id != sim_id else None
                return
        # Click on graph node
        for sim_id, (nx, ny) in self._node_positions.items():
            dx, dy = pos[0] - nx, pos[1] - ny
            if dx * dx + dy * dy <= 22 * 22:
                game.selected_sim_id = sim_id if game.selected_sim_id != sim_id else None
                return
