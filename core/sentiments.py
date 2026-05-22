"""
core/sentiments.py — Named, persistent emotional tags on relationships.

Sentiments are distinct from the generic memory store: each is a specific,
named social event that a sim "carries" about another for N ticks, and that
actively gates or unlocks interaction types in the scheduler.

Trigger mapping:  _apply_resolved detects patterns in (interaction, valence,
                  result) and calls add_sentiment(rel, name, tick).
Decay:            engine run_tick calls decay_sentiments(rel, tick) every tick.
Gating:           choose_interaction checks blocked_by / unlocked_by.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.relationships import RelationshipRecord


# ── Sentiment catalogue ───────────────────────────────────────────────────────


@dataclass
class SentimentDef:
    valence: float  # emotional charge: -1.0..+1.0
    decay_ticks: int  # how many ticks until the sentiment fades (−1 = permanent)
    blocks: list[str] = field(default_factory=list)  # interaction types blocked
    unlocks: list[str] = field(default_factory=list)  # interaction types unlocked


SENTIMENT_CATALOGUE: dict[str, SentimentDef] = {
    # ── Positive ──────────────────────────────────────────────────────────────
    "first_kiss": SentimentDef(+1.0, 300, unlocks=["kiss", "embrace", "hold hands"]),
    "first_love": SentimentDef(+1.0, 600, unlocks=["express love", "propose marriage"]),
    "saved_me": SentimentDef(+0.9, 400, unlocks=["deep emotional talk", "confide"]),
    "shared_triumph": SentimentDef(+0.8, 150, unlocks=["celebrate together"]),
    "inspired_me": SentimentDef(+0.8, 250, unlocks=["deep conversation", "mentor"]),
    "reconciled": SentimentDef(+0.7, 200, unlocks=["confide", "hug"]),
    "childhood_bond": SentimentDef(+0.6, -1, unlocks=["share memory", "confide"]),
    "gratitude": SentimentDef(+0.7, 100, unlocks=["express gratitude"]),
    "reliable_partner": SentimentDef(+0.8, 220, unlocks=["confide", "plan together"]),
    # ── Negative ──────────────────────────────────────────────────────────────
    "betrayal": SentimentDef(
        -1.0, 200, blocks=["share secret", "confide", "express love"]
    ),
    "heartbreak": SentimentDef(
        -0.9, 300, blocks=["flirt", "hold hands", "kiss", "express love"]
    ),
    "held_grudge": SentimentDef(-0.7, 150, blocks=["flirt", "compliment"]),
    "embarrassed_me": SentimentDef(
        -0.6, 100, blocks=["joke", "share story", "tell story"]
    ),
    "jealousy_drama": SentimentDef(-0.5, 80, blocks=["compliment appearance"]),
    "rivalry_formed": SentimentDef(
        -0.8,
        250,
        blocks=["deep conversation", "confide"],
        unlocks=["rivalry_escalation", "confront"],
    ),
    "cheated_on_me": SentimentDef(
        -1.0, -1, blocks=["flirt", "kiss", "hold hands", "express love"]
    ),
    "lied_to_me": SentimentDef(-0.8, 180, blocks=["confide", "share secret"]),
    "financial_strain": SentimentDef(
        -0.7, 180, blocks=["invest together", "share secret"]
    ),
}


# ── SentimentRecord ───────────────────────────────────────────────────────────


@dataclass
class SentimentRecord:
    name: str  # key into SENTIMENT_CATALOGUE
    added_tick: int
    expires_tick: int  # −1 = never expires
    source: str = ""  # interaction string that triggered this


# ── Relationship helpers ──────────────────────────────────────────────────────


def add_sentiment(
    rel: "RelationshipRecord",
    name: str,
    current_tick: int,
    source: str = "",
    allow_duplicate: bool = False,
) -> bool:
    """
    Add a sentiment to a relationship record.
    Returns True if added, False if skipped (duplicate and allow_duplicate=False).
    """
    if not hasattr(rel, "sentiments"):
        rel.sentiments = []

    defn = SENTIMENT_CATALOGUE.get(name)
    if defn is None:
        return False

    if not allow_duplicate:
        existing_names = {s.name for s in rel.sentiments}
        if name in existing_names:
            return False

    expires = current_tick + defn.decay_ticks if defn.decay_ticks > 0 else -1
    rel.sentiments.append(
        SentimentRecord(
            name=name, added_tick=current_tick, expires_tick=expires, source=source
        )
    )
    return True


def decay_sentiments(rel: "RelationshipRecord", current_tick: int) -> list[str]:
    """Remove expired sentiments. Returns list of expired names for logging."""
    if not hasattr(rel, "sentiments"):
        rel.sentiments = []
        return []
    expired = [
        s.name
        for s in rel.sentiments
        if s.expires_tick != -1 and current_tick >= s.expires_tick
    ]
    rel.sentiments = [
        s
        for s in rel.sentiments
        if s.expires_tick == -1 or current_tick < s.expires_tick
    ]
    return expired


def active_sentiments(rel: "RelationshipRecord") -> list[SentimentRecord]:
    return getattr(rel, "sentiments", [])


def is_interaction_blocked(rel: "RelationshipRecord", interaction: str) -> bool:
    """Return True if any active sentiment blocks this interaction."""
    inter_lower = interaction.lower()
    for sentiment in active_sentiments(rel):
        defn = SENTIMENT_CATALOGUE.get(sentiment.name)
        if defn and any(b in inter_lower for b in defn.blocks):
            return True
    return False


def sentiment_unlocked_interactions(rel: "RelationshipRecord") -> list[str]:
    """Return all interactions currently unlocked by active sentiments."""
    unlocked = []
    for sentiment in active_sentiments(rel):
        defn = SENTIMENT_CATALOGUE.get(sentiment.name)
        if defn:
            unlocked.extend(defn.unlocks)
    return unlocked


def sentiment_valence_bonus(rel: "RelationshipRecord") -> float:
    """Sum of active sentiment valences — used to modulate adjudicator deltas."""
    return sum(
        SENTIMENT_CATALOGUE[s.name].valence
        for s in active_sentiments(rel)
        if s.name in SENTIMENT_CATALOGUE
    )


# ── Trigger detection ─────────────────────────────────────────────────────────


def detect_sentiment(
    interaction: str,
    valence: float,
    result: dict,
    friendship: float,
    romance: float,
    current_tick: int,
    sim_a=None,
    sim_b=None,
) -> list[str]:
    """
    Inspect a resolved interaction and return a list of sentiment names to add.
    Called from _apply_resolved in engine.py.
    """
    triggered: list[str] = []
    inter = interaction.lower()
    reaction = result.get("sim_b_reaction", "").lower()
    mem_tag = result.get("memory_tag", "").lower()

    # ── Positive triggers ──────────────────────────────────────────────────
    if "kiss" in inter and valence > 0.5 and romance >= 20:
        triggered.append("first_kiss")

    if (
        ("express love" in inter or "express_love" in inter)
        and valence > 0.6
        and romance >= 55
    ):
        triggered.append("first_love")

    if (
        ("comfort" in inter or "support" in inter)
        and valence > 0.7
        and friendship >= 50
    ):
        triggered.append("saved_me")

    if ("celebrate" in inter or "shared triumph" in mem_tag) and valence > 0.6:
        triggered.append("shared_triumph")

    if ("inspire" in inter or "mentor" in inter) and valence > 0.6:
        triggered.append("inspired_me")

    if ("apologise" in inter or "reconcile" in inter) and valence > 0.5:
        triggered.append("reconciled")

    if ("thank" in reaction or "grateful" in reaction) and valence > 0.6:
        triggered.append("gratitude")

    # ── Negative triggers ──────────────────────────────────────────────────
    if "secret" in inter and valence < -0.5:
        triggered.append("betrayal")

    if ("break" in inter and "up" in inter) or ("heartbreak" in mem_tag):
        triggered.append("heartbreak")

    grudge_threshold = -0.6
    if sim_a is not None:
        traits = set(getattr(sim_a, "profile", {}).get("traits", []))
        if "hot-headed" in traits or "jealous" in traits:
            grudge_threshold = -0.45
        if "cheerful" in traits or "good" in traits:
            grudge_threshold = -0.7

    if valence < grudge_threshold and friendship > 30:
        triggered.append("held_grudge")

    if "embarrass" in inter or ("embarrass" in reaction and valence < -0.3):
        triggered.append("embarrassed_me")

    if "jealous" in reaction or "jealous" in mem_tag:
        triggered.append("jealousy_drama")

    if ("rival" in inter or "rivalry" in mem_tag) and valence < -0.4:
        triggered.append("rivalry_formed")

    if ("lie" in reaction or "cheat" in mem_tag) and valence < -0.6:
        triggered.append("lied_to_me")

    if ("cheat" in inter or "infidel" in mem_tag) and romance > 40 and valence < -0.5:
        triggered.append("cheated_on_me")

    return triggered
