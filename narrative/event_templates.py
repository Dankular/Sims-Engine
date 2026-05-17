"""
narrative/event_templates.py — EventConsequences templates for each life event type.

Each function takes the involved sim IDs and returns a fully-specified
EventConsequences object. Templates capture the social logic of what
each event type means for everyone involved.

Usage:
    consequences = build_consequences(event_type, primary_id, secondary_ids, engine)
"""
from __future__ import annotations

from core.event_record import EventConsequences, EventType, Visibility
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine


def build_consequences(
    event_type: str,
    primary_id: str,
    secondary_ids: list[str],
    engine: "SimEngine",
    extra: dict | None = None,
) -> EventConsequences:
    """Dispatch to the right template. extra carries event-specific data."""
    extra = extra or {}
    fn = _TEMPLATES.get(event_type, _default)
    return fn(primary_id, secondary_ids, engine, extra)


# ── Helper ─────────────────────────────────────────────────────────────────────

def _all_household_ids(sim_id: str, engine: "SimEngine") -> list[str]:
    sim = engine._sim_lookup.get(sim_id)
    if not sim or not sim.household_id:
        return []
    for hh in engine.households:
        if hh.id == sim.household_id:
            return [mid for mid in hh.member_ids if mid != sim_id]
    return []


def _close_friends(sim_id: str, engine: "SimEngine", threshold: float = 55.0) -> list[str]:
    return [
        o.sim_id for o in engine.sims
        if o.sim_id != sim_id
        and engine.relationships.get(sim_id, o.sim_id).friendship >= threshold
    ]


# ── Templates ──────────────────────────────────────────────────────────────────

