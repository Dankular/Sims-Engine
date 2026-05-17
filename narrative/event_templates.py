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
            c.moodlets.append((hid, "mourning"))
            c.moodlets.append((hid, "grief_stricken"))
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
    c.moodlets.append((primary_id, "just_promoted"))
    c.moodlets.append((primary_id, "feeling_confident"))
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
    if severity in ("moderate", "severe"):
        c.moodlets.append((primary_id, "queasy"))
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
    c.moodlets.append((primary_id, "publicly_humiliated"))
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
    c.moodlets.append((primary_id, "new_home"))
    c.moodlets.append((primary_id, "property_owner"))
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


# ── Conflict templates ─────────────────────────────────────────────────────────

def _betrayal(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    betrayer_id = extra.get("betrayer_id", primary_id)
    victim_id   = extra.get("victim_id", secondary_ids[0] if secondary_ids else "")
    if victim_id:
        c.sentiments.append((victim_id, betrayer_id, "betrayal"))
        c.relationship_deltas.append((victim_id, betrayer_id, -25.0, -15.0))
        c.moodlets.append((victim_id, "betrayed"))
        c.moodlets.append((victim_id, "heartbroken"))
        c.emotions.append((victim_id, "grief", 0.8, 12))
        c.emotions.append((victim_id, "anger", 0.7,  8))
        c.fears.append((victim_id, "fear of betrayal"))
        c.interactions_blocked.append((victim_id, "confide"))
        c.interactions_blocked.append((victim_id, "share secret"))
    c.reputation_deltas.append((betrayer_id, -12.0))
    c.moodlets.append((betrayer_id, "guilty"))
    c.moodlets.append((betrayer_id, "stressed"))
    c.interactions_unlocked.append((betrayer_id, "sincerely apologise for recent hurtful behaviour"))
    for wid in secondary_ids:
        if wid != victim_id:
            c.emotions.append((wid, "disapproval", 0.5, 6))
    return c


def _cheating_accusation(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    accused_id  = primary_id
    accuser_id  = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((accused_id, "wrongfully_accused"))
    c.moodlets.append((accused_id, "embarrassed"))
    c.moodlets.append((accuser_id, "cheated_on"))
    c.moodlets.append((accuser_id, "furious"))
    c.emotions.append((accused_id, "nervousness", 0.7, 10))
    c.emotions.append((accuser_id, "anger",       0.8,  8))
    c.reputation_deltas.append((accused_id, -10.0))
    if accuser_id:
        c.relationship_deltas.append((accuser_id, accused_id, -20.0, -25.0))
        c.sentiments.append((accuser_id, accused_id, "jealousy_drama"))
        c.interactions_unlocked.append((accused_id, "defend yourself against accusation"))
        c.interactions_unlocked.append((accuser_id, "confront about painful or hurtful behaviour"))
    return c


def _argument(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    other_id = secondary_ids[0] if secondary_ids else ""
    severity = extra.get("severity", 0.5)
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "anger", 0.6 * severity, 8))
    if other_id:
        c.moodlets.append((other_id, "stressed"))
        c.emotions.append((other_id, "anger", 0.5 * severity, 6))
        c.relationship_deltas.append((primary_id, other_id, -8.0 * severity, -3.0 * severity))
        c.sentiments.append((primary_id, other_id, "held_grudge"))
        c.interactions_unlocked.append((primary_id, "sincerely apologise for recent hurtful behaviour"))
        c.interactions_unlocked.append((other_id,   "sincerely apologise for recent hurtful behaviour"))
    return c


def _rivalry_escalation(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    rival_id = secondary_ids[0] if secondary_ids else ""
    c.reputation_deltas.append((primary_id, -5.0))
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "anger", 0.7, 10))
    if rival_id:
        c.reputation_deltas.append((rival_id, -3.0))
        c.relationship_deltas.append((primary_id, rival_id, -15.0, 0.0))
        c.sentiments.append((primary_id, rival_id, "rivalry_formed"))
        c.sentiments.append((rival_id, primary_id, "rivalry_formed"))
        c.interactions_unlocked.append((primary_id, "rivalry_escalation"))
        c.interactions_unlocked.append((rival_id,   "rivalry_escalation"))
    for sid in secondary_ids[1:]:
        c.emotions.append((sid, "surprise", 0.4, 5))
    return c


def _public_embarrassment(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "mortified"))
    c.moodlets.append((primary_id, "publicly_humiliated"))
    c.emotions.append((primary_id, "embarrassment", 0.8, 12))
    c.emotions.append((primary_id, "nervousness",   0.5,  8))
    c.reputation_deltas.append((primary_id, -8.0))
    c.celebrity_deltas.append((primary_id, -3.0))
    for wid in secondary_ids:
        c.emotions.append((wid, "surprise",   0.4, 4))
        c.emotions.append((wid, "amusement",  0.3, 3))
    c.interactions_unlocked.append((primary_id, "laugh off the embarrassment"))
    return c


def _apology(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    target_id = secondary_ids[0] if secondary_ids else ""
    c.emotions.append((primary_id, "relief",  0.5, 6))
    c.emotions.append((primary_id, "hopeful", 0.4, 5))
    if target_id:
        c.relationship_deltas.append((primary_id, target_id, 5.0, 0.0))
        c.sentiments.append((primary_id, target_id, "reconciled"))
        c.emotions.append((target_id, "surprise", 0.4, 4))
        c.interactions_unlocked.append((target_id, "attempt to genuinely repair the relationship"))
    return c


def _reconciliation_evt(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    other_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "grateful"))
    c.emotions.append((primary_id, "relief",    0.7, 10))
    c.emotions.append((primary_id, "gratitude", 0.6,  8))
    if other_id:
        c.relationship_deltas.append((primary_id, other_id, 12.0, 3.0))
        c.sentiments.append((primary_id, other_id, "reconciled"))
        c.sentiments.append((other_id, primary_id, "reconciled"))
        c.moodlets.append((other_id, "grateful"))
        c.emotions.append((other_id, "relief", 0.6, 8))
        c.interactions_unlocked.append((primary_id, "express deep affection and appreciation"))
    return c


