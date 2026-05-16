"""
datasets/interests.py — Interest-specific dialogue content for all 15 ungrounded
interests in INTERESTS_POOL.

(cooking, fitness, travel already handled by datasets/cooking.py, fitness.py, travel.py)

Architecture:
  load_all_interests() → dict[interest_name, list[str]]
  sample_interest_seed(interest, data) → formatted interaction string | None

Each interest maps to 1-3 HuggingFace datasets. Content is cached under a single
"interest_seeds" key in .sim_cache/.

Scheduler integration:
  When sim_a and sim_b share ≥1 interest, 25% chance to route through here.
  Weight: 1.5 base + 0.3 if they share multiple interests (shared passion bonus).

Interest → primary dataset mapping
──────────────────────────────────
gaming      GEM/viggo + Estwld/steam_game_reviews_1m
music       m-a-p/Music-Instruct + baobaoh/13-dimensions-music-emotions
film        Tobi-AIs/TMDB-movies-dataset-2024 + Pikilit/movie_recommendations
writing     armanc/writing-feedback-dataset + tasyasari/creative-writing-stories
hiking      Rohit-Deshmukh/Hiking-Dataset + rjac/outdoors-conversations
sports      nickmuchi/sports-intent-classification + Stevenwudi/sportsgpt
yoga        SiddharthVarshney/mindfulness_wellbeing_conversations + ayushayt/YogaDataset
meditation  (same as yoga pool)
coding      smangrul/code-chat-assistant-v1 + iamtarun/python_code_instructions_18k_alpaca
art         keremberke/painting-style-classification + shivi/20k_creative_writing_and_art_prompts
photography Pikilit/photography_tutorial_conversations
dancing     Chaithanya-kutty/Dance-Style-Classification
gardening   Whispering-GPT/gardening_conversations + nbertagnolli/PlantDoc
reading     BrightData/Goodreads-Books + nlpkevinl/whatsthatbook
volunteering heegyu/prosocial-dialog-v2 + theblackcat102/ethical-issues-dialog
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "interest_seeds"
_MAX_PER_INTEREST = 600


# ── Per-interest loaders ──────────────────────────────────────────────────────

def _load_gaming(limit: int) -> list[str]:
    seeds: list[str] = []
    # GEM/viggo — structured game dialogue acts
    try:
        from datasets import load_dataset
        ds = load_dataset("GEM/viggo", split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            ref = row.get("references") or row.get("target") or row.get("text") or ""
            if isinstance(ref, list):
                ref = ref[0] if ref else ""
            ref = str(ref).strip()
            if 15 < len(ref) < 200:
                seeds.append(f"[GAMING] {ref}")
    except Exception:
        pass
    # Steam reviews — emotional opinions on games
    try:
        from datasets import load_dataset
        ds = load_dataset("Estwld/steam_game_reviews_1m", split="train",
                          streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= limit // 2:
                break
            review = (row.get("review") or row.get("text") or "").strip()
            if 20 < len(review) < 200 and len(seeds) < limit:
                seeds.append(f"[GAMING] {review[:180]}")
                count += 1
    except Exception:
        pass
    return seeds


def _load_music(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("m-a-p/Music-Instruct", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            q = (row.get("instruction") or row.get("input") or row.get("question") or "").strip()
            a = (row.get("output") or row.get("answer") or row.get("response") or "").strip()
            if q and a and 10 < len(q) < 150:
                seeds.append(f"[MUSIC] Q: {q[:120]} A: {a[:150]}")
    except Exception:
        pass
    # Genre fallback
    if len(seeds) < limit // 2:
        try:
            from datasets import load_dataset
            ds = load_dataset("nmeshcheriakova/music_genre_classification_dataset",
                              split="train", streaming=True, trust_remote_code=True)
            for row in ds:
                if len(seeds) >= limit:
                    break
                genre = (row.get("genre") or row.get("label") or "").strip()
                text  = (row.get("text") or row.get("lyrics") or row.get("description") or "").strip()
                if genre and text and len(text) > 20:
                    seeds.append(f"[MUSIC — {genre}] {text[:150]}")
        except Exception:
            pass
    return seeds


def _load_film(limit: int) -> list[str]:
    seeds: list[str] = []
    # TMDB — movie overviews as taste-profile seeds
    try:
        from datasets import load_dataset
        ds = load_dataset("Tobi-AIs/TMDB-movies-dataset-2024", split="train",
                          streaming=True, trust_remote_code=True)
        count = 0
        for row in ds:
            if count >= limit // 2:
                break
            title   = (row.get("title") or row.get("name") or "").strip()
            overview = (row.get("overview") or row.get("description") or "").strip()
            genres  = row.get("genres") or row.get("genre") or ""
            if isinstance(genres, list):
                genres = ", ".join(str(g) for g in genres)
            if title and overview and len(overview) > 20:
                seeds.append(f"[FILM] \"{title}\" ({genres}): {overview[:200]}")
                count += 1
    except Exception:
        pass
    # Movie recommendation dialogues
    try:
        from datasets import load_dataset
        ds = load_dataset("Pikilit/movie_recommendations", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            text = (row.get("text") or row.get("dialog") or row.get("conversation") or "").strip()
            if text and 20 < len(text) < 300:
                seeds.append(f"[FILM] {text[:280]}")
    except Exception:
        pass
    return seeds


def _load_writing(limit: int) -> list[str]:
    seeds: list[str] = []
    # Writing feedback dialogues
    try:
        from datasets import load_dataset
        ds = load_dataset("armanc/writing-feedback-dataset", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            text = (row.get("text") or row.get("story") or row.get("input") or "").strip()
            feedback = (row.get("feedback") or row.get("output") or "").strip()
            if text and feedback:
                seeds.append(
                    f"[WRITING] Story excerpt: \"{text[:150]}\"\n"
                    f"Feedback: \"{feedback[:150]}\""
                )
    except Exception:
        pass
    # Short creative stories
    try:
        from datasets import load_dataset
        ds = load_dataset("tasyasari/creative-writing-stories", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            story = (row.get("story") or row.get("text") or row.get("output") or "").strip()
            if story and 30 < len(story) < 400:
                seeds.append(f"[WRITING] \"{story[:350]}\"")
    except Exception:
        pass
    return seeds


def _load_hiking(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("Rohit-Deshmukh/Hiking-Dataset", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            name = (row.get("name") or row.get("trail_name") or "a trail").strip()
            diff = (row.get("difficulty") or row.get("level") or "moderate").strip()
            desc = (row.get("description") or row.get("text") or "").strip()
            seeds.append(f"[HIKING] {name} ({diff}): {desc[:180]}")
    except Exception:
        pass
    try:
        from datasets import load_dataset
        ds = load_dataset("rjac/outdoors-conversations", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            text = (row.get("text") or row.get("conversation") or "").strip()
            if text and 15 < len(text) < 300:
                seeds.append(f"[HIKING] {text[:280]}")
    except Exception:
        pass
    return seeds


def _load_sports(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("nickmuchi/sports-intent-classification", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            text = (row.get("text") or row.get("utterance") or "").strip()
            sport = (row.get("label") or row.get("sport") or "").strip()
            if text and 10 < len(text) < 200:
                seeds.append(f"[SPORTS — {sport}] {text}")
    except Exception:
        pass
    try:
        from datasets import load_dataset
        ds = load_dataset("Stevenwudi/sportsgpt", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            q = (row.get("question") or row.get("input") or "").strip()
            a = (row.get("answer") or row.get("output") or "").strip()
            if q and a and len(q) > 10:
                seeds.append(f"[SPORTS] {q[:120]} — {a[:120]}")
    except Exception:
        pass
    return seeds


def _load_yoga_meditation(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("SiddharthVarshney/mindfulness_wellbeing_conversations",
                          split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            text = (row.get("text") or row.get("conversation") or
                    row.get("utterance") or "").strip()
            if text and 15 < len(text) < 300:
                seeds.append(f"[YOGA/MEDITATION] {text[:280]}")
    except Exception:
        pass
    try:
        from datasets import load_dataset
        ds = load_dataset("ayushayt/YogaDataset", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            pose = (row.get("pose_name") or row.get("name") or "").strip()
            desc = (row.get("description") or row.get("text") or "").strip()
            if pose or desc:
                seeds.append(f"[YOGA] {pose}: {desc[:200]}" if pose else f"[YOGA] {desc[:200]}")
    except Exception:
        pass
    return seeds


def _load_coding(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("smangrul/code-chat-assistant-v1", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            for col in ["content", "text", "instruction", "input"]:
                val = row.get(col, "")
                if isinstance(val, list):
                    val = " ".join(str(v) for v in val)
                val = str(val).strip()
                if 20 < len(val) < 300:
                    seeds.append(f"[CODING] {val[:280]}")
                    break
    except Exception:
        pass
    try:
        from datasets import load_dataset
        ds = load_dataset("iamtarun/python_code_instructions_18k_alpaca", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            instruction = (row.get("instruction") or row.get("input") or "").strip()
            if instruction and 15 < len(instruction) < 200:
                seeds.append(f"[CODING] {instruction}")
    except Exception:
        pass
    return seeds


def _load_art(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("keremberke/painting-style-classification", "full",
                          split="train", streaming=True, trust_remote_code=True)
        seen_styles: set[str] = set()
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            style = (row.get("label") or row.get("style") or row.get("category") or "").strip()
            if style and style not in seen_styles:
                seen_styles.add(style)
                seeds.append(f"[ART] I'm really into {style} — there's something about the way it handles light and space.")
    except Exception:
        pass
    try:
        from datasets import load_dataset
        ds = load_dataset("shivi/20k_creative_writing_and_art_prompts", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            prompt = (row.get("prompt") or row.get("text") or "").strip()
            if prompt and 15 < len(prompt) < 200 and "art" in prompt.lower():
                seeds.append(f"[ART] {prompt}")
    except Exception:
        pass
    return seeds


def _load_photography(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("Pikilit/photography_tutorial_conversations", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            text = (row.get("text") or row.get("conversation") or
                    row.get("tutorial") or "").strip()
            if text and 15 < len(text) < 300:
                seeds.append(f"[PHOTOGRAPHY] {text[:280]}")
    except Exception:
        # Fallback: generated photography tips
        photography_tips = [
            "The golden hour just before sunset is everything — the light is so soft.",
            "Rule of thirds is the first thing I unlearn with every new subject.",
            "I've been shooting in manual mode for three years and I still second-guess the ISO.",
            "There's something about candid portraits that staged ones just can't replicate.",
            "I got obsessed with street photography after just one afternoon in the city.",
        ]
        seeds.extend(f"[PHOTOGRAPHY] {t}" for t in photography_tips)
    return seeds[:limit]


def _load_dancing(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("Chaithanya-kutty/Dance-Style-Classification", split="train",
                          streaming=True, trust_remote_code=True)
        seen: set[str] = set()
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            style = (row.get("label") or row.get("style") or row.get("dance_style") or "").strip()
            if style and style not in seen:
                seen.add(style)
                seeds.append(
                    f"[DANCING] I've been getting into {style} lately — "
                    f"the footwork takes forever to get right but it's so satisfying when it clicks."
                )
    except Exception:
        pass
    # Fallback generated seeds if dataset unavailable
    dance_styles = ["salsa", "hip-hop", "ballet", "contemporary", "swing", "bachata", "breakdancing"]
    for s in dance_styles:
        if len(seeds) < limit:
            seeds.append(f"[DANCING] My go-to is {s} — the rhythm just feels natural to me.")
    return seeds[:limit]


def _load_gardening(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("Whispering-GPT/gardening_conversations", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            text = (row.get("text") or row.get("conversation") or
                    row.get("utterance") or "").strip()
            if text and 15 < len(text) < 300:
                seeds.append(f"[GARDENING] {text[:280]}")
    except Exception:
        pass
    try:
        from datasets import load_dataset
        ds = load_dataset("nbertagnolli/PlantDoc", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            desc = (row.get("text") or row.get("description") or row.get("label") or "").strip()
            if desc and 10 < len(desc) < 200:
                seeds.append(f"[GARDENING — plant care] {desc[:180]}")
    except Exception:
        pass
    return seeds


def _load_reading(limit: int) -> list[str]:
    seeds: list[str] = []
    # Goodreads book recommendations
    try:
        from datasets import load_dataset
        ds = load_dataset("BrightData/Goodreads-Books", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            title  = (row.get("title") or row.get("book_title") or "").strip()
            author = (row.get("author") or row.get("authors") or "").strip()
            desc   = (row.get("description") or row.get("overview") or "").strip()
            genre  = (row.get("genre") or row.get("genres") or "").strip()
            if isinstance(author, list):
                author = author[0] if author else ""
            if title and desc:
                seeds.append(
                    f"[READING] \"{title}\" by {author} ({genre}): {desc[:180]}"
                )
    except Exception:
        pass
    # r/whatsthatbook — nostalgic half-memories
    try:
        from datasets import load_dataset
        ds = load_dataset("nlpkevinl/whatsthatbook", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            post = (row.get("post") or row.get("text") or row.get("query") or "").strip()
            if post and 20 < len(post) < 300:
                seeds.append(f"[READING — memory] \"{post[:250]}\"")
    except Exception:
        pass
    return seeds


def _load_volunteering(limit: int) -> list[str]:
    seeds: list[str] = []
    try:
        from datasets import load_dataset
        ds = load_dataset("heegyu/prosocial-dialog-v2", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit // 2:
                break
            text = (row.get("response") or row.get("text") or
                    row.get("utterance") or "").strip()
            if text and 15 < len(text) < 250:
                seeds.append(f"[VOLUNTEERING] {text[:230]}")
    except Exception:
        pass
    try:
        from datasets import load_dataset
        ds = load_dataset("theblackcat102/ethical-issues-dialog", split="train",
                          streaming=True, trust_remote_code=True)
        for row in ds:
            if len(seeds) >= limit:
                break
            q = (row.get("question") or row.get("dialog") or row.get("text") or "").strip()
            if q and 15 < len(q) < 250:
                seeds.append(f"[VOLUNTEERING — ethics] {q[:230]}")
    except Exception:
        pass
    return seeds


# ── Master loader ─────────────────────────────────────────────────────────────

_INTEREST_LOADERS: dict[str, callable] = {
    "gaming":       _load_gaming,
    "music":        _load_music,
    "film":         _load_film,
    "writing":      _load_writing,
    "hiking":       _load_hiking,
    "sports":       _load_sports,
    "yoga":         _load_yoga_meditation,
    "meditation":   _load_yoga_meditation,   # shares pool
    "coding":       _load_coding,
    "art":          _load_art,
    "photography":  _load_photography,
    "dancing":      _load_dancing,
    "gardening":    _load_gardening,
    "reading":      _load_reading,
    "volunteering": _load_volunteering,
}


def load_all_interests() -> dict[str, list[str]]:
    """
    Load content for all 15 ungrounded interests.
    Returns {interest_name: [formatted_seed_strings]}.
    Cached as a single JSON blob in .sim_cache/interest_seeds.json.
    """
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    data: dict[str, list[str]] = {}
    for interest, loader in _INTEREST_LOADERS.items():
        if interest == "meditation":   # shares yoga pool — filled after yoga loads
            continue
        try:
            items = loader(_MAX_PER_INTEREST)
            data[interest] = items
        except Exception:
            data[interest] = []

    # meditation shares yoga pool
    data["meditation"] = data.get("yoga", [])

    cache_save(_CACHE_KEY, data)
    return data


def sample_interest_seed(
    interest: str,
    data: dict[str, list[str]],
) -> str | None:
    """Return a formatted seed string for a specific interest."""
    pool = data.get(interest, [])
    return random.choice(pool) if pool else None


def format_interest_interaction(
    seed: str,
    sim_a_name: str,
    sim_b_name: str,
    shared: bool,
) -> str:
    """Wrap a raw interest seed in an interaction context string."""
    bond = (
        f"Both {sim_a_name} and {sim_b_name} share this interest — "
        f"lean into the mutual passion; this is a high-affinity bonding moment."
        if shared else
        f"{sim_a_name} is the enthusiast here. "
        f"Adjudicate based on {sim_b_name}'s openness and whether they engage or zone out."
    )
    return f"{seed}\n{bond}"
