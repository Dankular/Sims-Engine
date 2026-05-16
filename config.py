"""
sim_v2/config.py — All constants for the simulation. No mutable state.
"""

from pathlib import Path

# ── llama-cpp-python / GGUF backend ───────────────────────────────────────────
GGUF_REPO = "unsloth/Qwen3.5-9B-GGUF"
GGUF_FILENAME = "Qwen3.5-9B-Q4_K_M.gguf"  # swap UD-Q4_K_XL for higher quality
GGUF_N_CTX = 8192
GGUF_GPU_LAYERS = -1  # -1 = all layers on GPU; 0 = CPU-only
GGUF_N_THREADS = None  # None = auto

# ── HuggingFace model/dataset IDs ─────────────────────────────────────────────
# Memory embeddings — static-retrieval-mrl is 100-400× faster on CPU
HF_SENTENCE_MODEL = "sentence-transformers/static-retrieval-mrl-en-v1"
HF_SENTENCE_MODEL_FULL = (
    "sentence-transformers/all-mpnet-base-v2"  # higher quality fallback
)
HF_PERSONALITY_MODEL = "KevSun/Personality_LM"
# Child / short-text OCEAN scorer (DistilBERT, no long essay needed)
HF_CHILD_OCEAN_MODEL = "Arash-Alborz/personality-trait-predictor"
# Emotion classifier — ModernBERT, exactly 27 GoEmotions labels, multi-label
HF_EMOTION_CLASSIFIER = "cirimus/modernbert-base-go-emotions"
HF_EMOTION_CLASSIFIER_ML = (
    "AnasAlokla/multilingual_go_emotions"  # multilingual fallback
)
# MBTI inference from text
HF_MBTI_MODEL = "theta/MBTI-ckiplab-bert"
# Background LOD smaller LLM
GGUF_BG_REPO = "unsloth/Ministral-3B-Instruct-2410-GGUF"
GGUF_BG_FILENAME = "Ministral-3B-Instruct-2410-Q4_K_M.gguf"
HF_OKCUPID_DATASET = "SpiceeChat/OkCupid-59k-Anonymized-Profiles"
HF_PROSOCIAL_DATASET = "allenai/prosocial-dialog"
HF_DIALOGUE_DATASET = "agentlans/multi-character-dialogue"
HF_ATOMIC_DATASET = "Estwld/atomic2020-origin"
HF_SOCIAL_IQA_DATASET = "allenai/social_i_qa"
HF_EMPATHETIC_DATASET = "facebook/empathetic_dialogues"
HF_EMOTION_DATASET = "dair-ai/emotion"
HF_CONVAI2_DATASET = "convai-challenge/conv_ai_2"

# ── Cache / persistence paths ─────────────────────────────────────────────────
CACHE_DIR = Path(__file__).parent / ".sim_cache"
SIM_DB_PATH = str(Path(__file__).parent / "sim_state.db")

# ── Simulation timing ─────────────────────────────────────────────────────────
TICK_SECONDS = 0.5
GAME_START_HOUR = 8

# ── Needs ─────────────────────────────────────────────────────────────────────
NEEDS_DECAY = 3.0
SOCIAL_RESTORE = 15.0
MEMORY_THRESHOLD = 0.75
MAX_MEMORIES = 50

NEED_CRITICAL = 15
NEED_LOW = 35
NEED_OK = 65

NEED_NAMES = [
    "hunger",
    "energy",
    "social",
    "fun",
    "hygiene",
    "environment",
    "bladder",
    "comfort",
]

# ── Relationships ─────────────────────────────────────────────────────────────
REL_STRANGER = 0
REL_ACQUAINTANCE = 20
REL_FRIEND = 45
REL_CLOSE = 65
REL_BEST = 82

# ── Interaction cooldowns ─────────────────────────────────────────────────────
COOLDOWN_TICKS = 3

# ── Economy ───────────────────────────────────────────────────────────────────
PAY_PERIOD_TICKS = 5
BASE_SALARY = {"low": 40, "medium": 90, "high": 180}
LIVING_COST_PER_TICK = 8
LOW_FUNDS_THRESHOLD = 300

# ── Career events ─────────────────────────────────────────────────────────────
CAREER_EVENT_INTERVAL = 8
CAREER_EVENT_CHANCE = 0.35

# ── Fears ────────────────────────────────────────────────────────────────────
FEAR_RESOLVE_VALENCE = 0.70
FEAR_REDUCTION = 0.12

# ── LOD system ───────────────────────────────────────────────────────────────
LOD_ACTIVE_LIMIT = 20
LOD_BACKGROUND_LIMIT = 120

# ── Schedule phases ───────────────────────────────────────────────────────────
SCHEDULE_WORK = set(range(9, 17))
SCHEDULE_SOCIAL = set(range(18, 23))

# ── Async adjudication ────────────────────────────────────────────────────────
ADJ_WORKERS = 3

# ── Shops ────────────────────────────────────────────────────────────────────
SHOP_DEFS = [
    {"name": "restaurant", "need": "hunger", "cost": 30, "restore": 80},
    {"name": "gym", "need": "fun", "cost": 20, "restore": 50},
    {"name": "spa", "need": "comfort", "cost": 45, "restore": 60},
    {"name": "convenience store", "need": "hunger", "cost": 12, "restore": 40},
]
LOW_NEED_SHOP_THRESHOLD = 25