# ── Romance arc templates ──────────────────────────────────────────────────────

def _crush_formed(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    crush_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "flirty"))
    c.emotions.append((primary_id, "desire",       0.6, 8))
    c.emotions.append((primary_id, "anticipating", 0.5, 6))
    c.wants.append((primary_id, "spend more time with my crush"))
    c.interactions_unlocked.append((primary_id, "flirt"))
    c.interactions_unlocked.append((primary_id, "compliment appearance"))
    return c


def _love_confession(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    target_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "flirty"))
    c.emotions.append((primary_id, "nervousness", 0.6, 5))
    c.emotions.append((primary_id, "anticipating", 0.7, 6))
    if target_id:
        c.emotions.append((target_id, "surprise", 0.6, 5))
        c.relationship_deltas.append((primary_id, target_id, 5.0, 10.0))
        c.interactions_unlocked.append((target_id, "express love in return"))
        c.interactions_unlocked.append((target_id, "let them down gently"))
    return c


def _first_kiss_event(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    partner_id = secondary_ids[0] if secondary_ids else ""
    for sid in [primary_id] + ([partner_id] if partner_id else []):
        c.moodlets.append((sid, "flirty"))
        c.emotions.append((sid, "love",        0.9, 15))
        c.emotions.append((sid, "excitement",  0.7, 10))
        c.celebrity_deltas.append((sid, 0.5))
    if partner_id:
        c.sentiments.append((primary_id, partner_id, "first_kiss"))
        c.sentiments.append((partner_id, primary_id, "first_kiss"))
        c.relationship_deltas.append((primary_id, partner_id, 8.0, 15.0))
        c.interactions_unlocked.append((primary_id, "kiss"))
        c.interactions_unlocked.append((partner_id, "kiss"))
    return c


def _dating_started(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    partner_id = secondary_ids[0] if secondary_ids else ""
    for sid in [primary_id] + ([partner_id] if partner_id else []):
        c.moodlets.append((sid, "flirty"))
        c.emotions.append((sid, "love",      0.8, 12))
        c.emotions.append((sid, "optimism",  0.6,  8))
        c.wants.append((sid, "go on a romantic date"))
    if partner_id:
        c.relationship_deltas.append((primary_id, partner_id, 5.0, 10.0))
        c.interactions_unlocked.append((primary_id, "plan romantic evening"))
        c.interactions_unlocked.append((partner_id, "plan romantic evening"))
    return c


def _engagement(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    partner_id = secondary_ids[0] if secondary_ids else ""
    for sid in [primary_id] + ([partner_id] if partner_id else []):
        c.moodlets.append((sid, "newly_wed"))
        c.emotions.append((sid, "love",      1.0, 20))
        c.emotions.append((sid, "excitement", 0.9, 15))
        c.reputation_deltas.append((sid, 3.0))
        c.celebrity_deltas.append((sid, 2.0))
        c.interactions_unlocked.append((sid, "plan the wedding"))
    if partner_id:
        c.relationship_deltas.append((primary_id, partner_id, 10.0, 15.0))
        c.sentiments.append((primary_id, partner_id, "first_love"))
        c.sentiments.append((partner_id, primary_id, "first_love"))
    # Inform friends
    for fid in _close_friends(primary_id, engine, threshold=50):
        c.interactions_unlocked.append((fid, "congratulate on engagement"))
        c.emotions.append((fid, "joy", 0.4, 5))
    return c


# ── Friendship arc templates ───────────────────────────────────────────────────

def _best_friends_formed(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    friend_id = secondary_ids[0] if secondary_ids else ""
    for sid in [primary_id] + ([friend_id] if friend_id else []):
        c.moodlets.append((sid, "deeply_connected"))
        c.emotions.append((sid, "joy",      0.8, 12))
        c.emotions.append((sid, "gratitude", 0.6, 8))
        c.wants.append((sid, "spend quality time with my best friend"))
        c.interactions_unlocked.append((sid, "share deepest secret"))
        c.interactions_unlocked.append((sid, "confide deepest worry"))
    if friend_id:
        c.sentiments.append((primary_id, friend_id, "childhood_bond"))
        c.sentiments.append((friend_id, primary_id, "childhood_bond"))
    return c


def _drifting_apart(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    other_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "uncomfortable"))
    c.emotions.append((primary_id, "sadness",     0.5, 8))
    c.emotions.append((primary_id, "sentimental", 0.4, 6))
    c.wants.append((primary_id, "reach out to a drifting friend"))
    if other_id:
        c.relationship_deltas.append((primary_id, other_id, -5.0, -2.0))
        c.interactions_unlocked.append((primary_id, "reach out to share feelings and seek comfort"))
    return c


def _crisis_support(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    supported_id = extra.get("supported_id", secondary_ids[0] if secondary_ids else "")
    c.moodlets.append((primary_id, "deeply_connected"))
    c.emotions.append((primary_id, "gratitude", 0.8, 12))
    c.emotions.append((primary_id, "relief",    0.6,  8))
    if supported_id:
        c.sentiments.append((primary_id, supported_id, "saved_me"))
        c.sentiments.append((supported_id, primary_id, "gratitude"))
        c.relationship_deltas.append((primary_id, supported_id, 15.0, 3.0))
        c.moodlets.append((supported_id, "grateful"))
        c.interactions_unlocked.append((primary_id, "express deep affection and appreciation"))
    return c


def _jealousy_incident(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    target_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "anger", 0.6, 8))
    c.reputation_deltas.append((primary_id, -4.0))
    if target_id:
        c.sentiments.append((primary_id, target_id, "jealousy_drama"))
        c.relationship_deltas.append((primary_id, target_id, -8.0, 0.0))
        c.emotions.append((target_id, "surprise",    0.4, 5))
        c.emotions.append((target_id, "nervousness", 0.3, 4))
        c.interactions_unlocked.append((target_id, "offer reassurance"))
    return c


def _social_exclusion(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    excluded_id = secondary_ids[0] if secondary_ids else ""
    c.reputation_deltas.append((primary_id, -5.0))
    c.emotions.append((primary_id, "pride", 0.3, 3))
    if excluded_id:
        c.moodlets.append((excluded_id, "lonely"))
        c.moodlets.append((excluded_id, "social_isolation"))
        c.emotions.append((excluded_id, "sadness",     0.7, 10))
        c.emotions.append((excluded_id, "nervousness", 0.5,  7))
        c.relationship_deltas.append((excluded_id, primary_id, -10.0, -5.0))
        c.fears.append((excluded_id, "fear of rejection"))
        c.wants.append((excluded_id, "find a group where I belong"))
    return c


# ── Family arc templates ───────────────────────────────────────────────────────

def _pregnancy_discovery(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "uncomfortable"))
    c.emotions.append((primary_id, "surprise",   0.8, 6))
    c.emotions.append((primary_id, "anticipating",0.6, 8))
    c.wants.append((primary_id, "prepare for the arrival of a new baby"))
    for sid in secondary_ids:
        c.emotions.append((sid, "surprise", 0.7, 5))
        c.emotions.append((sid, "joy",      0.5, 6))
        c.interactions_unlocked.append((sid, "prenatal care support"))
    for hid in _all_household_ids(primary_id, engine):
        c.emotions.append((hid, "anticipating", 0.4, 5))
    return c


def _sibling_rivalry(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    sibling_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "anger", 0.5, 8))
    if sibling_id:
        c.relationship_deltas.append((primary_id, sibling_id, -8.0, 0.0))
        c.sentiments.append((primary_id, sibling_id, "rivalry_formed"))
        c.moodlets.append((sibling_id, "stressed"))
        c.interactions_unlocked.append((primary_id, "sibling rivalry escalation"))
        c.interactions_unlocked.append((primary_id, "work through sibling tension"))
    # Parents get stress
    for parent_id in engine._sim_lookup.get(primary_id, object().__class__).__mro__[:0]:
        pass  # handled via household lookup
    for hid in _all_household_ids(primary_id, engine):
        if hid != sibling_id:
            c.emotions.append((hid, "nervousness", 0.3, 4))
    return c


def _parental_neglect(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    child_id = secondary_ids[0] if secondary_ids else ""
    c.reputation_deltas.append((primary_id, -8.0))
    c.emotions.append((primary_id, "guilt", 0.6, 10))
    c.moodlets.append((primary_id, "stressed"))
    if child_id:
        c.relationship_deltas.append((primary_id, child_id, -10.0, -5.0))
        c.emotions.append((child_id, "sadness",     0.7, 12))
        c.emotions.append((child_id, "nervousness", 0.5,  8))
        c.fears.append((child_id, "fear of abandonment"))
    c.wants.append((primary_id, "spend quality time with family"))
    return c


def _parental_support(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    child_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "proud"))
    c.emotions.append((primary_id, "joy",   0.7, 10))
    c.emotions.append((primary_id, "pride", 0.6,  8))
    c.reputation_deltas.append((primary_id, 4.0))
    if child_id:
        c.relationship_deltas.append((primary_id, child_id, 12.0, 0.0))
        c.emotions.append((child_id, "joy",      0.7, 10))
        c.emotions.append((child_id, "gratitude", 0.6,  8))
        c.sentiments.append((child_id, primary_id, "saved_me"))
        c.wants.append((child_id, "make my parent proud"))
    return c


def _inheritance_received(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    amount = extra.get("amount", 500.0)
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.simoleons += amount
    c.emotions.append((primary_id, "grief",    0.6, 10))
    c.emotions.append((primary_id, "grateful", 0.5,  8))
    c.moodlets.append((primary_id, "grateful"))
    c.wants.append((primary_id, "honour the memory of the departed"))
    return c


def _family_feud(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    other_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "anger", 0.8, 12))
    c.reputation_deltas.append((primary_id, -6.0))
    if other_id:
        c.relationship_deltas.append((primary_id, other_id, -20.0, -10.0))
        c.sentiments.append((primary_id, other_id, "held_grudge"))
        c.sentiments.append((other_id, primary_id, "held_grudge"))
        c.moodlets.append((other_id, "stressed"))
        c.emotions.append((other_id, "anger", 0.7, 10))
    for hid in _all_household_ids(primary_id, engine):
        c.moodlets.append((hid, "drama_witnessed"))
        c.emotions.append((hid, "nervousness", 0.5, 6))
    return c


# ── Gossip / rumour templates ──────────────────────────────────────────────────

# ── Aging arc templates ────────────────────────────────────────────────────────

def _life_stage_transition(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    new_stage = extra.get("new_stage", "adult")
    if new_stage == "teen":
        c.moodlets.append((primary_id, "energised"))
        c.emotions.append((primary_id, "anticipating", 0.7, 10))
        c.wants.append((primary_id, "discover who I am and my place in the world"))
        c.interactions_unlocked.append((primary_id, "teen rebellion"))
    elif new_stage == "adult":
        c.moodlets.append((primary_id, "proud"))
        c.emotions.append((primary_id, "optimism", 0.7, 12))
        c.reputation_deltas.append((primary_id, 5.0))
        c.wants.append((primary_id, "establish myself in my career"))
    elif new_stage == "elder":
        c.moodlets.append((primary_id, "grateful"))
        c.emotions.append((primary_id, "sentimental", 0.6, 15))
        c.wants.append((primary_id, "leave a meaningful legacy"))
        c.interactions_unlocked.append((primary_id, "share life wisdom"))
        c.interactions_unlocked.append((primary_id, "tell stories from the past"))
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.interactions_unlocked.append((fid, "celebrate life milestone"))
        c.emotions.append((fid, "joy", 0.3, 5))
    return c


def _midlife_crisis(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "in_a_slump"))
    c.moodlets.append((primary_id, "stir_crazy"))
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "nervousness", 0.7, 15))
    c.emotions.append((primary_id, "disappointment", 0.5, 10))
    c.reputation_deltas.append((primary_id, -3.0))
    c.wants.append((primary_id, "recapture lost youth and adventure"))
    c.wants.append((primary_id, "make a dramatic life change"))
    # Erratic behaviour — may initiate flirtatious or reckless interactions
    c.interactions_unlocked.append((primary_id, "flirt recklessly"))
    c.interactions_unlocked.append((primary_id, "announce dramatic life change"))
    for hid in _all_household_ids(primary_id, engine):
        c.emotions.append((hid, "nervousness", 0.4, 6))
    return c


