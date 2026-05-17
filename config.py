"""
sim_v2/config.py — All constants for the simulation. No mutable state.
"""

from pathlib import Path

# ── llama-cpp-python / GGUF backend ───────────────────────────────────────────
# ACTIVE tier adjudicator — 3-4B is enough; downstream models (GoEmotions,
# sentiment, AITA) handle emotion/reputation; LLM only needs valid JSON + reaction text.
#
# Recommended options (pick one):
#   Qwen2.5-3B  — ~2 GB Q4,  same Qwen family, best JSON compliance
#   Phi-4-mini  — ~2.5 GB Q4, stronger reasoning, good for complex dilemmas
#   Llama-3.2-3B — ~2 GB Q4, widely available
#
# Ollama equivalents: qwen2.5:3b | phi4-mini | llama3.2:3b
GGUF_REPO = "Qwen/Qwen2.5-3B-Instruct-GGUF"
GGUF_FILENAME = "Qwen2.5-3B-Instruct-Q4_K_M.gguf"
# Alternative: Phi-4-mini (stronger reasoning, slightly larger)
# GGUF_REPO     = "bartowski/Phi-4-mini-instruct-GGUF"
# GGUF_FILENAME = "Phi-4-mini-instruct-Q4_K_M.gguf"
GGUF_N_CTX = 4096  # 4 k is plenty for our ~800-token prompts; saves RAM
GGUF_GPU_LAYERS = -1  # -1 = all layers on GPU; 0 = CPU-only
GGUF_N_THREADS = None  # None = auto

# Whether to prefix user messages with Qwen3's /no_think directive.
# Set True only when using Qwen3.x models (suppresses <think> blocks).
# Qwen2.5 / Phi / Llama models should leave this False.
GGUF_USE_NO_THINK = False

# ── HuggingFace model/dataset IDs ─────────────────────────────────────────────
# Small inference models (lazy-loaded, CPU-only, all have hardcoded fallbacks)
NLI_SMALL_MODEL = (
    "cross-encoder/nli-deberta-v3-small"  # 85 MB  — scheduler + arc detection
)
GOAL_NLI_MODEL = "typeform/distilbert-base-uncased-mnli"  # 67 MB  — goal inference
SENTIMENT_MODEL = (
    "cardiffnlp/twitter-roberta-base-sentiment-latest"  # 125 MB — delta modulation
)
EKMAN_MODEL = (
    "j-hartmann/emotion-english-distilroberta-base"  # 83 MB  — emotional cascade
)
CROSS_ENCODER_MODEL = (
    "cross-encoder/ms-marco-MiniLM-L-6-v2"  # 85 MB  — memory reranking
)
COMET_MODEL = (
    "google/flan-t5-small"  # 300 MB — ATOMIC causal inference via instruction prompts
)
REWARD_MODEL = (
    "OpenAssistant/reward-model-deberta-v3-large-v2"  # 400 MB — conformity pressure
)

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
# Background LOD — even lighter; 1.5B is sufficient for the compact bg prompt
# Ollama equivalent: qwen2.5:1.5b
GGUF_BG_REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
GGUF_BG_FILENAME = "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
HF_OKCUPID_DATASET = "SpiceeChat/OkCupid-59k-Anonymized-Profiles"
HF_PROSOCIAL_DATASET = "allenai/prosocial-dialog"
HF_DIALOGUE_DATASET = "agentlans/multi-character-dialogue"
HF_ATOMIC_DATASET = "Estwld/atomic2020-origin"
HF_SOCIAL_IQA_DATASET = "allenai/social_i_qa"
HF_EMPATHETIC_DATASET = "facebook/empathetic_dialogues"
HF_EMOTION_DATASET = "dair-ai/emotion"
HF_CONVAI2_DATASET = "convai-challenge/conv_ai_2"

# ── Emergent mechanics ────────────────────────────────────────────────────────
# Emotional contagion — friendship threshold before emotion spreads between sims
CONTAGION_FRIENDSHIP_MIN = 35  # below this: no contagion
CONTAGION_MAX_STRENGTH = 0.30  # max emotion bleed at best-friend level (100)
CONTAGION_SKIP_EMOTIONS = {"neutral"}  # emotions that don't propagate

# Reputation gating — how much reputation shifts pair-selection scores
# Range: rep -100..+100 → adjustment -0.50..+0.25 (avoidance stronger than attraction)
REPUTATION_SCORE_SCALE = 200.0  # divisor; keeps adjustment in -0.5..+0.5 range
REPUTATION_BOOST_CAP = 0.25  # cap the upward boost (fame ≠ forced interaction)

# Memory bias — how much shared memory valence shifts pair-selection scores
MEMORY_BIAS_LOOKBACK = 6  # last N memories considered
MEMORY_BIAS_WEIGHT = 0.25  # max contribution to interaction score

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

