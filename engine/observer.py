"""
engine/observer.py — Structured interaction observer.

Subscribes to the engine EventBus and writes every resolved interaction as a
JSONL record.  Each record is self-contained so the pattern miner can analyse
it without running the engine.

Usage:
    obs = InteractionObserver("reports/run.jsonl")
    obs.attach(engine)          # or obs.attach_bus(engine._bus)
    engine.run_tick() ...
    obs.close()
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.events import EventBus

# Prefix-tag → seed type label
_SEED_TAGS: list[tuple[str, str]] = [
    ("[TEASE]",               "tease"),
    ("[ROMANCE —",            "romance_flirt"),
    ("[ENCHANTING",           "rizz"),
    ("[SELF-DISCLOSURE —",    "self_disclosure"),
    ("[INTIMATE — SUGGESTIVE]","sensual"),
    ("[PARTNERS DYNAMICS]",   "intima"),
    ("[NSFW STARTER]",        "nsfw"),
    ("[MEMORY — RECALLED]",   "hippocorpus_recalled"),
    ("[MEMORY — RETOLD]",     "hippocorpus_retold"),
    ("[MEMORY — IMAGINED]",   "hippocorpus_imagined"),
    ("[DISCOVERY]",           "ccpe"),
    ("[HEALTH CONCERN]",      "health"),
    ("[DEEP SUPPORT]",        "mental_chat"),
    ("[COMMUNITY DILEMMA]",   "aita"),
    ("[EMOTIONAL INTELLIGENCE]","ei"),
    ("[CREATIVE WORK",        "creative"),
    ("[RECONCILIATION",       "counsel_chat"),
    ("[SELF-DISCLOSURE",      "self_disclosure"),
]


def _infer_seed_type(interaction: str) -> str:
    upper = interaction.upper()
    for tag, label in _SEED_TAGS:
        if tag.upper() in upper:
            return label
    return "catalog"


_KEYWORD_CATS: list[tuple[tuple[str, ...], str]] = [
    # Most specific first — order matters
    (("gaslight", "guilt trip", "love bomb", "silent treatment", "ultimatum",
      "manipulate", "darvo", "stonewalling"), "toxic"),
    (("reconcile", "resolution", "make amends", "forgive", "work through conflict",
      "fresh start", "own your part", "elephant in the room",
      "repair", "address the rift"), "repair"),
    (("comfort", "emotional support", "validate feeling", "sit in silence",
      "coping strategy", "mental wellbeing", "reach out", "seek comfort",
      "share feelings", "open up about"), "support"),
    (("reminisce", "recall", "shared memory", "old times", "origin story",
      "relive", "piece together", "nostalgic", "remember when",
      "look back", "those days"), "nostalgic"),
    (("debate", "argue about", "challenge assumption", "thought experiment",
      "counterargument", "defend position", "philosophy", "intellectual",
      "moral dilemma", "ethics", "logic", "learning insight", "insight",
      "what i learned", "ask thoughtful"), "intellectual"),
    (("cook together", "work out", "plan a trip", "collaborate", "play a game",
      "explore interest", "shared hobby", "fitness", "recipe", "exercise",
      "snowball", "build in snow", "build something", "snow fort",
      "artwork", "share art", "paint together", "draw together",
      "gourmet meal", "craft together", "build a",
      "store the", "tidy", "clean together", "fix together"), "activity"),
    (("financial", "money", "budget", "health scare", "symptom", "practical help",
      "troubleshoot", "problem-solving", "afford"), "practical"),
    (("preference", "find out", "learn about", "curious about", "what do you think",
      "tell me more", "ask follow", "discover", "thoughtful question",
      "meaningful question"), "discovery"),
    (("flirt", "serenade", "love note", "romantic", "ask on a date",
      "love language", "slow dance", "admire you"), "romantic"),
    (("tender", "longing", "private hope", "speak softly", "attachment",
      "relationship future", "shared vision"), "intimate"),
    (("confide", "share secret", "life advice", "apologize", "set boundary",
      "moral", "past regret", "admit mistake", "honest feedback",
      "values", "challenge belief"), "deep"),
    (("insult", "mock", "rumour", "passive aggress", "cold shoulder",
      "dismiss", "one-up", "backhanded", "cold treatment"), "mean"),
    (("joke", "tease", "banter", "impression", "pun", "roast", "quote from",
      "one-liner", "meme", "impersonate"), "funny"),
    (("celebration", "celebrate", "recommend", "invite", "catch up", "check in on",
      "ask about", "compliment", "story", "share joke", "borrow",
      "join the", "holiday"), "friendly"),
]


def _infer_category(interaction: str) -> str:
    # 1. Exact catalog match
    try:
        from config import INTERACTION_TYPES
        low = interaction.strip().lower()
        for cat, actions in INTERACTION_TYPES.items():
            if low in [a.lower() for a in actions]:
                return cat
    except Exception:
        pass

    # 2. Prefix tag match (dataset seeds)
    upper = interaction.upper()
    tag_cats = {
        "[TEASE]": "funny", "[ROMANCE": "romantic", "[SELF-DISCLOSURE": "deep",
        "[INTIMATE": "intimate", "[PARTNERS": "intimate", "[NSFW": "intimate",
        "[MEMORY": "nostalgic", "[DISCOVERY]": "discovery",
        "[HEALTH CONCERN]": "practical", "[DEEP SUPPORT]": "support",
        "[RECONCILIATION": "repair",
    }
    for tag, cat in tag_cats.items():
        if tag.upper() in upper:
            return cat

    # 3. Keyword fuzzy match — covers organic LLM-generated / dataset-seeded strings
    low = interaction.lower()
    for keywords, cat in _KEYWORD_CATS:
        if any(kw in low for kw in keywords):
            return cat

    # 4. Long descriptive strings (dataset seeds) default to friendly
    if len(interaction) > 60:
        return "friendly"

    return "unknown"


def _sim_snapshot(sim: Any, friendship: float, romance: float) -> dict:
    return {
        "id": sim.sim_id,
        "name": sim.name,
        "ocean": dict(sim.profile.get("ocean", {})),
        "traits": list(sim.profile.get("traits", [])),
        "aspiration": sim.profile.get("aspiration", ""),
        "age": sim.profile.get("age", 0),
        "friendship": round(float(friendship), 1),
        "romance": round(float(romance), 1),
        "emotion": getattr(sim.emotion, "dominant", "neutral"),
        "needs_social": round(float(getattr(sim.needs, "social", 50)), 1),
        "reputation": round(float(getattr(sim, "reputation_score", 0.0)), 1),
    }


class InteractionObserver:
    """Writes one JSONL record per resolved interaction."""

    BUFFER_SIZE = 64

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._buf: list[str] = []
        self._count = 0

    # ── Attach ────────────────────────────────────────────────────────────────

    def attach(self, engine: Any) -> "InteractionObserver":
        """Attach to a running SimEngine."""
        self.attach_bus(engine._bus)
        return self

    def attach_bus(self, bus: "EventBus") -> "InteractionObserver":
        bus.on("interaction_resolved", self._on_resolved)
        return self

    # ── Event handler ─────────────────────────────────────────────────────────

    def _on_resolved(
        self,
        sim_a: Any,
        sim_b: Any,
        result: dict,
        valence: float,
        friendship_delta: float = 0.0,
        romance_delta: float = 0.0,
        stage_before: str = "small_talk",
        stage_after: str = "small_talk",
        arc_mult: float = 1.0,
        tick: int = 0,
        interaction_id: str = "",
        interaction: str = "",
        **_: Any,
    ) -> None:
        try:
            # Get relationship context
            friendship = sim_a.profile.get("friendship", 0.0)
            romance = sim_a.profile.get("romance", 0.0)
            try:
                import engine.engine as _em
                eng = getattr(_em, "_current_engine", None)
                if eng:
                    rel = eng.relationships.get(sim_a.sim_id, sim_b.sim_id)
                    friendship = rel.friendship
                    romance = rel.romance
            except Exception:
                pass

            record = {
                "tick": tick,
                "interaction_id": interaction_id,
                "sim_a": _sim_snapshot(sim_a, friendship, romance),
                "sim_b": _sim_snapshot(sim_b, friendship, romance),
                "interaction": interaction,
                "category": _infer_category(interaction),
                "seed_type": _infer_seed_type(interaction),
                "valence": round(float(valence), 4),
                "friendship_delta": round(float(friendship_delta), 3),
                "romance_delta": round(float(romance_delta), 3),
                "emotion_a": result.get("emotion_a", "neutral"),
                "emotion_b": result.get("emotion_b", "neutral"),
                "stage_before": stage_before,
                "stage_after": stage_after,
                "stage_changed": stage_before != stage_after,
                "arc_mult": round(float(arc_mult), 3),
                "consent": getattr(sim_a, "_consent_state", {}).get(sim_b.sim_id, ""),
            }
            self._buf.append(json.dumps(record, ensure_ascii=False))
            self._count += 1
            if len(self._buf) >= self.BUFFER_SIZE:
                self._flush()
        except Exception:
            pass

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _flush(self) -> None:
        if self._buf:
            self._fh.write("\n".join(self._buf) + "\n")
            self._fh.flush()
            self._buf.clear()

    def close(self) -> int:
        self._flush()
        self._fh.close()
        return self._count

    def __enter__(self) -> "InteractionObserver":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