def _elder_decline(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "fighting_illness"))
    c.emotions.append((primary_id, "sentimental", 0.6, 10))
    c.emotions.append((primary_id, "discomfort",  0.4,  8))
    c.wants.append((primary_id, "spend time with loved ones"))
    c.wants.append((primary_id, "put affairs in order"))
    c.interactions_unlocked.append((primary_id, "share life wisdom"))
    c.interactions_unlocked.append((primary_id, "reminisce about the past"))
    for fid in _close_friends(primary_id, engine, threshold=50):
        c.emotions.append((fid, "nervousness", 0.4, 6))
        c.interactions_unlocked.append((fid, "offer elder care support"))
    return c


def _death_preparation(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "grateful"))
    c.emotions.append((primary_id, "sentimental",   0.8, 20))
    c.emotions.append((primary_id, "fear",          0.5, 10))
    c.wants.append((primary_id, "make peace with everyone I care about"))
    c.wants.append((primary_id, "pass on something meaningful"))
    c.reputation_deltas.append((primary_id, 5.0))  # community respect for elders
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.interactions_unlocked.append((fid, "offer final comfort"))
        c.interactions_unlocked.append((fid, "say goodbye"))
        c.emotions.append((fid, "sentimental", 0.5, 8))
    return c


# ── Career depth templates ─────────────────────────────────────────────────────

