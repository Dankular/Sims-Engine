"""
pygame_app/renderer.py — Information-rich dashboard renderer.

Layout (1600×900):
  HUD bar          : y=0    h=44
  ┌──────────┬──────────────────┬──────────┬──────────┐
  │ Sim      │  Social graph    │ Live     │ Selected │
  │ Roster   │                  │ Feed     │ Detail   │
  │ 340px    │  600px           │ 360px    │ 300px    │
  │          │                  │          │          │
  ├──────────┴──────────────────┴──────────┴──────────┤
  │ Relationships  │  Valence timeline  │ Model trace │
  │ 530px          │  600px             │ 470px       │
  └────────────────────────────────────────────────────┘
  bottom strip     : h=188
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pygame

from pygame_app import colors as C

if TYPE_CHECKING:
    from pygame_app.game import Game

# ── Layout constants ──────────────────────────────────────────────────────────
W, H          = 1600, 900
HUD_H         = 44
BOT_H         = 188
CONTENT_H     = H - HUD_H - BOT_H          # 668

ROSTER_W      = 340
GRAPH_W       = 600
FEED_W        = 360
DETAIL_W      = W - ROSTER_W - GRAPH_W - FEED_W   # 300

ROSTER_X      = 0
GRAPH_X       = ROSTER_W
FEED_X        = ROSTER_W + GRAPH_W
DETAIL_X      = FEED_X + FEED_W
CONTENT_Y     = HUD_H
BOT_Y         = HUD_H + CONTENT_H

REL_PANEL_W   = 530
VAL_PANEL_W   = 600
MODEL_PANEL_W = W - REL_PANEL_W - VAL_PANEL_W   # 470

NEED_NAMES    = ["hunger", "energy", "social", "fun", "hygiene", "bladder", "comfort"]
NEED_SHORT    = ["HUN",    "ENE",    "SOC",    "FUN", "HYG",     "BLD",     "COM"]
OCEAN_KEYS    = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
OCEAN_SHORT   = ["O", "C", "E", "A", "N"]

EMOTION_SYMBOL = {
    "joy": "♦", "love": "♥", "excitement": "★", "admiration": "✦",
    "amusement": "☺", "gratitude": "♡", "optimism": "☀", "pride": "↑",
    "relief": "≈", "approval": "✓", "caring": "❤", "curiosity": "?",
    "surprise": "!", "realization": "◉", "desire": "◈", "neutral": "·",
    "sadness": "▼", "grief": "▽", "disappointment": "↘", "remorse": "✗",
    "anger": "⚡", "annoyance": "~", "disgust": "≠", "disapproval": "✗",
    "embarrassment": "//", "fear": "!!", "nervousness": "~~", "confusion": "??",
    "nostalgia": "∿",
}

STAGE_ICON = {
    "child": "👶", "teen": "🧑", "young_adult": "👤",
    "adult": "👥", "elder": "👴",
}


def _lerp_colour(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _draw_bar(surf, x, y, w, h, val, colour, bg=C.NEED_BG, radius=2):
    pygame.draw.rect(surf, bg, (x, y, w, h), border_radius=radius)
    fill = max(0, min(w, int(w * val / 100)))
    if fill > 0:
        pygame.draw.rect(surf, colour, (x, y, fill, h), border_radius=radius)


def _draw_hbar(surf, x, y, w, h, val01, colour, bg=C.NEED_BG, radius=2):
    """val01 is 0..1"""
    pygame.draw.rect(surf, bg, (x, y, w, h), border_radius=radius)
    fill = max(0, min(w, int(w * val01)))
    if fill > 0:
        pygame.draw.rect(surf, colour, (x, y, fill, h), border_radius=radius)


def _clamp_text(font, text: str, max_w: int) -> str:
    if font.size(text)[0] <= max_w:
        return text
    while text and font.size(text + "…")[0] > max_w:
        text = text[:-1]
    return text + "…"


def _wrap_text(font, text: str, max_w: int) -> list[str]:
    """Break text into lines that fit within max_w pixels."""
    if not text:
        return []
    if font.size(text)[0] <= max_w:
        return [text]
    words  = text.split()
    lines  = []
    line   = ""
    for word in words:
        test = (line + " " + word).strip()
        if font.size(test)[0] <= max_w:
            line = test
        else:
            if line:
                lines.append(line)
            # If a single word is wider than max_w, clamp it
            if font.size(word)[0] > max_w:
                word = _clamp_text(font, word, max_w)
            line = word
    if line:
        lines.append(line)
    return lines


class Renderer:
    def __init__(self, surface: pygame.Surface):
        self.surf = surface
        self.W, self.H = surface.get_size()
        self._init_fonts()
        self._node_pos: dict[str, tuple[int, int]] = {}
        self._card_rects: dict[str, pygame.Rect] = {}

    def _init_fonts(self):
        candidates = ["Segoe UI", "Consolas", "Arial", "DejaVu Sans", None]
        for name in candidates:
            try:
                self.f_xl   = pygame.font.SysFont(name, 20, bold=True)
                self.f_lg   = pygame.font.SysFont(name, 16, bold=True)
                self.f_md   = pygame.font.SysFont(name, 14)
                self.f_sm   = pygame.font.SysFont(name, 12)
                self.f_xs   = pygame.font.SysFont(name, 11)
                self.f_hud  = pygame.font.SysFont(name, 13)
                break
            except Exception:
                continue

    # ── Top-level draw ────────────────────────────────────────────────────────

    def draw(self, game: "Game") -> None:
        self.surf.fill(C.BG)
        state = game.state   # thread-safe snapshot

        # Grab feed data in one locked call so renderer never races engine thread
        event_log, valence_history, model_trace = game.get_feed_snapshot()

        self._draw_hud(game, state)
        self._draw_panel_borders()
        self._draw_roster(game, state)
        self._draw_graph(game, state)
        self._draw_feed(event_log)
        self._draw_detail(game, state)
        self._draw_bottom_data(game, state, valence_history, model_trace)

    # ── HUD ───────────────────────────────────────────────────────────────────

    def _draw_hud(self, game: "Game", state: dict) -> None:
        pygame.draw.rect(self.surf, C.HUD_BG, (0, 0, self.W, HUD_H))
        pygame.draw.line(self.surf, C.BORDER_MID, (0, HUD_H - 1), (self.W, HUD_H - 1), 1)

        sim_label = state.get("sim_label") or f"Tick {state.get('tick',0):04d}"
        venue     = state.get("venue", {}).get("name", "?") if isinstance(state.get("venue"), dict) else "?"
        speed     = state.get("speed_label", f"{game.speed:.2g}×")
        pending   = state.get("pending_interactions", 0)
        n_sims    = len(state.get("sims", []))
        pause_txt = "  ⏸ PAUSED" if game.paused else ""

        x = 14
        parts = [
            ("SIMS ENGINE", C.TEXT_GOLD,   self.f_xl),
            (f"   {sim_label}", C.TEXT,    self.f_hud),
            (f"  │  {venue}",  C.TEXT_DIM, self.f_hud),
            (f"  │  {speed}",  C.ACCENT,   self.f_hud),
            (f"  │  {n_sims} sims", C.TEXT_DIM, self.f_hud),
        ]
        if pending:
            parts.append((f"  ⏳ {pending}", C.TEXT_GOLD, self.f_hud))
        if pause_txt:
            parts.append((pause_txt, C.NEED_LOW, self.f_hud))

        for text, col, font in parts:
            s = font.render(text, True, col)
            self.surf.blit(s, (x, (HUD_H - s.get_height()) // 2))
            x += s.get_width()

        keys = "SPACE=pause  +/-=speed  TAB=focus  ESC=quit"
        ks = self.f_xs.render(keys, True, C.TEXT_GHOST)
        self.surf.blit(ks, (self.W - ks.get_width() - 12, (HUD_H - ks.get_height()) // 2))

    # ── Panel dividers ────────────────────────────────────────────────────────

    def _draw_panel_borders(self) -> None:
        col = C.BORDER
        y0, y1 = CONTENT_Y, BOT_Y
        # Vertical dividers in content area
        for x in (ROSTER_W, FEED_X, DETAIL_X):
            pygame.draw.line(self.surf, col, (x, y0), (x, y1), 1)
        # Horizontal: content / bottom
        pygame.draw.line(self.surf, C.BORDER_MID, (0, BOT_Y), (self.W, BOT_Y), 1)
        # Bottom sub-dividers
        for x in (REL_PANEL_W, REL_PANEL_W + VAL_PANEL_W):
            pygame.draw.line(self.surf, col, (x, BOT_Y), (x, self.H), 1)

    # ── Sim Roster ────────────────────────────────────────────────────────────

    def _draw_roster(self, game: "Game", state: dict) -> None:
        sims = state.get("sims", [])
        area_h = CONTENT_H - 2
        card_h = max(130, min(210, area_h // max(1, len(sims))))

        # Panel header
        h = self.f_xs.render("SIM ROSTER", True, C.TEXT_DIM)
        self.surf.blit(h, (ROSTER_X + 8, CONTENT_Y + 5))

        self._card_rects.clear()
        for i, sim in enumerate(sims):
            y = CONTENT_Y + 22 + i * card_h
            if y + card_h > BOT_Y:
                break
            rect = pygame.Rect(ROSTER_X + 4, y + 3, ROSTER_W - 8, card_h - 6)
            self._card_rects[sim["id"]] = rect
            self._draw_sim_card(sim, rect, sim["id"] == game.selected_sim_id)

    def _draw_sim_card(self, sim: dict, rect: pygame.Rect, selected: bool) -> None:
        bg  = C.PANEL_SEL if selected else C.PANEL
        bdr = C.BORDER_SEL if selected else C.BORDER
        pygame.draw.rect(self.surf, bg, rect, border_radius=5)
        pygame.draw.rect(self.surf, bdr, rect, 1, border_radius=5)

        x0, y0 = rect.x + 8, rect.y + 7

        # ── Left: emotion circle ──────────────────────────────────────────
        emo   = sim.get("emotion", "neutral")
        emo_c = C.emotion_colour(emo)
        cx, cy = x0 + 18, y0 + 18
        pygame.draw.circle(self.surf, C.PANEL_DARK, (cx, cy), 20)
        pygame.draw.circle(self.surf, emo_c, (cx, cy), 20, 2)
        sym = EMOTION_SYMBOL.get(emo, "·")
        es  = self.f_sm.render(sym[:2], True, emo_c)
        self.surf.blit(es, (cx - es.get_width() // 2, cy - es.get_height() // 2))

        # ── Right of circle: name, age, stage ────────────────────────────
        tx = x0 + 44
        name = sim["name"]
        age  = sim.get("profile", {}).get("age") if isinstance(sim.get("profile"), dict) else None
        # state dict has age nested or flat — try both
        age  = age or sim.get("age", "?")
        stage = sim.get("life_stage", "adult")
        stage_c = C.STAGE_COLOUR.get(stage, C.TEXT_DIM)

        ns = self.f_lg.render(name, True, C.TEXT_BRIGHT)
        self.surf.blit(ns, (tx, y0))

        stage_lbl = f"{age}  {stage.replace('_',' ')}"
        sl = self.f_xs.render(stage_lbl, True, stage_c)
        self.surf.blit(sl, (tx, y0 + 18))

        # § and career
        job_s = self.f_xs.render(
            f"{sim.get('job','?')}  §{sim.get('simoleons',0):.0f}  perf:{sim.get('career_performance',0):.0f}",
            True, C.TEXT_DIM,
        )
        self.surf.blit(job_s, (tx, y0 + 30))

        # Arc badge (right side of name row)
        arc_text, arc_c = self._arc_label(sim)
        if arc_text:
            as_ = self.f_xs.render(arc_text, True, arc_c)
            self.surf.blit(as_, (rect.right - as_.get_width() - 8, y0 + 5))

        # ── Needs bars ────────────────────────────────────────────────────
        ny     = y0 + 48
        bar_w  = (rect.width - 16) // len(NEED_NAMES)
        needs  = sim.get("needs", {})
        for j, (need, short) in enumerate(zip(NEED_NAMES, NEED_SHORT)):
            val = needs.get(need, 0)
            bx  = x0 + j * bar_w
            c   = C.need_colour(val)
            ls  = self.f_xs.render(short, True, c)
            self.surf.blit(ls, (bx, ny))
            _draw_bar(self.surf, bx, ny + 13, bar_w - 2, 5, val, c)

        # ── OCEAN mini bars ───────────────────────────────────────────────
        oy = ny + 24
        ocean = sim.get("ocean", {})
        bar_total_w = rect.width - 16
        ob_w = bar_total_w // 5
        for j, (key, short) in enumerate(zip(OCEAN_KEYS, OCEAN_SHORT)):
            val = ocean.get(key, 0.5)
            col = C.OCEAN_COLOURS.get(key, C.TEXT_DIM)
            bx  = x0 + j * ob_w
            ls  = self.f_xs.render(short, True, col)
            self.surf.blit(ls, (bx, oy))
            _draw_hbar(self.surf, bx, oy + 12, ob_w - 2, 4, val, col)

        # ── Wants / goal ─────────────────────────────────────────────────
        wy = oy + 22
        wants = sim.get("active_wants", [])[:1]
        for w in wants:
            wt = _clamp_text(self.f_xs, f"↳ {w}", rect.width - 18)
            ws = self.f_xs.render(wt, True, C.TEXT_GHOST)
            self.surf.blit(ws, (x0, wy))
            wy += 13

    def _arc_label(self, sim: dict) -> tuple[str, tuple]:
        fears = sim.get("fears", [])
        # arc state is not currently in state dict — infer from emotion
        emo = sim.get("emotion", "neutral")
        if emo in ("grief", "sadness", "remorse") and fears:
            return "GRIEF", C.ARC_GRIEF
        if emo in ("anger", "annoyance") and sim.get("career_performance", 50) > 75:
            return "BURNOUT", C.ARC_BURNOUT
        return "", C.TEXT_DIM

    # ── Social graph ──────────────────────────────────────────────────────────

    def _draw_graph(self, game: "Game", state: dict) -> None:
        sims = state.get("sims", [])
        rels = state.get("relationships", [])

        area_x = GRAPH_X + 1
        area_w = GRAPH_W - 2
        area_h = CONTENT_H

        cx = area_x + area_w // 2
        cy = CONTENT_Y + area_h // 2
        radius = min(area_w, area_h) // 2 - 60

        # Panel header
        h = self.f_xs.render("SOCIAL GRAPH", True, C.TEXT_DIM)
        self.surf.blit(h, (area_x + 8, CONTENT_Y + 5))

        # Position nodes
        self._node_pos.clear()
        n = max(1, len(sims))
        for i, sim in enumerate(sims):
            angle = -math.pi / 2 + 2 * math.pi * i / n
            nx = int(cx + radius * math.cos(angle))
            ny = int(cy + radius * math.sin(angle))
            self._node_pos[sim["id"]] = (nx, ny)

        sim_map = {s["id"]: s for s in sims}

        # Draw relationship edges
        for rel in rels:
            a_id, b_id = rel["sim_a"], rel["sim_b"]
            if a_id not in self._node_pos or b_id not in self._node_pos:
                continue
            ax, ay = self._node_pos[a_id]
            bx, by = self._node_pos[b_id]
            f = rel.get("friendship", 0)
            r = rel.get("romance", 0)

            if r > 20:
                col = C.REL_ROMANCE
                w   = max(1, min(6, int(r / 15)))
            elif f > 45:
                col = C.REL_FRIEND
                w   = max(1, min(5, int(f / 18)))
            elif f < -20:
                col = C.REL_ENEMY
                w   = max(1, min(4, int(abs(f) / 18)))
            else:
                col = C.REL_NEUTRAL
                w   = 1

            # Draw edge with glow if strong
            if w >= 3:
                glow = tuple(max(0, c - 50) for c in col)
                pygame.draw.line(self.surf, glow, (ax, ay), (bx, by), w + 2)
            pygame.draw.line(self.surf, col, (ax, ay), (bx, by), w)

            # State label at midpoint
            state_lbl = rel.get("state", "")
            if state_lbl and state_lbl not in ("strangers", ""):
                mx, my = (ax + bx) // 2, (ay + by) // 2
                ls = self.f_xs.render(state_lbl, True, col)
                bg_r = pygame.Rect(mx - ls.get_width()//2 - 2, my - 7, ls.get_width() + 4, 14)
                pygame.draw.rect(self.surf, C.PANEL_DARK, bg_r, border_radius=2)
                self.surf.blit(ls, (mx - ls.get_width() // 2, my - 6))

        # Draw parent-child dotted lines
        for sim in sims:
            for pid in sim.get("parent_ids", []):
                if pid in self._node_pos and sim["id"] in self._node_pos:
                    sx, sy = self._node_pos[sim["id"]]
                    px, py = self._node_pos[pid]
                    # Dashed line approximation
                    dx, dy = px - sx, py - sy
                    length = max(1, math.sqrt(dx*dx + dy*dy))
                    steps = int(length / 8)
                    for k in range(0, steps, 2):
                        t0, t1 = k / steps, min(1, (k + 1) / steps)
                        p0 = (int(sx + dx * t0), int(sy + dy * t0))
                        p1 = (int(sx + dx * t1), int(sy + dy * t1))
                        pygame.draw.line(self.surf, C.TEXT_GHOST, p0, p1, 1)

        # Draw sim nodes
        for sim in sims:
            if sim["id"] not in self._node_pos:
                continue
            nx, ny  = self._node_pos[sim["id"]]
            emo_c   = C.emotion_colour(sim.get("emotion", "neutral"))
            lod     = sim.get("lod_tier", "ACTIVE")
            lod_c   = C.LOD_ACTIVE if lod == "ACTIVE" else C.LOD_BG_NODE if lod == "BACKGROUND" else C.LOD_DORMANT
            sel     = sim["id"] == game.selected_sim_id
            r       = 24 if sel else 20

            # Outer glow ring (emotion colour)
            pygame.draw.circle(self.surf, C.PANEL_DARK, (nx, ny), r + 4)
            pygame.draw.circle(self.surf, emo_c, (nx, ny), r + 4, 2)

            # Inner fill (LOD colour)
            pygame.draw.circle(self.surf, C.PANEL, (nx, ny), r)
            pygame.draw.circle(self.surf, lod_c, (nx, ny), r, 2)

            if sel:
                pygame.draw.circle(self.surf, C.BORDER_SEL, (nx, ny), r + 6, 2)

            # Initials
            parts = sim["name"].split()
            initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()
            init_s = self.f_md.render(initials, True, C.TEXT_BRIGHT)
            self.surf.blit(init_s, (nx - init_s.get_width() // 2, ny - init_s.get_height() // 2))

            # Name below
            fn = sim["name"].split()[0]
            ns = self.f_xs.render(fn, True, C.TEXT)
            self.surf.blit(ns, (nx - ns.get_width() // 2, ny + r + 4))

            # Age / stage pill
            age   = sim.get("age", "?")
            stage = sim.get("life_stage", "")
            stage_c = C.STAGE_COLOUR.get(stage, C.TEXT_DIM)
            age_s = self.f_xs.render(str(age), True, stage_c)
            self.surf.blit(age_s, (nx - age_s.get_width() // 2, ny + r + 17))

        # Centre: venue + clock
        venue = state.get("venue", {}).get("name", "") if isinstance(state.get("venue"), dict) else ""
        sim_lbl = state.get("sim_label", "")
        if sim_lbl:
            sl = self.f_hud.render(sim_lbl, True, C.TEXT_ACCENT)
            self.surf.blit(sl, (cx - sl.get_width() // 2, CONTENT_Y + 16))
        if venue:
            vl = self.f_xs.render(f"📍 {venue}", True, C.TEXT_DIM)
            self.surf.blit(vl, (cx - vl.get_width() // 2, CONTENT_Y + 32))

    # ── Live Feed ─────────────────────────────────────────────────────────────

    def _draw_feed(self, event_log: list) -> None:
        x0 = FEED_X + 1
        w  = FEED_W - 2

        h = self.f_xs.render("LIVE FEED", True, C.TEXT_DIM)
        self.surf.blit(h, (x0 + 6, CONTENT_Y + 5))

        y = CONTENT_Y + 22
        for entry in event_log:
            if y + 16 > BOT_Y:
                break
            y = self._draw_feed_entry(entry, x0, y, w, y_max=BOT_Y - 2)

    def _draw_feed_entry(self, entry: dict, x0: int, y: int, w: int, y_max: int = 0) -> int:
        icon  = entry.get("icon", "·")
        text  = entry.get("text", "")
        col   = entry.get("colour", C.TEXT)
        sub   = entry.get("sub", [])
        if y_max == 0:
            y_max = BOT_Y - 4

        # Icon — pinned to first line
        ic = self.f_sm.render(icon[:2], True, col)
        self.surf.blit(ic, (x0 + 4, y))

        # Main text — word-wrapped, indented after icon
        text_x    = x0 + 22
        wrap_w    = w - 26
        lines     = _wrap_text(self.f_sm, text, wrap_w)
        line_h    = 15
        for i, line in enumerate(lines):
            if y + line_h > y_max:
                break
            lx = text_x if i == 0 else x0 + 4   # indent continuation lines
            ls = self.f_sm.render(line, True, col)
            self.surf.blit(ls, (lx, y))
            y += line_h

        # Sub-lines — word-wrapped, slightly smaller font
        sub_x  = x0 + 8
        sub_w  = w - 12
        sub_h  = 13
        for sl_text, sl_col in sub:
            sub_lines = _wrap_text(self.f_xs, sl_text, sub_w)
            for sl in sub_lines:
                if y + sub_h > y_max:
                    break
                ss = self.f_xs.render(sl, True, sl_col)
                self.surf.blit(ss, (sub_x, y))
                y += sub_h

        # Separator
        if y + 3 <= y_max:
            pygame.draw.line(self.surf, C.BORDER, (x0 + 4, y + 2), (x0 + w - 8, y + 2), 1)
        return y + 5

    # ── Selected sim detail ───────────────────────────────────────────────────

    def _draw_detail(self, game: "Game", state: dict) -> None:
        x0 = DETAIL_X + 1
        w  = DETAIL_W - 2

        sim_data = None
        if game.selected_sim_id:
            for s in state.get("sims", []):
                if s["id"] == game.selected_sim_id:
                    sim_data = s
                    break

        hdr_txt = sim_data["name"] if sim_data else "DETAIL"
        h = self.f_xs.render(hdr_txt if not sim_data else "DETAIL — " + sim_data["name"], True, C.TEXT_DIM)
        self.surf.blit(h, (x0 + 6, CONTENT_Y + 5))

        if not sim_data:
            hint = self.f_xs.render("Click a sim to inspect", True, C.TEXT_GHOST)
            self.surf.blit(hint, (x0 + 8, CONTENT_Y + 28))
            return

        y = CONTENT_Y + 24
        ocean = sim_data.get("ocean", {})
        cy_radar = y + 85
        cx_radar = x0 + w // 2
        self._draw_ocean_radar(ocean, cx_radar, cy_radar, 72)
        y = cy_radar + 82

        # Fears
        fears = sim_data.get("fears", [])
        if fears:
            fs = self.f_xs.render("Fears:", True, C.TEXT_DIM)
            self.surf.blit(fs, (x0 + 6, y))
            y += 14
            for fear in fears[:3]:
                fl = _clamp_text(self.f_xs, f"  • {fear}", w - 10)
                fls = self.f_xs.render(fl, True, C.VALENCE_NEG)
                self.surf.blit(fls, (x0 + 6, y))
                y += 13

        # Wants
        wants = sim_data.get("active_wants", [])
        if wants:
            y += 4
            ws = self.f_xs.render("Wants:", True, C.TEXT_DIM)
            self.surf.blit(ws, (x0 + 6, y))
            y += 14
            for w_txt in wants[:3]:
                wl = _clamp_text(self.f_xs, f"  • {w_txt}", w - 10)
                wls = self.f_xs.render(wl, True, C.VALENCE_POS)
                self.surf.blit(wls, (x0 + 6, y))
                y += 13

        # Relationships involving this sim
        y += 6
        rs = self.f_xs.render("Relationships:", True, C.TEXT_DIM)
        self.surf.blit(rs, (x0 + 6, y))
        y += 14
        for rel in state.get("relationships", []):
            if sim_data["id"] not in (rel["sim_a"], rel["sim_b"]):
                continue
            other_id = rel["sim_b"] if rel["sim_a"] == sim_data["id"] else rel["sim_a"]
            other_name = next(
                (s["name"].split()[0] for s in state.get("sims", []) if s["id"] == other_id), other_id[:6]
            )
            f = rel.get("friendship", 0)
            r = rel.get("romance", 0)
            f_col = C.VALENCE_POS if f > 30 else C.VALENCE_NEG if f < -10 else C.VALENCE_NEU
            line = f"{other_name}: F{f:+.0f} R{r:+.0f}  {rel.get('state','?')}"
            ls = self.f_xs.render(_clamp_text(self.f_xs, line, w - 12), True, f_col)
            self.surf.blit(ls, (x0 + 8, y))
            y += 13
            if y > BOT_Y - 10:
                break

    def _draw_ocean_radar(self, ocean: dict, cx: int, cy: int, r: int) -> None:
        n_axes = 5
        labels = ["O", "C", "E", "A", "N"]
        keys   = OCEAN_KEYS
        angles = [-math.pi / 2 + 2 * math.pi * i / n_axes for i in range(n_axes)]

        # Background grid
        for ring in (0.25, 0.5, 0.75, 1.0):
            pts = [
                (int(cx + r * ring * math.cos(a)), int(cy + r * ring * math.sin(a)))
                for a in angles
            ]
            pygame.draw.polygon(self.surf, C.BORDER, pts, 1)

        # Axes
        for a in angles:
            pygame.draw.line(self.surf, C.BORDER,
                             (cx, cy),
                             (int(cx + r * math.cos(a)), int(cy + r * math.sin(a))), 1)

        # OCEAN polygon
        vals = [ocean.get(k, 0.5) for k in keys]
        pts  = [
            (int(cx + r * v * math.cos(a)), int(cy + r * v * math.sin(a)))
            for v, a in zip(vals, angles)
        ]
        fill_surf = pygame.Surface((r * 2 + 4, r * 2 + 4), pygame.SRCALPHA)
        shifted   = [(p[0] - cx + r + 2, p[1] - cy + r + 2) for p in pts]
        pygame.draw.polygon(fill_surf, (80, 175, 245, 60), shifted)
        self.surf.blit(fill_surf, (cx - r - 2, cy - r - 2))
        pygame.draw.polygon(self.surf, C.TEXT_ACCENT, pts, 2)

        # Dots + labels
        for i, (v, a, lbl) in enumerate(zip(vals, angles, labels)):
            px = int(cx + r * v * math.cos(a))
            py = int(cy + r * v * math.sin(a))
            col = list(C.OCEAN_COLOURS.values())[i]
            pygame.draw.circle(self.surf, col, (px, py), 4)
            # Label at full radius
            lx = int(cx + (r + 12) * math.cos(a))
            ly = int(cy + (r + 12) * math.sin(a))
            ls = self.f_xs.render(f"{lbl}:{v:.2f}", True, col)
            self.surf.blit(ls, (lx - ls.get_width() // 2, ly - ls.get_height() // 2))

    # ── Bottom strip ──────────────────────────────────────────────────────────

    def _draw_bottom_data(
        self, game: "Game", state: dict,
        valence_history: list, model_trace: list,
    ) -> None:
        self._draw_relationships_panel(state)
        self._draw_valence_panel(valence_history)
        self._draw_model_trace_panel(model_trace)

    def _draw_relationships_panel(self, state: dict) -> None:
        x0 = 0
        w  = REL_PANEL_W
        h = self.f_xs.render("RELATIONSHIPS", True, C.TEXT_DIM)
        self.surf.blit(h, (x0 + 8, BOT_Y + 5))

        rels = state.get("relationships", [])
        sim_names = {s["id"]: s["name"].split()[0] for s in state.get("sims", [])}
        y = BOT_Y + 20
        row_h = 27

        for rel in rels:
            if y + row_h > self.H - 4:
                break
            a_name = sim_names.get(rel["sim_a"], rel["sim_a"][:6])
            b_name = sim_names.get(rel["sim_b"], rel["sim_b"][:6])
            f = rel.get("friendship", 0)
            r = rel.get("romance", 0)
            state_lbl = rel.get("state", "strangers")
            rom_lbl   = rel.get("romance_label", "")

            f_col = C.VALENCE_POS if f > 30 else C.VALENCE_NEG if f < -10 else C.VALENCE_NEU
            r_col = C.REL_ROMANCE if r > 10 else C.TEXT_GHOST

            # Names + state
            pair_txt = f"{a_name} ↔ {b_name}"
            ps = self.f_sm.render(pair_txt, True, C.TEXT)
            self.surf.blit(ps, (x0 + 8, y))
            st = self.f_xs.render(state_lbl, True, f_col)
            self.surf.blit(st, (x0 + 8 + ps.get_width() + 6, y + 1))

            # Friendship bar
            bx = x0 + 8
            by = y + 15
            bw = 120
            f_norm = (f + 100) / 200  # -100..100 → 0..1
            _draw_hbar(self.surf, bx, by, bw, 6, f_norm, f_col)
            fv = self.f_xs.render(f"F:{f:+.0f}", True, f_col)
            self.surf.blit(fv, (bx + bw + 4, by - 2))

            # Romance bar
            rbx = bx + bw + 36
            r_norm = max(0, r) / 100
            _draw_hbar(self.surf, rbx, by, 80, 6, r_norm, r_col)
            if rom_lbl and rom_lbl != "none":
                rl = self.f_xs.render(rom_lbl, True, r_col)
                self.surf.blit(rl, (rbx + 84, by - 2))

            y += row_h

    def _draw_valence_panel(self, valence_history: list) -> None:
        history = valence_history
        x0 = REL_PANEL_W + 1
        w  = VAL_PANEL_W - 2

        h = self.f_xs.render("VALENCE HISTORY", True, C.TEXT_DIM)
        self.surf.blit(h, (x0 + 8, BOT_Y + 5))

        if not history:
            return

        # Sparkline
        chart_x = x0 + 8
        chart_y = BOT_Y + 20
        chart_w = w - 16
        chart_h = BOT_H - 50

        # Zero line
        zero_y = chart_y + chart_h // 2
        pygame.draw.line(self.surf, C.BORDER_MID, (chart_x, zero_y), (chart_x + chart_w, zero_y), 1)

        n = min(len(history), 60)
        recent = history[-n:]
        step   = chart_w / max(1, n - 1)

        # Draw area fill + line
        prev_x = prev_y = None
        for i, v in enumerate(recent):
            px = int(chart_x + i * step)
            py = int(zero_y - v * (chart_h // 2))
            col = C.VALENCE_POS if v > 0 else C.VALENCE_NEG

            if prev_x is not None:
                pygame.draw.line(self.surf, col, (prev_x, prev_y), (px, py), 2)
                # Filled area
                fa = pygame.Surface((abs(px - prev_x) + 2, abs(py - zero_y) + 2), pygame.SRCALPHA)
                fc = (*col, 40)
                pts = [(0, 0), (abs(px - prev_x), prev_y - min(prev_y, py)),
                       (abs(px - prev_x), zero_y - min(prev_y, py)), (0, zero_y - prev_y)]
            prev_x, prev_y = px, py
            pygame.draw.circle(self.surf, col, (px, py), 2)

        # Labels
        labels = [
            ("+1.0", chart_x + chart_w + 4, chart_y),
            (" 0.0", chart_x + chart_w + 4, zero_y - 6),
            ("-1.0", chart_x + chart_w + 4, chart_y + chart_h - 11),
        ]
        for lbl, lx, ly in labels:
            ls = self.f_xs.render(lbl, True, C.TEXT_GHOST)
            self.surf.blit(ls, (lx, ly))

        # Stats overlay
        if history:
            mean_v = sum(history[-20:]) / max(1, min(20, len(history)))
            pos_c  = C.VALENCE_POS if mean_v > 0 else C.VALENCE_NEG
            stat   = f"mean(20): {mean_v:+.2f}  n={len(history)}"
            ss = self.f_xs.render(stat, True, pos_c)
            self.surf.blit(ss, (x0 + 8, BOT_Y + BOT_H - 18))

    def _draw_model_trace_panel(self, model_trace: list) -> None:
        x0 = REL_PANEL_W + VAL_PANEL_W + 1
        w  = MODEL_PANEL_W - 2

        h = self.f_xs.render("MODEL TRACE  (last interaction)", True, C.TEXT_DIM)
        self.surf.blit(h, (x0 + 8, BOT_Y + 5))

        trace = model_trace
        if not trace:
            nd = self.f_xs.render("No interaction yet", True, C.TEXT_GHOST)
            self.surf.blit(nd, (x0 + 10, BOT_Y + 24))
            return

        y = BOT_Y + 20
        row_h = 16
        for label, value, col in trace:
            if y + row_h > self.H - 4:
                break
            lbl_s = self.f_xs.render(f"{label}:", True, C.TEXT_DIM)
            val_s = self.f_xs.render(_clamp_text(self.f_xs, str(value), w - lbl_s.get_width() - 16), True, col)
            self.surf.blit(lbl_s, (x0 + 8, y))
            self.surf.blit(val_s, (x0 + 8 + lbl_s.get_width() + 4, y))
            y += row_h

    # ── Click handling ────────────────────────────────────────────────────────

    def handle_click(self, pos: tuple[int, int], game: "Game") -> None:
        for sim_id, rect in self._card_rects.items():
            if rect.collidepoint(pos):
                game.selected_sim_id = None if game.selected_sim_id == sim_id else sim_id
                return
        for sim_id, (nx, ny) in self._node_pos.items():
            if (pos[0]-nx)**2 + (pos[1]-ny)**2 <= 25**2:
                game.selected_sim_id = None if game.selected_sim_id == sim_id else sim_id
                return
