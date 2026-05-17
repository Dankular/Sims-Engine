"""pygame_app/colors.py — Dashboard dark-theme palette."""

# ── Background layers ─────────────────────────────────────────────────────────
BG          = (10,  15,  26)
PANEL       = (14,  21,  35)
PANEL_SEL   = (20,  36,  64)
PANEL_DARK  = ( 8,  12,  20)
HUD_BG      = ( 7,  11,  18)
STORY_BG    = (12,  18,  30)

# ── Borders ───────────────────────────────────────────────────────────────────
BORDER      = (32,  52,  82)
BORDER_SEL  = (80, 140, 220)
BORDER_MID  = (50,  78, 118)

# ── Text ──────────────────────────────────────────────────────────────────────
TEXT_BRIGHT = (228, 240, 252)
TEXT        = (175, 200, 225)
TEXT_DIM    = ( 95, 128, 162)
TEXT_GHOST  = ( 50,  72,  98)
TEXT_GOLD   = (240, 200,  75)
TEXT_ACCENT = ( 80, 175, 245)
TEXT_GREEN  = ( 70, 210, 120)
TEXT_RED    = (220,  80,  80)
HUD_TEXT    = (175, 200, 225)

WHITE  = (255, 255, 255)
BLACK  = (  0,   0,   0)
ACCENT = ( 80, 175, 245)

# Story
NARRATOR_C  = (160, 195, 255)
DIALOGUE_C  = (255, 225, 130)

# ── Valence ───────────────────────────────────────────────────────────────────
VALENCE_POS = ( 55, 200,  95)
VALENCE_NEU = (110, 155, 200)
VALENCE_NEG = (215,  75,  75)

def valence_colour(v: float) -> tuple:
    if v > 0.15:  return VALENCE_POS
    if v < -0.15: return VALENCE_NEG
    return VALENCE_NEU

def valence_colour_norm(v: float) -> tuple:
    """v is 0..1 (normalised valence from adjudicator)."""
    return valence_colour(v - 0.5)

# ── Emotion colours ───────────────────────────────────────────────────────────
EMOTION_COLOUR: dict[str, tuple] = {
    "joy":            (255, 220,  60),
    "love":           (230, 100, 170),
    "excitement":     ( 80, 220, 255),
    "admiration":     ( 80, 200, 200),
    "amusement":      (255, 190,  60),
    "gratitude":      ( 80, 210, 130),
    "optimism":       (100, 220,  80),
    "pride":          (180, 100, 240),
    "relief":         ( 80, 200, 140),
    "approval":       ( 80, 190, 120),
    "caring":         (100, 200, 240),
    "curiosity":      ( 80, 190, 240),
    "surprise":       (240, 240, 180),
    "realization":    (240, 240, 160),
    "desire":         (210,  90, 210),
    "neutral":        (140, 165, 195),
    "sadness":        ( 80, 120, 220),
    "grief":          ( 90, 110, 210),
    "disappointment": ( 90, 110, 200),
    "remorse":        ( 90, 120, 190),
    "anger":          (220,  70,  70),
    "annoyance":      (200,  90,  70),
    "disgust":        (160,  80,  60),
    "disapproval":    (180,  80,  70),
    "embarrassment":  (200, 100, 160),
    "fear":           (200,  75,  75),
    "nervousness":    (220, 165,  60),
    "confusion":      (200, 170,  80),
    "nostalgia":      (160, 130, 220),
}

def emotion_colour(emo: str) -> tuple:
    return EMOTION_COLOUR.get(emo, TEXT_DIM)

# ── Needs ─────────────────────────────────────────────────────────────────────
NEED_OK   = ( 55, 195,  90)
NEED_LOW  = (235, 175,  55)
NEED_CRIT = (215,  70,  70)
NEED_BG   = ( 22,  35,  55)

def need_colour(val: float) -> tuple:
    if val >= 65: return NEED_OK
    if val >= 35: return NEED_LOW
    return NEED_CRIT

# ── Relationships ─────────────────────────────────────────────────────────────
REL_FRIEND  = ( 55, 155, 240)
REL_ROMANCE = (230,  95, 180)
REL_ENEMY   = (215,  70,  70)
REL_NEUTRAL = ( 50,  75, 110)
REL_RIVAL   = (200, 120,  60)

# ── OCEAN traits ──────────────────────────────────────────────────────────────
OCEAN_COLOURS: dict[str, tuple] = {
    "openness":          ( 91, 155, 213),
    "conscientiousness": (237, 125,  49),
    "extraversion":      (169, 209, 142),
    "agreeableness":     (255, 192,   0),
    "neuroticism":       (255, 107, 107),
}

# ── Arc / life-stage ──────────────────────────────────────────────────────────
ARC_GRIEF   = (100, 130, 240)
ARC_BURNOUT = (235, 130,  55)
ARC_LONELY  = ( 75, 135, 205)
ARC_GOAL    = ( 75, 195, 160)

STAGE_COLOUR: dict[str, tuple] = {
    "child":       (140, 215, 255),
    "teen":        (240, 200,  80),
    "young_adult": (100, 220, 150),
    "adult":       (200, 215, 235),
    "elder":       (150, 155, 200),
}

# ── LOD ───────────────────────────────────────────────────────────────────────
LOD_ACTIVE  = ( 55, 215, 120)
LOD_BG_NODE = (235, 175,  55)
LOD_DORMANT = ( 75,  98, 125)
