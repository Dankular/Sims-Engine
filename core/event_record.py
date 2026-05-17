"""
core/event_record.py — Life event data model.

LifeEvent is the canonical record for any significant occurrence in a sim's life.
It captures who was involved, the emotional/social weight, how widely it is known,
and the full set of downstream consequences to apply.

All narrative systems (marriage, pregnancy, career, illness, drama, etc.) produce
LifeEvent objects that flow through EventEngine.process(), which applies consequences
and propagates visibility through the social graph.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ── Visibility levels (ordered: private < witnessed < household < club < public) ──

class Visibility:
    PRIVATE   = "private"    # only the involved sims know
    WITNESSED = "witnessed"  # sims present at same venue also know
    HOUSEHOLD = "household"  # all household members know
    CLUB      = "club"       # club members of involved sims know
    PUBLIC    = "public"     # everyone knows (enters gossip graph)


# ── Event type constants ────────────────────────────────────────────────────────

class EventType:
    # Life milestones
    BIRTH         = "birth"
    DEATH         = "death"
    MARRIAGE      = "marriage"
    DIVORCE       = "divorce"
    BREAKUP       = "breakup"
    MOVING_OUT    = "moving_out"
    # Career
    JOB_LOSS      = "job_loss"
    PROMOTION     = "promotion"
    GIG_SUCCESS   = "gig_success"
    # Health
    ILLNESS       = "illness"
    RECOVERY      = "recovery"
    HEALTH_SCARE  = "health_scare"
    # Social
    SCANDAL       = "scandal"
    REDEMPTION    = "redemption"
    RIVALRY       = "rivalry"
    RECONCILIATION= "reconciliation"
    # Achievement
    WISH_FULFILLED= "wish_fulfilled"
    MILESTONE     = "milestone"
    CRAFTED_ITEM  = "crafted_item"
    PROPERTY_BOUGHT = "property_bought"
    # Calendar
    BIRTHDAY      = "birthday"
    ANNIVERSARY   = "anniversary"
    HOLIDAY       = "holiday"
    # Drama
    RANDOM_DRAMA  = "random_drama"
    LLM_SUGGESTED = "llm_suggested"


# ── Consequence spec ────────────────────────────────────────────────────────────

@dataclass
class EventConsequences:
    """All downstream effects to apply when an event fires."""

    # (sim_id, moodlet_key) — moodlet added to that sim
    moodlets: list[tuple[str, str]] = field(default_factory=list)

    # (sim_a_id, sim_b_id, sentiment_name) — sentiment added to the A→B relationship
    sentiments: list[tuple[str, str, str]] = field(default_factory=list)

    # (sim_a_id, sim_b_id, friendship_delta, romance_delta)
    relationship_deltas: list[tuple[str, str, float, float]] = field(default_factory=list)

    # (sim_id, rep_delta)
    reputation_deltas: list[tuple[str, float]] = field(default_factory=list)

    # (sim_id, want_description)
    wants: list[tuple[str, str]] = field(default_factory=list)

    # (sim_id, fear_label)
    fears: list[tuple[str, str]] = field(default_factory=list)

    # (sim_id, interaction_string) — added to sim._unlocked_interactions
    interactions_unlocked: list[tuple[str, str]] = field(default_factory=list)

    # (sim_id, interaction_string) — added to sim._blocked_interactions (new field)
    interactions_blocked: list[tuple[str, str]] = field(default_factory=list)

    # (sim_id, emotion, intensity, duration)
    emotions: list[tuple[str, str, float, int]] = field(default_factory=list)

    # Celebrity score delta per sim: (sim_id, delta)
    celebrity_deltas: list[tuple[str, float]] = field(default_factory=list)

    def merge(self, other: "EventConsequences") -> "EventConsequences":
        """Combine two consequence sets (used for dual-sided events)."""
        return EventConsequences(
            moodlets             = self.moodlets             + other.moodlets,
            sentiments           = self.sentiments           + other.sentiments,
            relationship_deltas  = self.relationship_deltas  + other.relationship_deltas,
            reputation_deltas    = self.reputation_deltas    + other.reputation_deltas,
            wants                = self.wants                + other.wants,
            fears                = self.fears                + other.fears,
            interactions_unlocked= self.interactions_unlocked+ other.interactions_unlocked,
            interactions_blocked = self.interactions_blocked + other.interactions_blocked,
            emotions             = self.emotions             + other.emotions,
            celebrity_deltas     = self.celebrity_deltas     + other.celebrity_deltas,
        )


# ── LifeEvent ───────────────────────────────────────────────────────────────────

@dataclass
class LifeEvent:
    """
    A significant occurrence in a sim's life.

    Created by trigger detectors or existing narrative systems,
    processed by EventEngine.process() which applies consequences
    and propagates visibility through the social graph.
    """
    event_id:           str
    event_type:         str                  # EventType constant
    primary_sim_id:     str                  # main subject
    secondary_sim_ids:  list[str]            # other directly involved sims
    narrative:          str                  # human-readable summary
    tick:               int

    # Visibility
    visibility:         str = Visibility.PRIVATE
    known_to:           set[str] = field(default_factory=set)  # sim_ids who know

    # Emotional weight
    valence:            float = 0.0          # -1.0..+1.0 (positive/negative event)
    intensity:          float = 0.5          # 0.0..1.0 (how impactful)

    # Duration of effects
    duration_ticks:     int = 20             # -1 = permanent
    expires_tick:       int = -1             # set at process time

    # Downstream effects
    consequences:       EventConsequences = field(default_factory=EventConsequences)

    # Source
    source:             str = ""             # e.g. "llm_suggested", "trigger:need_based"

    @staticmethod
    def make(
        event_type: str,
        primary_sim_id: str,
        narrative: str,
        tick: int,
        secondary_sim_ids: list[str] | None = None,
        visibility: str = Visibility.PRIVATE,
        valence: float = 0.0,
        intensity: float = 0.5,
        duration_ticks: int = 20,
        consequences: EventConsequences | None = None,
        source: str = "",
    ) -> "LifeEvent":
        ev = LifeEvent(
            event_id          = uuid.uuid4().hex[:10],
            event_type        = event_type,
            primary_sim_id    = primary_sim_id,
            secondary_sim_ids = secondary_sim_ids or [],
            narrative         = narrative,
            tick              = tick,
            visibility        = visibility,
            known_to          = {primary_sim_id} | set(secondary_sim_ids or []),
            valence           = valence,
            intensity         = intensity,
            duration_ticks    = duration_ticks,
            expires_tick      = tick + duration_ticks if duration_ticks > 0 else -1,
            consequences      = consequences or EventConsequences(),
            source            = source,
        )
        return ev
