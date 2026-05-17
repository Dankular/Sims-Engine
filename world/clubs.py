"""
world/clubs.py — Interest-based social groups (Clubs).

Clubs are groups of 3–6 sims who share at least one interest and meet
regularly at a specific venue. When a club meeting fires, all present
members get a group interaction seeded from their shared interest, with
friendship bonuses. Club rules bias interaction type selection.

ClubManager.form_clubs() is called at engine init.
ClubManager.tick()       is called every CLUB_MEETING_INTERVAL ticks.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

CLUB_MEETING_INTERVAL = 15   # ticks between meeting attempts
CLUB_MIN_MEMBERS = 3
CLUB_MAX_MEMBERS = 6

# Club rule → interaction weight modifier
CLUB_RULE_INTERACTION_BONUS: dict[str, dict[str, float]] = {
    "always_be_friendly":  {"friendly": 2.0},
    "no_mean_interactions":{"mean": 0.0},
    "encourage_romance":   {"romantic": 1.8, "flirt": 2.0},
    "share_jokes_often":   {"funny": 2.5},
    "deep_conversations":  {"deep": 2.0},
    "skill_sharing":       {"mentor": 2.0, "teach": 2.0},
}

# Interest → default club rule
_INTEREST_RULES: dict[str, str] = {
    "fitness":      "always_be_friendly",
    "cooking":      "share_jokes_often",
    "reading":      "deep_conversations",
    "art":          "deep_conversations",
    "music":        "share_jokes_often",
    "gaming":       "share_jokes_often",
    "travel":       "always_be_friendly",
    "meditation":   "deep_conversations",
    "writing":      "deep_conversations",
    "coding":       "skill_sharing",
    "photography":  "always_be_friendly",
    "film":         "deep_conversations",
    "sports":       "always_be_friendly",
    "gardening":    "always_be_friendly",
    "volunteering": "always_be_friendly",
}

# Interest → suitable meeting venues
_INTEREST_VENUES: dict[str, str] = {
    "fitness":    "gym",
    "cooking":    "home (1:1)",
    "reading":    "library",
    "art":        "library",
    "music":      "house party",
    "gaming":     "home (1:1)",
    "travel":     "coffee shop",
    "meditation": "park",
    "writing":    "coffee shop",
    "coding":     "coffee shop",
    "photography":"park",
    "film":       "coffee shop",
    "sports":     "gym",
    "gardening":  "park",
    "volunteering":"park",
}


@dataclass
class Club:
    club_id: str
    name: str
    interest: str
    meeting_venue: str
    member_ids: list[str]
    rule: str = "always_be_friendly"
    founded_tick: int = 0
    last_meeting_tick: int = -CLUB_MEETING_INTERVAL


class ClubManager:
    def __init__(self) -> None:
        self.clubs: list[Club] = []
        self._sim_clubs: dict[str, list[str]] = {}  # sim_id → [club_id]

    # ── Setup ─────────────────────────────────────────────────────────────────

    def form_clubs(self, sims: list["Sim"], current_tick: int = 0) -> None:
        """
        Auto-form clubs from sims sharing at least one interest.
        Called once at engine init; also called when new sims join the world.
        """
        from collections import defaultdict
        interest_groups: dict[str, list[str]] = defaultdict(list)
        for sim in sims:
            for interest in sim.profile.get("interests", []):
                interest_groups[interest].append(sim.sim_id)

        for interest, sim_ids in interest_groups.items():
            if len(sim_ids) < CLUB_MIN_MEMBERS:
                continue
            # Check if a club for this interest already exists
            if any(c.interest == interest for c in self.clubs):
                continue

            members = sim_ids[:CLUB_MAX_MEMBERS]
            venue   = _INTEREST_VENUES.get(interest, "coffee shop")
            rule    = _INTEREST_RULES.get(interest, "always_be_friendly")
            name    = f"The {interest.title()} Club"
            club    = Club(
                club_id=uuid.uuid4().hex[:8],
                name=name,
                interest=interest,
                meeting_venue=venue,
                member_ids=members,
                rule=rule,
                founded_tick=current_tick,
            )
            self.clubs.append(club)
            for sid in members:
                self._sim_clubs.setdefault(sid, []).append(club.club_id)

    def get_clubs_for_sim(self, sim_id: str) -> list[Club]:
        ids = self._sim_clubs.get(sim_id, [])
        return [c for c in self.clubs if c.club_id in ids]

    def share_club(self, sim_a_id: str, sim_b_id: str) -> Club | None:
        """Return a shared club if both sims are members, else None."""
        clubs_a = set(self._sim_clubs.get(sim_a_id, []))
        clubs_b = set(self._sim_clubs.get(sim_b_id, []))
        shared  = clubs_a & clubs_b
        if not shared:
            return None
        club_id = next(iter(shared))
        return next((c for c in self.clubs if c.club_id == club_id), None)

    def interaction_weight_mods(self, sim_a_id: str, sim_b_id: str) -> dict[str, float]:
        """Return interaction weight modifiers if both sims share a club."""
        club = self.share_club(sim_a_id, sim_b_id)
        if club is None:
            return {}
        return CLUB_RULE_INTERACTION_BONUS.get(club.rule, {})

    def pair_score_bonus(self, sim_a_id: str, sim_b_id: str) -> float:
        """Pair-selection bonus for sims in the same club."""
        return 0.20 if self.share_club(sim_a_id, sim_b_id) else 0.0

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        """
        Attempt a club meeting for each club whose interval has elapsed.
        A meeting triggers a group interaction for all active members.
        """
        tick = engine.tick_count
        for club in self.clubs:
            if tick - club.last_meeting_tick < CLUB_MEETING_INTERVAL:
                continue
            present = [
                engine._sim_lookup[sid]
                for sid in club.member_ids
                if sid in engine._sim_lookup
                and not getattr(engine._sim_lookup[sid], "_sleeping", False)
            ]
            if len(present) < 2:
                continue

            club.last_meeting_tick = tick
            self._run_meeting(club, present, engine)

    def _run_meeting(self, club: Club, members: list["Sim"], engine: "SimEngine") -> None:
        """Run a club meeting: boost social needs and queue one interaction."""
        import logging
        logger = logging.getLogger(__name__)

        # Venue swap for the duration of the meeting
        from world.venues import VENUES
        venue = next(
            (v for v in VENUES if v.get("name", "") == club.meeting_venue),
            engine._venue,
        )

        # All members get a small social need boost (just being there)
        for sim in members:
            sim.needs.restore("social", 5.0)
            sim.needs.restore("fun",    3.0)

        # Queue one LLM interaction for the two most socially eager members
        from engine.scheduler import pick_interaction_pair, choose_interaction
        pair = pick_interaction_pair(members, engine.relationships)
        if pair:
            sim_a, sim_b = pair
            # Bias toward club's preferred interaction type
            rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
            sim_a._current_venue_name = club.meeting_venue
            interaction = choose_interaction(
                sim_a, sim_b, rel, engine.tick_count, engine._datasets
            )
            engine._submit_interaction(sim_a, sim_b, interaction, venue)
            logger.info(
                "[Club] %s meeting at %s — %s → %s  [%s]",
                club.name, club.meeting_venue, sim_a.name, sim_b.name, interaction,
            )

        engine._bus.emit(
            "club_meeting",
            club_name=club.name,
            interest=club.interest,
            members=[m.name for m in members],
            tick=engine.tick_count,
        )