# ── Autonomous self-care (free, no simoleons required) ────────────────────────
# Sleep — restores energy when critically low
SLEEP_ENERGY_THRESHOLD = 25  # energy < this → sim starts sleeping
SLEEP_ENERGY_RESTORE = 8.0  # energy units restored per tick while sleeping
SLEEP_WAKE_THRESHOLD = 70  # energy > this → sim wakes up
# Basic at-home needs (free fallback when shops unavailable/unaffordable)
HUNGER_HOME_THRESHOLD = 12  # hunger < this → eat something basic at home
HUNGER_HOME_RESTORE = 30.0
BLADDER_FLUSH_THRESHOLD = 10  # bladder < this → use bathroom (always free)
BLADDER_RESTORE = 90.0
HYGIENE_SHOWER_THRESHOLD = 20  # hygiene < this → quick shower at home
HYGIENE_RESTORE = 55.0

# ── Aging & life cycle ───────────────────────────────────────────────────────
TICKS_PER_YEAR = 50  # 1 in-game year = this many ticks
# full life (~75yr) = ~3750 ticks at delay=0
CHILD_BIRTH_CHANCE = 0.08  # probability per eligible couple per year
MIN_POPULATION = 1  # stop "until-death" run when below this

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

TRAIT_CATEGORY_GROUPS = {
    "personality": ["ambitious", "lazy", "creative", "genius", "geek", "bookworm"],
    "bonus": ["overachiever", "practice-focused learner", "self-assured"],
    "reward": ["fertile", "steel_bladder", "savvy_mentor", "socially_gifted"],
    "death": ["fire_affinity", "cold_affinity", "electric_aura", "haunting_presence"],
    "temporary": ["burnout", "inspired_streak", "grief_shock", "fearful"],
    "childhood_formative": [
        "rebellious_past",
        "caregiver_past",
        "explorer_past",
        "early_artist",
    ],
    "age_specific": ["calm_temperament", "intense_temperament", "wild", "inquisitive"],
    "lifestyle": ["active", "outdoors-oriented", "materialistic", "eco-focused"],
    "social": ["good", "evil", "mean", "family-oriented", "loner", "nosy"],
    "emotional": ["cheerful", "gloomy", "hot-headed", "romantic", "unflirty"],
    "aspirational": ["creative", "geek", "bookworm", "ambitious"],
}

AGE_TRAIT_CANDIDATES = {
    "infant": [
        "calm_temperament",
        "intense_temperament",
        "sensitive_temperament",
        "cautious_temperament",
        "highly_social_temperament",
        "high_movement_temperament",
    ],
    "toddler": [
        "independent",
        "clingy",
        "wild",
        "silly",
        "inquisitive",
        "social_charmer",
        "fussy",
        "angelic",
    ],
    "child": ["rebellious_past", "caregiver_past", "explorer_past", "early_artist"],
    "teen": ["rebellious_past", "overachiever", "skeptical", "competitive"],
}

DEALBREAKERS_POOL = [
    "smoking",
    "dishonesty",
    "anti-intellectualism",
    "aggression",
    "close-mindedness",
    "laziness",
    "rudeness",
]

LIKES_POOL = [
    "outdoor activities",
    "intellectual conversations",
    "cooking together",
    "live music",
    "art galleries",
    "spontaneous adventures",
    "cosy nights in",
    "deep philosophical talks",
    "sports events",
    "dancing",
    "hiking",
    "game nights",
    "volunteering",
    "stargazing",
    "road trips",
]

DISLIKES_POOL = [
    "loud parties",
    "small talk",
    "confrontation",
    "routine",
    "lateness",
    "arrogance",
    "indecisiveness",
    "pessimism",
    "materialism",
    "excessive social media use",
    "being ignored",
    "passive-aggressiveness",
    "recklessness",
    "narrow-mindedness",
]

