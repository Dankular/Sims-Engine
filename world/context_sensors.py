from __future__ import annotations

from typing import Any


def sense_context(
    engine: Any, sim_a: Any, sim_b: Any | None = None
) -> dict[str, float]:
    venue = getattr(sim_a, "_current_venue", {}) or {}
    noise = float(venue.get("noise", 0.3) or 0.3)
    crowd = float(venue.get("crowd", 0.4) or 0.4)
    intimacy = float(venue.get("intimacy", 0.5) or 0.5)

    rel_conflict = 0.0
    if sim_b is not None and engine is not None and hasattr(engine, "relationships"):
        try:
            rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
            rel_conflict = max(
                0.0, min(1.0, -float(getattr(rel, "friendship", 0.0)) / 100.0)
            )
        except Exception:
            rel_conflict = 0.0

    cleanliness = 0.6
    try:
        if engine is not None and hasattr(engine, "cleanliness"):
            rs = engine.cleanliness.room_state()
            if isinstance(rs, dict) and rs:
                values = []
                for v in rs.values():
                    if isinstance(v, dict):
                        values.append(float(v.get("cleanliness", 0.6) or 0.6))
                if values:
                    cleanliness = max(0.0, min(1.0, sum(values) / len(values)))
    except Exception:
        cleanliness = 0.6

    return {
        "ambient_noise": max(0.0, min(1.0, noise)),
        "crowd_density": max(0.0, min(1.0, crowd)),
        "intimacy": max(0.0, min(1.0, intimacy)),
        "room_cleanliness": cleanliness,
        "recent_conflict_nearby": rel_conflict,
        "object_affordance_score": _object_affordance_score(engine, sim_a),
    }


def _object_affordance_score(engine: Any, sim: Any) -> float:
    score = 0.4
    try:
        inv = getattr(sim, "inventory_objects", []) or []
        tags = {str(x.get("type", "")).lower() for x in inv if isinstance(x, dict)}
        if tags & {"book", "tool", "medical", "clothing", "collectible"}:
            score += 0.25
        if tags & {"weapon", "explosive"}:
            score -= 0.1
    except Exception:
        pass
    return max(0.0, min(1.0, score))
