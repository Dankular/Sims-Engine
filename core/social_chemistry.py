from __future__ import annotations

from dataclasses import dataclass


ASPIRATION_MATRIX = {
    ("Romance", "Romance"): 35.0,
    ("Family", "Family"): 35.0,
    ("Knowledge", "Knowledge"): 35.0,
    ("Fortune", "Fortune"): 35.0,
    ("Popularity", "Popularity"): 35.0,
    ("Creative", "Creative"): 35.0,
}

ZODIAC_MATRIX = {
    ("Aries", "Leo"): 8.0,
    ("Leo", "Aries"): 8.0,
    ("Cancer", "Scorpio"): 8.0,
    ("Scorpio", "Cancer"): 8.0,
}

PERSONALITY_KEYS = ["neat", "outgoing", "active", "playful", "nice"]


@dataclass
class ChemistryResult:
    a_to_b: float
    b_to_a: float
    chemistry: float
    level: str


def _aspiration_score(a: dict, b: dict) -> float:
    if a.get("aspiration") == b.get("aspiration"):
        return 35.0
    return ASPIRATION_MATRIX.get((a.get("aspiration"), b.get("aspiration")), 0.0)


def _zodiac_score(a: dict, b: dict) -> float:
    return ZODIAC_MATRIX.get((a.get("zodiac"), b.get("zodiac")), 0.0)


def _personality_score(a: dict, b: dict) -> float:
    pa = a.get("attraction_profile", {}).get("personality", {})
    pb = b.get("attraction_profile", {}).get("personality", {})
    total = 0.0
    mid = 5.0
    for key in PERSONALITY_KEYS:
        av = float(pa.get(key, 5.0))
        bv = float(pb.get(key, 5.0))
        if abs(av - mid) < 0.01 or abs(bv - mid) < 0.01:
            continue
        same_side = (av > mid and bv > mid) or (av < mid and bv < mid)
        if same_side:
            total += max(0.0, 7.0 - abs(av - bv) / 1.5)
        else:
            total -= max(1.0, abs(av - mid) + abs(bv - mid))
    return total


def _turn_on_off_score(a: dict, b: dict) -> float:
    ap = a.get("attraction_profile", {})
    turn_ons = set(ap.get("turn_ons", []))
    turn_off = ap.get("turn_off", "")
    b_traits = set(b.get("traits", []))
    score = 0.0
    score += 17.5 * len(turn_ons & b_traits)
    if turn_off and turn_off in b_traits:
        score -= 22.5
    return score


def attraction(a: dict, b: dict) -> float:
    score = 0.0
    score += _aspiration_score(a, b)
    score += _zodiac_score(a, b)
    score += _personality_score(a, b)
    score += _turn_on_off_score(a, b)
    return max(-100.0, min(100.0, round(score, 2)))


def chemistry_level(score: float) -> str:
    if score <= -25:
        return "repulsion"
    if score <= 0:
        return "neutral"
    if score <= 34:
        return "low"
    if score <= 89:
        return "medium"
    return "high"


def calculate_chemistry(sim_a, sim_b) -> ChemistryResult:
    a_to_b = attraction(sim_a.profile, sim_b.profile)
    b_to_a = attraction(sim_b.profile, sim_a.profile)
    chem = round((a_to_b + b_to_a) / 2.0, 2)
    return ChemistryResult(
        a_to_b=a_to_b, b_to_a=b_to_a, chemistry=chem, level=chemistry_level(chem)
    )