# ── Celebrity / Fame ──────────────────────────────────────────────────────────
CELEBRITY_TIERS = {
    "none": (0, 20),
    "known": (20, 40),
    "star": (40, 70),
    "celebrity": (70, 90),
    "icon": (90, 100),
}
CELEBRITY_SCORE_DECAY = 0.2  # per tick when below threshold
CELEBRITY_INTERACTION_THRESHOLD = 40  # minimum score to unlock fan interactions

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
    "romantic": [
        "flirt",
        "compliment appearance",
        "ask on date",
        "hold hands",
        "give gift",
        "reassure partner",
    ],
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
    # ── Social ────────────────────────────────────────────────────────────────
    "charisma": {
        "max": 10,
        "category": "social",
        "unlocks": {
            3: "tell riveting story",
            7: "enchanting introduction",
            10: "inspire crowd",
        },
    },
    "comedy": {
        "max": 10,
        "category": "social",
        "unlocks": {
            3: "tell great joke",
            7: "roast expertly",
            10: "headline comedy show",
        },
    },
    "mischief": {
        "max": 10,
        "category": "social",
        "unlocks": {
            3: "pull prank",
            6: "spread rumour expertly",
            9: "voodoo manipulation",
        },
    },
    "parenting": {
        "max": 10,
        "category": "social",
        "unlocks": {3: "teach life lesson", 7: "resolve family conflict"},
    },
    "mixology": {
        "max": 10,
        "category": "social",
        "unlocks": {3: "craft signature cocktail", 8: "dazzle with bar tricks"},
    },
    "cleaning": {
        "max": 5,
        "category": "practical",
        "unlocks": {2: "deep clean room", 4: "sanitize surfaces"},
    },
    # ── Creative ──────────────────────────────────────────────────────────────
    "cooking": {
        "max": 10,
        "category": "creative",
        "unlocks": {
            3: "cook gourmet meal",
            7: "host dinner party",
            10: "masterchef performance",
        },
    },
    "gourmet_cooking": {
        "max": 10,
        "category": "creative",
        "unlocks": {4: "prepare elegant dish", 8: "restaurant-quality meal"},
    },
    "baking": {
        "max": 10,
        "category": "creative",
        "unlocks": {3: "bake custom cake", 7: "dessert masterpiece"},
    },
    "writing": {
        "max": 10,
        "category": "creative",
        "unlocks": {
            3: "share short story",
            6: "publish novel",
            10: "literary masterpiece",
        },
    },
    "painting": {
        "max": 10,
        "category": "creative",
        "unlocks": {
            2: "share artwork",
            5: "perform original piece",
            9: "gallery exhibition",
        },
    },
    "photography": {
        "max": 5,
        "category": "creative",
        "unlocks": {2: "share great photo", 4: "professional shoot"},
    },
    "guitar": {
        "max": 10,
        "category": "creative",
        "unlocks": {3: "serenade with guitar", 7: "perform live", 10: "write hit song"},
    },
    "piano": {
        "max": 10,
        "category": "creative",
        "unlocks": {3: "serenade on piano", 7: "concert performance"},
    },
    "violin": {
        "max": 10,
        "category": "creative",
        "unlocks": {3: "play emotional piece", 8: "orchestral performance"},
    },
    "singing": {
        "max": 10,
        "category": "creative",
        "unlocks": {3: "sing serenade", 7: "karaoke spotlight", 10: "record album"},
    },
    "dj_mixing": {
        "max": 10,
        "category": "creative",
        "unlocks": {3: "drop sick beat", 7: "headline DJ set"},
    },
    "dancing": {
        "max": 5,
        "category": "creative",
        "unlocks": {2: "show off dance moves", 4: "lead group dance"},
    },
    # ── Mental ────────────────────────────────────────────────────────────────
    "logic": {
        "max": 10,
        "category": "mental",
        "unlocks": {
            3: "debate topic",
            6: "teach logic puzzle",
            10: "grand chess gambit",
        },
    },
    "programming": {
        "max": 10,
        "category": "mental",
        "unlocks": {
            3: "build simple app",
            6: "hack playfully",
            10: "release open-source project",
        },
    },
    "rocket_science": {
        "max": 10,
        "category": "mental",
        "unlocks": {4: "explain rocket mechanics", 8: "launch rocket"},
    },
    "video_gaming": {
        "max": 10,
        "category": "mental",
        "unlocks": {3: "trash talk expertly", 7: "speedrun challenge"},
    },
    # ── Physical ──────────────────────────────────────────────────────────────
    "fitness": {
        "max": 10,
        "category": "physical",
        "unlocks": {4: "intense workout", 8: "run marathon", 10: "coach fitness class"},
    },
    "wellness": {
        "max": 10,
        "category": "physical",
        "unlocks": {3: "guided meditation", 7: "teach yoga class"},
    },
    # ── Practical ─────────────────────────────────────────────────────────────
    "handiness": {
        "max": 10,
        "category": "practical",
        "unlocks": {
            3: "repair expertly",
            7: "upgrade appliance",
            10: "build custom furniture",
        },
    },
    "gardening": {
        "max": 10,
        "category": "practical",
        "unlocks": {3: "share gardening tips", 7: "cultivate rare plant"},
    },
    "fishing": {
        "max": 10,
        "category": "practical",
        "unlocks": {3: "share fishing spot", 6: "catch rare fish"},
    },
    # ── Legacy ────────────────────────────────────────────────────────────────
    "creativity": {
        "max": 10,
        "category": "creative",
        "unlocks": {2: "share artwork", 5: "perform original piece"},
    },
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