# ── Life events ───────────────────────────────────────────────────────────────
LIFE_EVENT_INTERVAL = 15
LIFE_EVENT_CHANCE = 0.25

# ── Gossip ───────────────────────────────────────────────────────────────────
GOSSIP_SPREAD_CHANCE = 0.30
MAX_GOSSIP_FACTS = 20

# ── Social norms ──────────────────────────────────────────────────────────────
SOCIAL_NORMS_COUNT = 15

# ── Personality pools ─────────────────────────────────────────────────────────
INTERESTS_POOL = [
    "hiking",
    "cooking",
    "reading",
    "gaming",
    "music",
    "art",
    "film",
    "travel",
    "fitness",
    "yoga",
    "coding",
    "gardening",
    "photography",
    "volunteering",
    "dancing",
    "writing",
    "meditation",
    "sports",
]

TRAITS_POOL = [
    "bookworm",
    "romantic",
    "loner",
    "outgoing",
    "hot-headed",
    "cheerful",
    "gloomy",
    "creative",
    "ambitious",
    "lazy",
    "neat",
    "slob",
    "good",
    "evil",
    "materialistic",
    "family-oriented",
    "geek",
    "foodie",
]

DEALBREAKERS_POOL = [
    "smoking",
    "dishonesty",
    "anti-intellectualism",
    "aggression",
    "close-mindedness",
    "laziness",
    "rudeness",
]

ASPIRATIONS = [
    "Fortune",
    "Family",
    "Popularity",
    "Knowledge",
    "Romance",
    "Creative",
]

JOBS = [
    "Software Engineer",
    "Teacher",
    "Nurse",
    "Artist",
    "Chef",
    "Journalist",
    "Accountant",
    "Barista",
    "Freelancer",
    "Researcher",
]

DIETS = ["omnivore", "vegetarian", "vegan", "pescatarian"]

EMOTIONS_27 = [
    "admiration",
    "amusement",
    "anger",
    "annoyance",
    "approval",
    "caring",
    "confusion",
    "curiosity",
    "desire",
    "disappointment",
    "disapproval",
    "disgust",
    "embarrassment",
    "excitement",
    "fear",
    "gratitude",
    "grief",
    "joy",
    "love",
    "nervousness",
    "optimism",
    "pride",
    "realization",
    "relief",
    "remorse",
    "sadness",
    "surprise",
]

INTERACTION_TYPES = {
    "friendly": ["chat", "tell story", "share joke", "compliment", "ask about day"],
    "funny": ["tell joke", "do impression", "share meme", "playful tease"],
    "mean": ["insult", "mock", "argue", "spread rumour"],
    "romantic": ["flirt", "compliment appearance", "ask on date", "hold hands", "give gift"],
    "intimate": ["speak tenderly", "share private hope", "express longing"],
    "deep": ["share secret", "discuss fears", "give life advice", "confide"],
}

VENUES = [
    {"name": "house party", "noise": 0.8, "intimacy": 0.3, "crowd": 0.9},
    {"name": "coffee shop", "noise": 0.3, "intimacy": 0.7, "crowd": 0.4},
    {"name": "park", "noise": 0.2, "intimacy": 0.6, "crowd": 0.3},
    {"name": "nightclub", "noise": 0.95, "intimacy": 0.2, "crowd": 0.95},
    {"name": "office", "noise": 0.4, "intimacy": 0.3, "crowd": 0.6},
    {"name": "home (1:1)", "noise": 0.1, "intimacy": 0.9, "crowd": 0.05},
    {"name": "gym", "noise": 0.5, "intimacy": 0.2, "crowd": 0.5},
    {"name": "library", "noise": 0.05, "intimacy": 0.5, "crowd": 0.2},
]

SKILL_DEFINITIONS = {
    "charisma": {
        "max": 10,
        "unlocks": {3: "tell riveting story", 7: "enchanting introduction"},
    },
    "cooking": {"max": 10, "unlocks": {3: "cook gourmet meal", 7: "host dinner party"}},
    "fitness": {"max": 10, "unlocks": {4: "intense workout", 8: "run marathon"}},
    "logic": {"max": 10, "unlocks": {3: "debate topic", 6: "teach logic puzzle"}},
    "creativity": {
        "max": 10,
        "unlocks": {2: "share artwork", 5: "perform original piece"},
    },
    "comedy": {"max": 10, "unlocks": {3: "tell great joke", 7: "roast expertly"}},
}

URBANSOUND_CLASS_PROPS: dict[str, dict] = {
    "air_conditioner": {"noise": 0.35, "crowd": 0.30, "intimacy": 0.60},
    "car_horn": {"noise": 0.75, "crowd": 0.70, "intimacy": 0.15},
    "children_playing": {"noise": 0.65, "crowd": 0.80, "intimacy": 0.10},
    "dog_bark": {"noise": 0.45, "crowd": 0.30, "intimacy": 0.40},
    "drilling": {"noise": 0.90, "crowd": 0.20, "intimacy": 0.05},
    "engine_idling": {"noise": 0.60, "crowd": 0.50, "intimacy": 0.20},
    "jackhammer": {"noise": 0.95, "crowd": 0.20, "intimacy": 0.05},
    "siren": {"noise": 0.85, "crowd": 0.60, "intimacy": 0.10},
    "street_music": {"noise": 0.55, "crowd": 0.65, "intimacy": 0.20},
    "gun_shot": {"noise": 0.95, "crowd": 0.90, "intimacy": 0.00},
}