def _demotion(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "stressed"))
    c.moodlets.append((primary_id, "embarrassed"))
    c.emotions.append((primary_id, "disappointment", 0.8, 12))
    c.emotions.append((primary_id, "embarrassment",  0.6,  8))
    c.reputation_deltas.append((primary_id, -6.0))
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.career_performance = max(0, sim.career_performance - 15)
    c.wants.append((primary_id, "prove myself and regain my former standing"))
    c.fears.append((primary_id, "fear of failure"))
    for hid in _all_household_ids(primary_id, engine):
        c.emotions.append((hid, "nervousness", 0.3, 4))
    return c


def _fired(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "stressed"))
    c.moodlets.append((primary_id, "broke"))
    c.emotions.append((primary_id, "anger",         0.7, 12))
    c.emotions.append((primary_id, "disappointment", 0.8, 15))
    c.emotions.append((primary_id, "nervousness",    0.7, 10))
    c.reputation_deltas.append((primary_id, -10.0))
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.career_performance = 20.0
    c.wants.append((primary_id, "find a new job urgently"))
    c.wants.append((primary_id, "figure out what went wrong"))
    c.fears.append((primary_id, "fear of poverty"))
    c.interactions_unlocked.append((primary_id, "vent about being fired"))
    for fid in _close_friends(primary_id, engine, threshold=45):
        c.interactions_unlocked.append((fid, "offer job hunting advice"))
        c.interactions_unlocked.append((fid, "offer financial support"))
    return c


def _workplace_romance(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    colleague_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "flirty"))
    c.emotions.append((primary_id, "excitement", 0.6, 10))
    if colleague_id:
        c.relationship_deltas.append((primary_id, colleague_id, 5.0, 12.0))
        c.moodlets.append((colleague_id, "flirty"))
        c.emotions.append((colleague_id, "anticipating", 0.5, 8))
        c.interactions_unlocked.append((primary_id, "flirt with colleague"))
        c.interactions_unlocked.append((colleague_id, "flirt with colleague"))
    c.reputation_deltas.append((primary_id, -2.0))  # office gossip risk
    return c


def _workplace_rivalry(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    rival_id = secondary_ids[0] if secondary_ids else ""
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "anger", 0.6, 10))
    if rival_id:
        c.relationship_deltas.append((primary_id, rival_id, -12.0, 0.0))
        c.sentiments.append((primary_id, rival_id, "rivalry_formed"))
        c.moodlets.append((rival_id, "stressed"))
        c.interactions_unlocked.append((primary_id, "rivalry_escalation"))
        c.interactions_unlocked.append((rival_id,   "rivalry_escalation"))
    c.wants.append((primary_id, "outperform my workplace rival"))
    return c


def _career_change(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "energised"))
    c.emotions.append((primary_id, "anticipating", 0.7, 12))
    c.emotions.append((primary_id, "nervousness",  0.4,  6))
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.career_performance = 40.0  # fresh start at middle level
    c.wants.append((primary_id, "prove myself in my new career path"))
    c.interactions_unlocked.append((primary_id, "share exciting career news"))
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.interactions_unlocked.append((fid, "encourage career change"))
    return c


# ── Education templates ────────────────────────────────────────────────────────

