from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path


_CATALOG_PATH = Path(__file__).resolve().parent / "open_world_actions.json"

_ENERGY_INTENTS = {"clean", "laundry", "move", "tidy", "cook"}
_SOCIAL_INTENTS = {"social", "serve"}
_ROMANCE_BLOCKLIST = {"ring", "landline", "checkout"}


@lru_cache(maxsize=1)
def load_open_world_actions() -> list[dict]:
    if not _CATALOG_PATH.exists():
        return []
    payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    actions = payload.get("actions", []) if isinstance(payload, dict) else []
    if not isinstance(actions, list):
        return []
    return [a for a in actions if isinstance(a, dict) and a.get("action_text")]


def load_open_world_action_index() -> dict:
    actions = load_open_world_actions()
    by_intent: dict[str, list[str]] = {}
    for rec in actions:
        intent = str(rec.get("intent", "utility"))
        by_intent.setdefault(intent, []).append(str(rec.get("action_text", "")))
    return {"actions": actions, "by_intent": by_intent}


def normalize_venue_name(venue_name: str) -> str:
    v = (venue_name or "").lower().strip()
    if "home" in v or "house" in v:
        return "home"
    if "kitchen" in v or "restaurant" in v:
        return "kitchen"
    if "office" in v:
        return "office"
    if "shop" in v or "retail" in v or "shopping" in v:
        return "retail_store"
    if "bath" in v or "toilet" in v:
        return "bathroom"
    return "home"


def allow_action_for_state(action: dict, sim_a, relationship) -> bool:
    text = str(action.get("action_text", "")).lower()
    intent = str(action.get("intent", "utility")).lower()
    friendship = float(getattr(relationship, "friendship", 0.0) or 0.0)
    romance = float(getattr(relationship, "romance", 0.0) or 0.0)

    needs = getattr(sim_a, "needs", None)
    energy = float(getattr(needs, "energy", 50.0) or 50.0)
    social = float(getattr(needs, "social", 50.0) or 50.0)
    hunger = float(getattr(needs, "hunger", 50.0) or 50.0)

    if energy < 20 and intent in _ENERGY_INTENTS:
        return False
    if hunger < 20 and intent in {"clean", "tidy", "laundry", "craft"}:
        return False
    if social < 25 and intent in _SOCIAL_INTENTS and friendship < 15:
        return False
    if romance >= 70 and any(t in text for t in _ROMANCE_BLOCKLIST):
        return False
    if friendship < 10 and intent in {"social", "serve"}:
        return False
    return True


def sample_action_candidates(
    sim_a, sim_b, relationship, max_candidates: int = 3
) -> list[tuple[str, float]]:
    actions = load_open_world_actions()
    if not actions:
        return []

    venue_name = getattr(sim_a, "_current_venue_name", "")
    venue_tag = normalize_venue_name(venue_name)

    pool: list[dict] = []
    fallback: list[dict] = []
    for rec in actions:
        if not allow_action_for_state(rec, sim_a, relationship):
            continue
        if rec.get("venue_tag") == venue_tag:
            pool.append(rec)
        elif rec.get("venue_tag") in {"home", "office", "kitchen"}:
            fallback.append(rec)

    source = pool if pool else fallback
    if not source:
        return []

    picks = random.sample(source, k=min(max_candidates, len(source)))
    out: list[tuple[str, float]] = []
    for rec in picks:
        text = str(rec.get("action_text", "")).replace("_", " ").strip().lower()
        confidence = float(rec.get("confidence", 0.6) or 0.6)
        intent = str(rec.get("intent", "utility"))
        weight = 0.65 + confidence
        if intent in _SOCIAL_INTENTS:
            weight += 0.2
        out.append((text, max(0.15, min(2.2, weight))))
    return out
