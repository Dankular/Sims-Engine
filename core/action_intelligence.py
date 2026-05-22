from __future__ import annotations

from typing import Any


def score_action_feasibility(sim: Any, action: str, env: dict[str, float]) -> float:
    a = (action or "").lower()
    needs = getattr(sim, "needs", None)
    energy = float(getattr(needs, "energy", 50.0) or 50.0)
    social = float(getattr(needs, "social", 50.0) or 50.0)
    hunger = float(getattr(needs, "hunger", 50.0) or 50.0)

    score = 1.0
    if (
        any(k in a for k in ("clean", "repair", "move", "carry", "workout"))
        and energy < 30
    ):
        score -= 0.45
    if (
        any(k in a for k in ("chat", "confide", "share", "date", "flirt"))
        and social < 20
    ):
        score -= 0.25
    if any(k in a for k in ("cook", "host", "decorate")) and hunger < 18:
        score -= 0.22

    noise = float(env.get("ambient_noise", 0.0) or 0.0)
    crowd = float(env.get("crowd_density", 0.0) or 0.0)
    intimacy = float(env.get("intimacy", 0.0) or 0.0)
    if any(k in a for k in ("discuss", "confide", "fears", "secret")):
        score += (intimacy * 0.25) - (noise * 0.20)
    if any(k in a for k in ("joke", "tease", "party")):
        score += crowd * 0.15

    return max(0.05, min(1.5, round(score, 3)))


def compute_social_risk(
    sim_a: Any, sim_b: Any, relationship: Any, action: str
) -> float:
    a = (action or "").lower()
    romance = float(getattr(relationship, "romance", 0.0) or 0.0)
    friendship = float(getattr(relationship, "friendship", 0.0) or 0.0)
    jealous = float(getattr(relationship, "jealousy_score", 0.0) or 0.0)

    risk = 0.1
    if any(k in a for k in ("flirt", "kiss", "love", "hands")):
        risk += 0.35 if romance < 30 else 0.12
        risk += min(0.25, jealous / 300.0)
    if any(k in a for k in ("argue", "insult", "mock", "rumour")):
        risk += 0.25
    if friendship >= 60:
        risk -= 0.12
    if friendship <= 0:
        risk += 0.12
    if getattr(sim_b, "emotion", None) is not None and getattr(
        sim_b.emotion, "dominant", ""
    ) in {"anger", "annoyance"}:
        risk += 0.15
    return max(0.0, min(1.0, round(risk, 3)))


def build_action_chain(sim: Any, action: str) -> list[str]:
    a = (action or "").lower()
    if any(k in a for k in ("flirt", "date", "love")):
        return ["chat", "compliment", action]
    if any(k in a for k in ("argue", "insult", "mock")):
        return ["raise concern", "state boundary", action]
    if any(k in a for k in ("confide", "fears", "secret")):
        return ["check in", "listen actively", action]
    return [action]


def apply_interruption(action_ctx: dict[str, Any]) -> str | None:
    if action_ctx.get("fire_risk", 0.0) > 0.7:
        return "respond to fire alarm"
    if action_ctx.get("bladder_critical", False):
        return "use bathroom"
    if action_ctx.get("energy_critical", False):
        return "go rest"
    return None


def explain_choice(
    action: str, weight: float, feasibility: float, risk: float, env: dict[str, float]
) -> str:
    return (
        f"Picked '{action}' with weight={weight:.2f}; feasibility={feasibility:.2f}; "
        f"social_risk={risk:.2f}; noise={env.get('ambient_noise', 0.0):.2f}; "
        f"crowd={env.get('crowd_density', 0.0):.2f}."
    )