def _school_performance(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    grade = extra.get("grade", "good")
    if grade == "excellent":
        c.moodlets.append((primary_id, "proud"))
        c.emotions.append((primary_id, "pride", 0.7, 8))
        c.reputation_deltas.append((primary_id, 3.0))
        c.celebrity_deltas.append((primary_id, 1.0))
    elif grade == "poor":
        c.moodlets.append((primary_id, "stressed"))
        c.emotions.append((primary_id, "disappointment", 0.6, 8))
        c.emotions.append((primary_id, "nervousness",    0.5, 6))
        c.wants.append((primary_id, "improve my grades"))
    for parent_id in [p for p in engine.sims if primary_id in p.profile.get("parent_ids", [])
                       or primary_id in getattr(p, "coworker_ids", [])]:
        pass  # parents notified via household visibility
    return c


def _homework_failure(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "nervousness",    0.5, 6))
    c.emotions.append((primary_id, "disappointment", 0.4, 5))
    c.wants.append((primary_id, "ask for help with studying"))
    c.interactions_unlocked.append((primary_id, "ask for tutoring help"))
    return c


def _exam_success(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "on_a_roll"))
    c.moodlets.append((primary_id, "proud"))
    c.emotions.append((primary_id, "pride",     0.8, 10))
    c.emotions.append((primary_id, "excitement", 0.6, 8))
    c.reputation_deltas.append((primary_id, 4.0))
    c.celebrity_deltas.append((primary_id, 1.0))
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.skills.gain_xp("logic", 1.5)
    c.interactions_unlocked.append((primary_id, "celebrate exam success"))
    return c


def _scholarship(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    amount = extra.get("amount", 1000.0)
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.simoleons += amount
    c.moodlets.append((primary_id, "proud"))
    c.moodlets.append((primary_id, "on_a_roll"))
    c.emotions.append((primary_id, "pride",      1.0, 15))
    c.emotions.append((primary_id, "excitement", 0.8, 10))
    c.reputation_deltas.append((primary_id, 8.0))
    c.celebrity_deltas.append((primary_id, 3.0))
    c.interactions_unlocked.append((primary_id, "share scholarship news"))
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.interactions_unlocked.append((fid, "congratulate on scholarship"))
        c.emotions.append((fid, "joy", 0.4, 5))
    return c


def _graduation(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "proud"))
    c.moodlets.append((primary_id, "on_a_roll"))
    c.emotions.append((primary_id, "pride",      1.0, 20))
    c.emotions.append((primary_id, "excitement", 0.8, 15))
    c.reputation_deltas.append((primary_id, 10.0))
    c.celebrity_deltas.append((primary_id, 4.0))
    c.wants.append((primary_id, "start my career with a strong foundation"))
    c.interactions_unlocked.append((primary_id, "share graduation news"))
    for fid in _close_friends(primary_id, engine, threshold=35):
        c.interactions_unlocked.append((fid, "celebrate graduation"))
        c.emotions.append((fid, "joy", 0.5, 6))
    return c


# ── Health depth templates ─────────────────────────────────────────────────────

def _chronic_stress(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "fighting_burnout"))
    c.moodlets.append((primary_id, "overwhelmed"))
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "nervousness", 0.7, 15))
    c.emotions.append((primary_id, "discomfort",  0.5, 10))
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.needs.energy = max(0, sim.needs.energy - 10)
        sim.needs.fun    = max(0, sim.needs.fun    - 8)
    c.wants.append((primary_id, "find a way to reduce stress"))
    c.wants.append((primary_id, "take a break and recover"))
    c.fears.append((primary_id, "fear of burnout"))
    c.interactions_unlocked.append((primary_id, "reach out to share feelings and seek comfort"))
    for fid in _close_friends(primary_id, engine, threshold=50):
        c.interactions_unlocked.append((fid, "check in on stressed friend"))
    return c


