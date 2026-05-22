"""
core/compatibility.py — Attraction and personality chemistry scoring.

attraction_score(sim_a, sim_b) → float  (-1.0..+1.0)

Factors:
  - OCEAN complementarity (some dimensions benefit from similarity, others from contrast)
  - Shared interests (bonding over common passions)
  - Likes/dislikes overlap (turn-ons vs turn-offs)
  - Dealbreaker clashes (hard penalties)
  - MBTI compatibility (if available)

Used in:
  - engine/scheduler.py pick_interaction_pair  → romantic bonus on score
  - engine/scheduler.py choose_interaction     → unlocks romantic actions earlier
  - engine/engine.py   get_state()             → exposed per-pair
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from core.traits import trait_compatibility_bonus
from core.knowledge_aspiration import knowledge_relationship_compatibility

if TYPE_CHECKING:
    from core.sim import Sim


def attraction_score(sim_a: "Sim", sim_b: "Sim") -> float:
    """
    Compute a chemistry score between two sims.
    Returns a float in -1.0..+1.0.
    Positive = strong mutual pull; negative = fundamental incompatibility.
    """
    ocean_a = sim_a.ocean
    ocean_b = sim_b.ocean
    p_a = sim_a.profile
    p_b = sim_b.profile

    score = 0.0

    # ── OCEAN compatibility (35% of score) ───────────────────────────────────
    # Openness: similarity is attractive (shared curiosity)
    score += (1.0 - abs(ocean_a["openness"] - ocean_b["openness"])) * 0.10

    # Extraversion: slight opposites attract (introvert + extrovert balance)
    ext_diff = abs(ocean_a["extraversion"] - ocean_b["extraversion"])
    score += (0.5 - abs(ext_diff - 0.3)) * 0.08  # peak at 0.3 difference

    # Agreeableness: both high = very compatible; both low = friction
    score += (ocean_a["agreeableness"] + ocean_b["agreeableness"]) / 2 * 0.08

    # Conscientiousness: similarity reduces conflict
    score += (
        1.0 - abs(ocean_a["conscientiousness"] - ocean_b["conscientiousness"])
    ) * 0.05

    # Neuroticism: both high = volatile pairing (penalty); one stable = buffer
    n_avg = (ocean_a["neuroticism"] + ocean_b["neuroticism"]) / 2
    score -= n_avg * 0.04

    # ── Shared interests (25% of score) ──────────────────────────────────────
    interests_a = set(p_a.get("interests", []))
    interests_b = set(p_b.get("interests", []))
    if interests_a and interests_b:
        jaccard = len(interests_a & interests_b) / len(interests_a | interests_b)
        score += jaccard * 0.25

    # ── Likes/dislikes compatibility (20% of score) ───────────────────────────
    likes_a = set(p_a.get("likes", []))
    likes_b = set(p_b.get("likes", []))
    dislikes_a = set(p_a.get("dislikes", []))
    dislikes_b = set(p_b.get("dislikes", []))

    # Shared likes → bonding
    if likes_a and likes_b:
        shared_likes = len(likes_a & likes_b)
        score += min(shared_likes * 0.06, 0.18)

    # Shared dislikes → mutual understanding (weaker bond than shared likes)
    if dislikes_a and dislikes_b:
        shared_dislikes = len(dislikes_a & dislikes_b)
        score += min(shared_dislikes * 0.03, 0.09)

    # A's dislikes overlap with B's likes → friction
    if dislikes_a and likes_b:
        clash = len(dislikes_a & likes_b)
        score -= clash * 0.06

    if dislikes_b and likes_a:
        clash = len(dislikes_b & likes_a)
        score -= clash * 0.06

    # ── Dealbreaker clashes (hard penalty) ───────────────────────────────────
    db_a = set(p_a.get("dealbreakers", []))
    db_b = set(p_b.get("dealbreakers", []))
    traits_a = set(p_a.get("traits", []))
    traits_b = set(p_b.get("traits", []))

    # Sim B's traits trigger Sim A's dealbreakers
    for db in db_a:
        if any(db.lower() in t.lower() for t in traits_b):
            score -= 0.20

    for db in db_b:
        if any(db.lower() in t.lower() for t in traits_a):
            score -= 0.20

    # ── MBTI compatibility bonus (if available) ───────────────────────────────
    mbti_a = p_a.get("mbti", "")
    mbti_b = p_b.get("mbti", "")
    if mbti_a and mbti_b and len(mbti_a) == 4 and len(mbti_b) == 4:
        score += _mbti_bonus(mbti_a, mbti_b) * 0.10

    # Trait compatibility/conflict layer
    score += trait_compatibility_bonus(sim_a, sim_b)
    score += knowledge_relationship_compatibility(sim_a, sim_b)

    return max(-1.0, min(1.0, round(score, 3)))


def _mbti_bonus(a: str, b: str) -> float:
    """Simple MBTI compatibility heuristic. Range: -0.5..+1.0."""
    # Shared J/P → compatible lifestyles
    lifestyle = 0.2 if a[3] == b[3] else -0.1
    # Shared N/S → shared worldview
    intuition = 0.2 if a[1] == b[1] else 0.0
    # Complementary I/E → balance
    social = 0.2 if a[0] != b[0] else 0.0
    return lifestyle + intuition + social


# ── Compatibility label ───────────────────────────────────────────────────────


def compatibility_label(score: float) -> str:
    if score >= 0.60:
        return "soulmates"
    if score >= 0.35:
        return "great match"
    if score >= 0.15:
        return "good chemistry"
    if score >= -0.10:
        return "neutral"
    if score >= -0.35:
        return "friction"
    return "incompatible"
