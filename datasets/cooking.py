"""
datasets/cooking.py — Cooking skill social content.

Source: cstrathe435/Task2Dial — 353 recipe instruction dialogues
        (information giver + follower — recreates the teaching/hosting dynamic)

Diet compatibility for dinner party hosting:
  omnivore   → compatible with all
  vegetarian → conflict with meat-heavy dishes
  vegan      → conflict with any animal product
  pescatarian → conflict with meat (not fish)

Cooking skill gates:
  Lvl 3: "cook gourmet meal" → sample a recipe dialogue as context
  Lvl 7: "host dinner party" → diet compatibility checked vs. guests
"""
from __future__ import annotations

import random
from datasets.cache import cache_load, cache_save

_CACHE_KEY = "cooking_dialogs"
_HF_ID     = "cstrathe435/Task2Dial"
_MAX_LOAD  = 300

# Dish tags that conflict with certain diets
_DIET_CONFLICTS: dict[str, list[str]] = {
    "vegetarian": ["meat", "beef", "chicken", "pork", "lamb", "steak", "bacon"],
    "vegan":      ["meat", "beef", "chicken", "pork", "egg", "milk", "cheese",
                   "butter", "cream", "dairy", "honey"],
    "pescatarian": ["beef", "chicken", "pork", "lamb", "steak", "bacon"],
}


def load_cooking_dialogs() -> list[dict]:
    """Returns list of {recipe_name, dialog_excerpt} dicts."""
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached

    dialogs: list[dict] = []
    try:
        from datasets import load_dataset
        ds = load_dataset(_HF_ID, split="train", streaming=True, trust_remote_code=True)
        for row in ds:
            if len(dialogs) >= _MAX_LOAD:
                break
            recipe = (row.get("recipe_name") or row.get("dish") or
                      row.get("title") or "a gourmet dish").strip()
            dialog = row.get("dialog") or row.get("conversation") or row.get("utterances") or []
            if isinstance(dialog, list) and dialog:
                # Take first exchange
                lines = []
                for turn in dialog[:4]:
                    text = turn.get("text", turn) if isinstance(turn, dict) else str(turn)
                    lines.append(str(text).strip()[:150])
                excerpt = " / ".join(lines)
            else:
                excerpt = str(dialog)[:300]
            if recipe and excerpt:
                dialogs.append({"recipe": recipe, "dialog": excerpt})
        cache_save(_CACHE_KEY, dialogs)
    except Exception:
        pass
    return dialogs


def sample_recipe(cooking_skill: float) -> dict | None:
    dialogs = load_cooking_dialogs()
    return random.choice(dialogs) if dialogs else None


def check_diet_compatibility(recipe: dict, guest_diets: list[str]) -> list[str]:
    """Return list of guest diets that conflict with the recipe."""
    recipe_text = (recipe.get("recipe", "") + " " + recipe.get("dialog", "")).lower()
    conflicts = []
    for diet in guest_diets:
        conflict_words = _DIET_CONFLICTS.get(diet, [])
        if any(w in recipe_text for w in conflict_words):
            conflicts.append(diet)
    return conflicts


def format_cooking_interaction(recipe: dict, cooking_skill: float,
                                guest_diets: list[str] | None = None) -> str:
    conflicts = check_diet_compatibility(recipe, guest_diets or []) if guest_diets else []
    conflict_note = ""
    if conflicts:
        conflict_note = (f"\n⚠️ Diet conflict: recipe may not suit "
                         f"{', '.join(conflicts)} guest(s). "
                         f"This creates tension — adjust outcome accordingly.")
    skill_label = "masterfully" if cooking_skill >= 7 else "competently" if cooking_skill >= 3 else "ambitiously"
    return (
        f"[COOKING — Sim A {skill_label} prepares {recipe['recipe']}]\n"
        f"Teaching exchange: \"{recipe['dialog'][:250]}\""
        f"{conflict_note}"
    )