def _injury(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    severity = extra.get("severity", "minor")
    c.moodlets.append((primary_id, "fighting_illness"))
    c.emotions.append((primary_id, "discomfort", 0.7, 10))
    c.emotions.append((primary_id, "fear",       0.4,  6))
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        penalty = {"minor": 15, "moderate": 30, "severe": 50}.get(severity, 15)
        sim.needs.energy  = max(0, sim.needs.energy  - penalty)
        sim.needs.comfort = max(0, sim.needs.comfort - penalty * 0.7)
    c.wants.append((primary_id, "rest and recover from the injury"))
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.interactions_unlocked.append((fid, "check in on injured friend"))
        c.emotions.append((fid, "nervousness", 0.3, 4))
    return c


def _hospitalization(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    c.moodlets.append((primary_id, "fighting_illness"))
    c.moodlets.append((primary_id, "stressed"))
    c.emotions.append((primary_id, "fear",        0.8, 15))
    c.emotions.append((primary_id, "discomfort",  0.7, 12))
    sim = engine._sim_lookup.get(primary_id)
    if sim:
        sim.health_status = "sick"
        sim.illness_severity = "severe"
        sim.illness_ticks_left = 10
        sim.needs.energy  = max(0, sim.needs.energy  - 40)
        sim.needs.comfort = max(0, sim.needs.comfort - 30)
    c.reputation_deltas.append((primary_id, -2.0))  # vulnerability becomes public
    c.wants.append((primary_id, "recover and return to normal life"))
    c.fears.append((primary_id, "fear of death"))
    for fid in _close_friends(primary_id, engine, threshold=40):
        c.moodlets.append((fid, "uncomfortable"))
        c.emotions.append((fid, "nervousness", 0.6, 10))
        c.emotions.append((fid, "fear",        0.4,  8))
        c.interactions_unlocked.append((fid, "hospital visit"))
        c.interactions_unlocked.append((fid, "check on hospitalized friend"))
    for hid in _all_household_ids(primary_id, engine):
        c.emotions.append((hid, "nervousness", 0.7, 12))
        c.emotions.append((hid, "fear",        0.5,  8))
    return c


def _rumour_created(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    target_id = secondary_ids[0] if secondary_ids else ""
    # Creator gains slight notoriety
    c.celebrity_deltas.append((primary_id, 0.5))
    # Target gets reputation hit (unconfirmed — lighter than proved scandal)
    if target_id:
        c.reputation_deltas.append((target_id, -4.0))
        c.moodlets.append((target_id, "uncomfortable"))
    return c


def _rumour_believed(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    target_id = extra.get("subject_id", "")
    believers  = secondary_ids
    rep_hit    = extra.get("rep_hit", -8.0)
    if target_id:
        c.reputation_deltas.append((target_id, rep_hit))
        c.moodlets.append((target_id, "stressed"))
        c.emotions.append((target_id, "nervousness", 0.6, 10))
        for bid in believers:
            c.relationship_deltas.append((bid, target_id, -6.0, -2.0))
            c.sentiments.append((bid, target_id, "lied_to_me"))
        c.interactions_unlocked.append((target_id, "defend your reputation"))
        c.interactions_unlocked.append((target_id, "clear the air"))
    return c


def _rumour_disproven(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    spreader_id = extra.get("spreader_id", secondary_ids[0] if secondary_ids else "")
    c.reputation_deltas.append((primary_id, 8.0))   # target vindicated
    c.moodlets.append((primary_id, "proud"))
    c.emotions.append((primary_id, "relief", 0.8, 10))
    if spreader_id:
        c.reputation_deltas.append((spreader_id, -10.0))
        c.moodlets.append((spreader_id, "embarrassed"))
        c.sentiments.append((primary_id, spreader_id, "held_grudge"))
        c.relationship_deltas.append((primary_id, spreader_id, -10.0, 0.0))
    return c


def _reputation_damage(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    c = EventConsequences()
    rep_hit = extra.get("rep_hit", -12.0)
    c.reputation_deltas.append((primary_id, rep_hit))
    c.celebrity_deltas.append((primary_id, -4.0))
    c.moodlets.append((primary_id, "stressed"))
    c.moodlets.append((primary_id, "embarrassed"))
    c.emotions.append((primary_id, "embarrassment", 0.8, 12))
    c.interactions_blocked.append((primary_id, "flirt"))
    c.interactions_blocked.append((primary_id, "propose marriage"))
    c.wants.append((primary_id, "rebuild my reputation"))
    c.interactions_unlocked.append((primary_id, "defend your reputation"))
    return c


def _default(primary_id: str, secondary_ids: list[str], engine: "SimEngine", extra: dict) -> EventConsequences:
    """Fallback for unrecognised event types."""
    c = EventConsequences()
    c.emotions.append((primary_id, "surprise", 0.3, 4))
    return c


# ── Dispatch table ─────────────────────────────────────────────────────────────



# ── World / seasonal templates ─────────────────────────────────────────────────

def _heatwave_event(p, s, e, x):
    c = EventConsequences()
    for sim in e.sims:
        c.emotions.append((sim.sim_id, "discomfort",  0.6, 6))
        c.emotions.append((sim.sim_id, "annoyance",   0.4, 4))
        c.moodlets.append((sim.sim_id, "too_cold"))  # reuse discomfort moodlet
    c.reputation_deltas.append((p, 0.0))
    return c


def _snow_day(p, s, e, x):
    c = EventConsequences()
    for sim in e.sims:
        c.emotions.append((sim.sim_id, "excitement", 0.5, 6))
        c.emotions.append((sim.sim_id, "joy",        0.3, 5))
        c.interactions_unlocked.append((sim.sim_id, "have a snowball fight"))
        c.interactions_unlocked.append((sim.sim_id, "build something together in the snow"))
    return c


def _storm_event(p, s, e, x):
    c = EventConsequences()
    for sim in e.sims:
        c.emotions.append((sim.sim_id, "apprehensive", 0.5, 6))
        c.emotions.append((sim.sim_id, "nervousness",  0.4, 5))
        c.moodlets.append((sim.sim_id, "uncomfortable"))
    return c


def _holiday_mood_shift(p, s, e, x):
    from narrative.event_templates import _all_household_ids
    c = EventConsequences()
    holiday_name = x.get("holiday_name", "Holiday")
    for sim in e.sims:
        c.emotions.append((sim.sim_id, "joy",      0.6, 8))
        c.emotions.append((sim.sim_id, "grateful", 0.4, 6))
        c.interactions_unlocked.append((sim.sim_id, f"celebrate {holiday_name}"))
        c.interactions_unlocked.append((sim.sim_id, "share holiday meal"))
    return c


def _seasonal_depression(p, s, e, x):
    c = EventConsequences()
    c.moodlets.append((p, "rainy_day_blues"))
    c.emotions.append((p, "sadness",     0.6, 12))
    c.emotions.append((p, "loneliness",  0.4,  8))
    c.wants.append((p, "find something meaningful to look forward to"))
    for fid in _close_friends(p, e, threshold=50):
        c.interactions_unlocked.append((fid, "reach out to share feelings and seek comfort"))
    return c


def _seasonal_boost(p, s, e, x):
    c = EventConsequences()
    c.moodlets.append((p, "energised"))
    c.emotions.append((p, "optimism", 0.6, 10))
    c.emotions.append((p, "joy",      0.4,  8))
    c.wants.append((p, "make the most of the season"))
    return c


# ── Community templates ────────────────────────────────────────────────────────

def _festival(p, s, e, x):
    c = EventConsequences()
    for sim in e.sims:
        sim.needs.restore("social", 12.0)
        sim.needs.restore("fun", 10.0)
        c.emotions.append((sim.sim_id, "joy",       0.7, 10))
        c.emotions.append((sim.sim_id, "excitement", 0.5,  8))
        c.interactions_unlocked.append((sim.sim_id, "celebrate at the festival"))
        c.interactions_unlocked.append((sim.sim_id, "meet someone new at event"))
    return c


def _neighborhood_dispute(p, s, e, x):
    c = EventConsequences()
    other_id = s[0] if s else ""
    c.moodlets.append((p, "stressed"))
    c.emotions.append((p, "anger", 0.6, 10))
    c.reputation_deltas.append((p, -4.0))
    if other_id:
        c.relationship_deltas.append((p, other_id, -10.0, -3.0))
        c.sentiments.append((p, other_id, "held_grudge"))
        c.moodlets.append((other_id, "stressed"))
        c.interactions_unlocked.append((p, "attempt to genuinely repair the relationship"))
    return c


def _club_meeting_event(p, s, e, x):
    c = EventConsequences()
    club_name = x.get("club_name", "the club")
    for member_id in s:
        c.emotions.append((member_id, "joy",      0.4, 6))
        c.interactions_unlocked.append((member_id, f"club activity at {club_name}"))
    return c


def _public_scandal(p, s, e, x):
    c = EventConsequences()
    rep_hit = x.get("rep_hit", -15.0)
    c.moodlets.append((p, "stressed"))
    c.moodlets.append((p, "embarrassed"))
    c.emotions.append((p, "embarrassment", 0.9, 15))
    c.reputation_deltas.append((p, rep_hit))
    c.celebrity_deltas.append((p, -6.0))
    for wid in s:
        c.sentiments.append((wid, p, "lied_to_me"))
        c.relationship_deltas.append((wid, p, -8.0, -3.0))
    c.interactions_blocked.append((p, "flirt"))
    c.interactions_blocked.append((p, "propose marriage"))
    c.interactions_unlocked.append((p, "defend your reputation"))
    return c


def _local_celebration(p, s, e, x):
    c = EventConsequences()
    for sim in e.sims:
        sim.needs.restore("social", 8.0)
        sim.needs.restore("fun",    8.0)
        c.emotions.append((sim.sim_id, "joy",      0.6, 8))
        c.emotions.append((sim.sim_id, "grateful", 0.4, 6))
        c.interactions_unlocked.append((sim.sim_id, "join the celebration"))
    return c


# ── Household templates ────────────────────────────────────────────────────────

def _bills_crisis(p, s, e, x):
    c = EventConsequences()
    c.moodlets.append((p, "broke"))
    c.moodlets.append((p, "stressed"))
    c.emotions.append((p, "nervousness",    0.8, 12))
    c.emotions.append((p, "disappointment", 0.6,  8))
    c.reputation_deltas.append((p, -3.0))
    c.wants.append((p, "find extra income urgently"))
    c.fears.append((p, "fear of poverty"))
    for hid in _all_household_ids(p, e):
        c.moodlets.append((hid, "stressed"))
        c.emotions.append((hid, "nervousness", 0.5, 8))
    c.interactions_unlocked.append((p, "ask someone for financial support"))
    return c


def _moving_house(p, s, e, x):
    c = EventConsequences()
    c.moodlets.append((p, "energised"))
    c.emotions.append((p, "anticipating", 0.7, 10))
    c.emotions.append((p, "nervousness",  0.4,  6))
    c.wants.append((p, "settle into the new home"))
    c.reputation_deltas.append((p, 3.0))
    c.interactions_unlocked.append((p, "host a housewarming party"))
    for fid in _close_friends(p, e, threshold=40):
        c.interactions_unlocked.append((fid, "help with the move"))
    return c


def _eviction_risk(p, s, e, x):
    c = EventConsequences()
    c.moodlets.append((p, "stressed"))
    c.moodlets.append((p, "broke"))
    c.emotions.append((p, "fear",        0.8, 15))
    c.emotions.append((p, "nervousness", 0.7, 12))
    c.reputation_deltas.append((p, -8.0))
    c.wants.append((p, "find emergency funds to avoid eviction"))
    c.fears.append((p, "fear of homelessness"))
    for hid in _all_household_ids(p, e):
        c.emotions.append((hid, "fear", 0.6, 10))
    for fid in _close_friends(p, e, threshold=40):
        c.interactions_unlocked.append((fid, "offer financial support"))
        c.interactions_unlocked.append((fid, "offer a place to stay"))
    return c


def _roommate_conflict(p, s, e, x):
    c = EventConsequences()
    other_id = s[0] if s else ""
    c.moodlets.append((p, "stressed"))
    c.emotions.append((p, "anger", 0.6, 10))
    if other_id:
        c.relationship_deltas.append((p, other_id, -12.0, -3.0))
        c.sentiments.append((p, other_id, "held_grudge"))
        c.moodlets.append((other_id, "stressed"))
        c.emotions.append((other_id, "anger", 0.5, 8))
        c.interactions_unlocked.append((p, "attempt to genuinely repair the relationship"))
        c.interactions_unlocked.append((p, "calmly confront about painful or hurtful behaviour"))
    return c


def _household_merge(p, s, e, x):
    c = EventConsequences()
    for sim_id in [p] + s:
        c.moodlets.append((sim_id, "uncomfortable"))
        c.emotions.append((sim_id, "anticipating", 0.5, 8))
    # Adjustment period — all pairs get slight relationship push
    for i, a_id in enumerate([p] + s):
        for b_id in ([p] + s)[i+1:]:
            c.relationship_deltas.append((a_id, b_id, 3.0, 0.0))
    return c


def _household_split(p, s, e, x):
    c = EventConsequences()
    c.moodlets.append((p, "uncomfortable"))
    c.emotions.append((p, "sadness",     0.5, 8))
    c.emotions.append((p, "anticipating", 0.4, 6))
    c.wants.append((p, "stay in touch with former housemates"))
    for hid in s:
        c.relationship_deltas.append((p, hid, -3.0, -1.0))
    return c


_TEMPLATES: dict[str, object] = {
    # Core life milestones
    EventType.BIRTH:                _birth,
    EventType.DEATH:                _death,
    EventType.MARRIAGE:             _marriage,
    EventType.DIVORCE:              _divorce,
    EventType.BREAKUP:              _breakup,
    EventType.JOB_LOSS:             _job_loss,
    EventType.PROMOTION:            _promotion,
    EventType.ILLNESS:              _illness,
    EventType.SCANDAL:              _scandal,
    EventType.REDEMPTION:           _redemption,
    EventType.MOVING_OUT:           _moving_out,
    EventType.HEALTH_SCARE:         _health_scare,
    EventType.WISH_FULFILLED:       _wish_fulfilled,
    EventType.MILESTONE:            _milestone,
    EventType.BIRTHDAY:             _birthday,
    EventType.GIG_SUCCESS:          _gig_success,
    EventType.PROPERTY_BOUGHT:      _property_bought,
    EventType.RANDOM_DRAMA:         _random_drama,
    # Conflict
    EventType.BETRAYAL:             _betrayal,
    EventType.CHEATING_ACCUSATION:  _cheating_accusation,
    EventType.ARGUMENT:             _argument,
    EventType.RIVALRY_ESCALATION:   _rivalry_escalation,
    EventType.PUBLIC_EMBARRASSMENT: _public_embarrassment,
    EventType.APOLOGY:              _apology,
    EventType.RECONCILIATION:       _reconciliation_evt,
    # Romance arc
    EventType.CRUSH_FORMED:         _crush_formed,
    EventType.LOVE_CONFESSION:      _love_confession,
    EventType.FIRST_KISS_EVENT:     _first_kiss_event,
    EventType.DATING_STARTED:       _dating_started,
    EventType.ENGAGEMENT:           _engagement,
    # Friendship arc
    EventType.BEST_FRIENDS_FORMED:  _best_friends_formed,
    EventType.DRIFTING_APART:       _drifting_apart,
    EventType.CRISIS_SUPPORT:       _crisis_support,
    EventType.JEALOUSY_INCIDENT:    _jealousy_incident,
    EventType.SOCIAL_EXCLUSION:     _social_exclusion,
    # Family arc
    EventType.PREGNANCY_DISCOVERY:  _pregnancy_discovery,
    EventType.SIBLING_RIVALRY:      _sibling_rivalry,
    EventType.PARENTAL_NEGLECT:     _parental_neglect,
    EventType.PARENTAL_SUPPORT:     _parental_support,
    EventType.INHERITANCE_RECEIVED: _inheritance_received,
    EventType.FAMILY_FEUD:          _family_feud,
    # Aging arc
    EventType.LIFE_STAGE_TRANSITION: _life_stage_transition,
    EventType.MIDLIFE_CRISIS:        _midlife_crisis,
    EventType.ELDER_DECLINE:         _elder_decline,
    EventType.DEATH_PREPARATION:     _death_preparation,
    # Career depth
    EventType.DEMOTION:              _demotion,
    EventType.FIRED:                 _fired,
    EventType.WORKPLACE_ROMANCE:     _workplace_romance,
    EventType.WORKPLACE_RIVALRY:     _workplace_rivalry,
    EventType.CAREER_CHANGE:         _career_change,
    # Education
    EventType.SCHOOL_PERFORMANCE:   _school_performance,
    EventType.HOMEWORK_FAILURE:      _homework_failure,
    EventType.EXAM_SUCCESS:          _exam_success,
    EventType.SCHOLARSHIP:           _scholarship,
    EventType.GRADUATION:            _graduation,
    # Health depth
    EventType.CHRONIC_STRESS:        _chronic_stress,
    EventType.INJURY:                _injury,
    EventType.HOSPITALIZATION:       _hospitalization,
    # World / seasonal
    EventType.HEATWAVE_EVENT:       _heatwave_event,
    EventType.SNOW_DAY:             _snow_day,
    EventType.STORM_EVENT:          _storm_event,
    EventType.HOLIDAY_MOOD_SHIFT:   _holiday_mood_shift,
    EventType.SEASONAL_DEPRESSION:  _seasonal_depression,
    EventType.SEASONAL_BOOST:       _seasonal_boost,
    # Community
    EventType.FESTIVAL:             _festival,
    EventType.NEIGHBORHOOD_DISPUTE: _neighborhood_dispute,
    EventType.CLUB_MEETING_EVENT:   _club_meeting_event,
    EventType.PUBLIC_SCANDAL:       _public_scandal,
    EventType.LOCAL_CELEBRATION:    _local_celebration,
    # Household
    EventType.BILLS_CRISIS:         _bills_crisis,
    EventType.MOVING_HOUSE:         _moving_house,
    EventType.EVICTION_RISK:        _eviction_risk,
    EventType.ROOMMATE_CONFLICT:    _roommate_conflict,
    EventType.HOUSEHOLD_MERGE:      _household_merge,
    EventType.HOUSEHOLD_SPLIT:      _household_split,
    # Misc / passthrough
    EventType.RECOVERY:         lambda p, s, e, x: EventConsequences(moodlets=[(p, 'energised')], emotions=[(p, 'relief', 0.7, 8)], reputation_deltas=[(p, 2.0)]),
    EventType.RIVALRY:          lambda p, s, e, x: EventConsequences(moodlets=[(p, 'stressed')], emotions=[(p, 'anger', 0.5, 8)]),
    EventType.CRAFTED_ITEM:     lambda p, s, e, x: EventConsequences(moodlets=[(p, 'proud')], emotions=[(p, 'pride', 0.5, 5)], celebrity_deltas=[(p, 0.5)]),
    EventType.ANNIVERSARY:      lambda p, s, e, x: EventConsequences(moodlets=[(p, 'deeply_connected')], emotions=[(p, 'love', 0.7, 10)]),
    EventType.HOLIDAY:          lambda p, s, e, x: EventConsequences(emotions=[(p, 'joy', 0.6, 8)]),
    EventType.LLM_SUGGESTED:    _default,
    EventType.RUMOUR_SPREAD:    lambda p, s, e, x: EventConsequences(celebrity_deltas=[(p, 0.2)]),
    # Gossip / rumour
    EventType.RUMOUR_CREATED:       _rumour_created,
    EventType.RUMOUR_BELIEVED:      _rumour_believed,
    EventType.RUMOUR_DISPROVEN:     _rumour_disproven,
    EventType.REPUTATION_DAMAGE:    _reputation_damage,
}