def _birth(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    child_id = extra.get("child_id", "")
    # Both parents
    for sid in [primary_id] + secondary_ids[:1]:
        c.moodlets.append((sid, "proud"))
        c.emotions.append((sid, "joy",   1.0, 20))
        c.emotions.append((sid, "pride", 0.8, 15))
        c.reputation_deltas.append((sid, 3.0))
        c.celebrity_deltas.append((sid, 1.0))
        c.wants.append((sid, "nurture and protect my child"))
    # Household members feel joy
    for hid in _all_household_ids(primary_id, engine):
        c.moodlets.append((hid, "just_had_fun"))
        c.emotions.append((hid, "joy", 0.5, 8))
    # Relationship between parents strengthens
    if len(secondary_ids) >= 1:
        c.relationship_deltas.append((primary_id, secondary_ids[0], 10.0, 5.0))
    return c


def _death(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    # Close friends grieve
    for fid in _close_friends(primary_id, engine, threshold=50):
        c.moodlets.append((fid, "heartbroken"))
        c.emotions.append((fid, "grief",   0.8, 15))
        c.emotions.append((fid, "sadness", 0.6, 10))
        c.sentiments.append((fid, primary_id, "heartbreak"))
        c.relationship_deltas.append((fid, primary_id, -5.0, -3.0))  # loss decay
    # Household members
    for hid in _all_household_ids(primary_id, engine):
        if hid not in [s for s, _ in c.moodlets]:
            c.moodlets.append((hid, "heartbroken"))
            c.emotions.append((hid, "grief", 0.9, 20))
    # Interactions unlocked for everyone who knew the deceased
    for fid in _close_friends(primary_id, engine, threshold=30):
        c.interactions_unlocked.append((fid, "share memories of the departed"))
        c.interactions_unlocked.append((fid, "offer condolences"))
    return c


def _marriage(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    partner_id = secondary_ids[0] if secondary_ids else None
    for sid in [primary_id] + ([partner_id] if partner_id else []):
        c.moodlets.append((sid, "newly_wed"))
        c.emotions.append((sid, "love",      1.0, 30))
        c.emotions.append((sid, "optimism",  0.8, 20))
        c.reputation_deltas.append((sid, 5.0))
        c.celebrity_deltas.append((sid, 2.0))
        c.wants.append((sid, "build a life together"))
        c.interactions_unlocked.append((sid, "romantic dinner"))
        c.interactions_unlocked.append((sid, "slow dance"))
    if partner_id:
        c.relationship_deltas.append((primary_id, partner_id, 15.0, 20.0))
        c.sentiments.append((primary_id, partner_id, "first_love"))
        c.sentiments.append((partner_id, primary_id, "first_love"))
    # Guests feel joy
    for guest_id in secondary_ids[1:]:
        c.moodlets.append((guest_id, "just_had_fun"))
        c.emotions.append((guest_id, "joy", 0.6, 8))
        c.interactions_unlocked.append((guest_id, "congratulate on marriage"))
    return c


def _divorce(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    partner_id = secondary_ids[0] if secondary_ids else None
    for sid in [primary_id] + ([partner_id] if partner_id else []):
        c.moodlets.append((sid, "heartbroken"))
        c.emotions.append((sid, "grief",         1.0, 15))
        c.emotions.append((sid, "disappointment",0.7, 10))
        c.reputation_deltas.append((sid, -3.0))
        c.fears.append((sid, "fear of commitment"))
    if partner_id:
        c.relationship_deltas.append((primary_id, partner_id, -20.0, -30.0))
        c.sentiments.append((primary_id, partner_id, "heartbreak"))
        c.sentiments.append((partner_id, primary_id, "heartbreak"))
        c.interactions_blocked.append((primary_id, "propose marriage"))
        c.interactions_blocked.append((partner_id, "propose marriage"))
    # Household — drama_witnessed moodlet
    for hid in _all_household_ids(primary_id, engine):
        c.moodlets.append((hid, "drama_witnessed"))
        c.emotions.append((hid, "surprise", 0.5, 5))
    return c


def _breakup(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    ex_id = secondary_ids[0] if secondary_ids else None
    # Dumped sim (secondary)
    if ex_id:
        c.moodlets.append((ex_id, "heartbroken"))
        c.emotions.append((ex_id, "grief",   0.8, 10))
        c.emotions.append((ex_id, "sadness", 0.6,  8))
        c.sentiments.append((ex_id, primary_id, "heartbreak"))
        c.reputation_deltas.append((primary_id, -2.0))  # initiator loses a bit
        c.relationship_deltas.append((primary_id, ex_id, -10.0, -20.0))
        c.interactions_blocked.append((primary_id, "flirt"))
        c.interactions_blocked.append((ex_id, "flirt"))
    # Primary sim
    c.moodlets.append((primary_id, "uncomfortable"))
    c.emotions.append((primary_id, "nervousness", 0.4, 5))
    c.wants.append((primary_id, "move on and find happiness"))
    return c


def _job_loss(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "stressed"))
    c.moodlets.append((primary_id, "broke"))
    c.emotions.append((primary_id, "disappointment", 0.8, 10))
    c.emotions.append((primary_id, "nervousness",    0.7,  8))
    c.reputation_deltas.append((primary_id, -5.0))
    c.wants.append((primary_id, "find a new job"))
    c.wants.append((primary_id, "ask someone for financial support"))
    c.fears.append((primary_id, "fear of poverty"))
    # Household members worry too
    for hid in _all_household_ids(primary_id, engine):
        c.moodlets.append((hid, "stressed"))
        c.emotions.append((hid, "nervousness", 0.4, 5))
    # Interactions unlocked for friends
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.interactions_unlocked.append((fid, "offer financial support"))
        c.interactions_unlocked.append((fid, "job hunting advice"))
    return c


def _promotion(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "proud"))
    c.moodlets.append((primary_id, "on_a_roll"))
    c.emotions.append((primary_id, "pride",      1.0, 12))
    c.emotions.append((primary_id, "excitement", 0.7,  8))
    c.reputation_deltas.append((primary_id, 8.0))
    c.celebrity_deltas.append((primary_id, 3.0))
    c.interactions_unlocked.append((primary_id, "share career success"))
    c.interactions_unlocked.append((primary_id, "mentor junior colleague"))
    # Close friends celebrate
    for fid in _close_friends(primary_id, engine, threshold=50):
        c.interactions_unlocked.append((fid, "congratulate on promotion"))
        c.emotions.append((fid, "joy", 0.3, 4))
    return c


def _illness(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    severity = extra.get("severity", "mild")
    intensity_map = {"mild": 0.4, "moderate": 0.6, "severe": 0.9}
    intensity = intensity_map.get(severity, 0.5)
    c.moodlets.append((primary_id, "fighting_illness"))
    c.emotions.append((primary_id, "discomfort", intensity, 8))
    c.wants.append((primary_id, "recover quickly and rest"))
    # Household members and close friends can check in
    for hid in _all_household_ids(primary_id, engine):
        c.moodlets.append((hid, "uncomfortable"))
        c.interactions_unlocked.append((hid, "check on sick housemate"))
    for fid in _close_friends(primary_id, engine, threshold=50):
        c.interactions_unlocked.append((fid, "check in on sick friend"))
    return c


def _scandal(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    rep_hit = extra.get("rep_hit", -15.0)
    c.moodlets.append((primary_id, "stressed"))
    c.moodlets.append((primary_id, "embarrassed"))
    c.emotions.append((primary_id, "embarrassment", 0.8, 12))
    c.emotions.append((primary_id, "nervousness",   0.7,  8))
    c.reputation_deltas.append((primary_id, rep_hit))
    c.celebrity_deltas.append((primary_id, -5.0))
    # Witnesses feel disapproval
    for wid in secondary_ids:
        c.sentiments.append((wid, primary_id, "lied_to_me"))
        c.emotions.append((wid, "disapproval", 0.5, 6))
        c.relationship_deltas.append((wid, primary_id, -8.0, -3.0))
    c.interactions_blocked.append((primary_id, "flirt"))
    c.interactions_blocked.append((primary_id, "propose marriage"))
    return c


def _redemption(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "proud"))
    c.emotions.append((primary_id, "pride",    0.7, 10))
    c.emotions.append((primary_id, "optimism", 0.6,  8))
    c.reputation_deltas.append((primary_id, 10.0))
    c.celebrity_deltas.append((primary_id, 3.0))
    c.wants.append((primary_id, "prove myself to the community"))
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.sentiments.append((fid, primary_id, "reconciled"))
        c.relationship_deltas.append((fid, primary_id, 5.0, 0.0))
    return c


def _moving_out(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "uncomfortable"))
    c.emotions.append((primary_id, "anticipating", 0.5, 8))
    # Ex-housemates drift slightly
    for hid in _all_household_ids(primary_id, engine):
        c.relationship_deltas.append((hid, primary_id, -3.0, -1.0))
        c.emotions.append((hid, "sentimental" if hasattr(engine._sim_lookup.get(hid, object()), 'sim_id') else "sadness", 0.3, 5))
    c.wants.append((primary_id, "settle into new home"))
    c.interactions_unlocked.append((primary_id, "invite old housemates over"))
    return c


def _health_scare(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "fighting_illness"))
    c.emotions.append((primary_id, "fear",        0.7, 10))
    c.emotions.append((primary_id, "nervousness", 0.6,  8))
    c.wants.append((primary_id, "take better care of my health"))
    c.fears.append((primary_id, "fear of death"))
    for fid in _close_friends(primary_id, engine, threshold=55):
        c.interactions_unlocked.append((fid, "check on friend's health"))
        c.emotions.append((fid, "nervousness", 0.3, 4))
    return c


def _wish_fulfilled(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    wish_desc = extra.get("description", "lifetime goal")
    c.moodlets.append((primary_id, "proud"))
    c.moodlets.append((primary_id, "on_a_roll"))
    c.emotions.append((primary_id, "pride",      1.0, 25))
    c.emotions.append((primary_id, "optimism",   0.8, 20))
    c.reputation_deltas.append((primary_id, 10.0))
    c.celebrity_deltas.append((primary_id, 5.0))
    c.wants.append((primary_id, "share this achievement with loved ones"))
    c.interactions_unlocked.append((primary_id, "share life achievement"))
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.interactions_unlocked.append((fid, "celebrate friend's achievement"))
        c.emotions.append((fid, "joy", 0.4, 5))
    return c


def _milestone(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "proud"))
    c.emotions.append((primary_id, "pride", 0.7, 8))
    c.reputation_deltas.append((primary_id, 3.0))
    return c


def _birthday(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "energised"))
    c.emotions.append((primary_id, "joy",      0.7, 10))
    c.emotions.append((primary_id, "grateful", 0.5,  8))
    c.wants.append((primary_id, "celebrate with people I care about"))
    for fid in secondary_ids:
        c.interactions_unlocked.append((fid, "wish happy birthday"))
        c.interactions_unlocked.append((fid, "give birthday gift"))
        c.emotions.append((fid, "joy", 0.4, 5))
    return c


def _gig_success(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "gig_rush"))
    c.moodlets.append((primary_id, "proud"))
    c.emotions.append((primary_id, "pride",      0.7,  8))
    c.emotions.append((primary_id, "excitement", 0.6,  6))
    c.reputation_deltas.append((primary_id, 2.0))
    c.celebrity_deltas.append((primary_id, 1.0))
    c.interactions_unlocked.append((primary_id, "share work portfolio"))
    return c


def _property_bought(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "proud"))
    c.moodlets.append((primary_id, "energised"))
    c.emotions.append((primary_id, "pride",    0.7, 10))
    c.reputation_deltas.append((primary_id, 5.0))
    c.wants.append((primary_id, "furnish and personalise my new property"))
    c.interactions_unlocked.append((primary_id, "invite someone over"))
    return c


def _random_drama(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    drama_type = extra.get("drama_type", "argument")
    if drama_type == "argument":
        c.moodlets.append((primary_id, "stressed"))
        for sid in secondary_ids:
            c.relationship_deltas.append((primary_id, sid, -5.0, -2.0))
            c.sentiments.append((primary_id, sid, "held_grudge"))
    elif drama_type == "misunderstanding":
        for sid in secondary_ids:
            c.relationship_deltas.append((primary_id, sid, -3.0, -1.0))
        c.interactions_unlocked.append((primary_id, "clear the air"))
    elif drama_type == "rumour_spread":
        c.reputation_deltas.append((primary_id, -8.0))
        c.moodlets.append((primary_id, "embarrassed"))
        c.emotions.append((primary_id, "embarrassment", 0.6, 8))
    return c


def _default(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    """Fallback for unrecognised event types."""
    c = EventConsequences()
    c.emotions.append((primary_id, "surprise", 0.3, 4))
    return c


# ── Dispatch table ─────────────────────────────────────────────────────────────

_TEMPLATES: dict[str, object] = {
    EventType.BIRTH:          _birth,
    EventType.DEATH:          _death,
    EventType.MARRIAGE:       _marriage,
    EventType.DIVORCE:        _divorce,
    EventType.BREAKUP:        _breakup,
    EventType.JOB_LOSS:       _job_loss,
    EventType.PROMOTION:      _promotion,
    EventType.ILLNESS:        _illness,
    EventType.SCANDAL:        _scandal,
    EventType.REDEMPTION:     _redemption,
    EventType.MOVING_OUT:     _moving_out,
    EventType.HEALTH_SCARE:   _health_scare,
    EventType.WISH_FULFILLED: _wish_fulfilled,
    EventType.MILESTONE:      _milestone,
    EventType.BIRTHDAY:       _birthday,
    EventType.GIG_SUCCESS:    _gig_success,
    EventType.PROPERTY_BOUGHT:_property_bought,
    EventType.RANDOM_DRAMA:   _random_drama,
}
