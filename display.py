"""
sim_v2/display.py — Rich terminal output for the simulation.
Subscribes to the engine event bus and prints tick-by-tick narrative.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config import GAME_START_HOUR, NEED_CRITICAL, NEED_LOW

if TYPE_CHECKING:
    from engine.engine import SimEngine

_SEP = "═" * 62

_EMOTION_EMOJI = {
    "joy": "😄", "love": "❤️", "excitement": "🤩", "admiration": "🤩",
    "amusement": "😂", "gratitude": "🙏", "optimism": "🌟", "pride": "😤",
    "relief": "😌", "approval": "👍", "caring": "🤗", "curiosity": "🤔",
    "surprise": "😲", "realization": "💡", "desire": "😍", "neutral": "😐",
    "sadness": "😢", "grief": "😭", "disappointment": "😞", "remorse": "😔",
    "anger": "😠", "annoyance": "😒", "disgust": "🤢", "disapproval": "👎",
    "embarrassment": "😳", "fear": "😨", "nervousness": "😰", "confusion": "😕",
}


def _need_bar(value: float, name: str) -> str:
    if value < NEED_CRITICAL:
        icon = "🔴"
    elif value < NEED_LOW:
        icon = "🟡"
    else:
        icon = "🟢"
    return f"{name[:3].upper()}:{icon}{int(value):3d}"


def _needs_line(sim) -> str:
    names = ["hunger", "energy", "social", "fun", "hygiene", "comfort"]
    return "  ".join(_need_bar(getattr(sim.needs, n), n) for n in names)


def _emotion_line(sim) -> str:
    emoji = _EMOTION_EMOJI.get(sim.emotion.dominant, "😐")
    moodlets = sim.emotion.moodlets
    if moodlets:
        details = "  ".join(
            f"{m.label}({int(m.intensity * 100)}%)" for m in moodlets[:3]
        )
        return f"{emoji} {sim.emotion.dominant}  [{details}]"
    return f"{emoji} {sim.emotion.dominant}"


def _skills_line(sim) -> str:
    abbr = {"charisma": "CHA", "comedy": "COM", "cooking": "COK",
            "fitness": "FIT", "logic": "LOG", "creativity": "CRE"}
    return "  ".join(
        f"{abbr.get(k, k[:3].upper())}:{v:.1f}"
        for k, v in sim.skills.levels.items()
    )


def print_sim_profile(sim) -> None:
    o = sim.profile["ocean"]
    print(
        f"\n  {'─'*56}\n"
        f"  {sim.name}  |  {sim.profile.get('age', '?')}yo {sim.profile['gender']}"
        f"  |  {sim.profile['job']}  |  §{sim.simoleons:.0f}\n"
        f"  Aspiration : {sim.profile['aspiration']}\n"
        f"  Traits     : {', '.join(sim.profile['traits'])}\n"
        f"  OCEAN      : O={o['openness']:.2f}  C={o['conscientiousness']:.2f}"
        f"  E={o['extraversion']:.2f}  A={o['agreeableness']:.2f}  N={o['neuroticism']:.2f}\n"
        f"  Skills     : {_skills_line(sim)}\n"
        f"  Summary    : {sim.profile.get('self_summary', '')}\n"
        f"  {'─'*56}"
    )


def print_tick_header(engine: SimEngine) -> None:
    tick = engine.tick_count
    hour = (GAME_START_HOUR + tick) % 24
    time_label = f"{hour:02d}:00"
    pending = len(engine._pending)
    pending_str = f"  ⏳ {pending} pending" if pending else ""
    print(f"\n{_SEP}")
    print(f"  TICK {tick:03d}  |  {time_label}  |{pending_str}")
    print(_SEP)


def print_active_sims(engine: SimEngine) -> None:
    from sim_types.enums import LODTier
    active = [s for s in engine.sims if s.lod_tier == LODTier.ACTIVE]
    for sim in active:
        wants_str = (
            "  ".join(f"'{w.description}'" for w in sim.active_wants[:2])
            or "none yet"
        )
        fears_str = (
            ", ".join(f.label for f in sim.fears[:2]) if sim.fears else "none"
        )
        print(
            f"\n  [{sim.name}]  §{sim.simoleons:.0f}"
            f"  LOD:{sim.lod_tier.name}  perf:{sim.career_performance:.0f}"
        )
        print(f"    Needs  : {_needs_line(sim)}")
        print(f"    Emotion: {_emotion_line(sim)}")
        print(f"    Wants  : {wants_str}")
        if sim.fears:
            print(f"    Fears  : {fears_str}")


def _on_interaction_resolved(engine: SimEngine, **kwargs) -> None:
    sim_a = kwargs["sim_a"]
    sim_b = kwargs["sim_b"]
    result = kwargs["result"]
    valence = kwargs["valence"]
    tick = kwargs["tick"]
    interaction_id = kwargs.get("interaction_id", "")

    rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
    fd = float(result.get("friendship_delta", 0))
    rd = float(result.get("romance_delta", 0))
    reaction = result.get("sim_b_reaction", "")
    memory = result.get("memory_tag", "")
    reasoning = result.get("reasoning", "")
    emo_a = result.get("emotion_a", sim_a.emotion.dominant)
    emo_b = result.get("emotion_b", sim_b.emotion.dominant)

    interaction_label = ""
    for item in engine._pending:
        if item.sim_a_id == sim_a.sim_id and item.sim_b_id == sim_b.sim_id:
            interaction_label = item.interaction
            break

    id_tag = f"  #{interaction_id}" if interaction_id else ""
    print(
        f"\n  ✅ RESOLVED  [tick {tick}]{id_tag}  {sim_a.name} → {sim_b.name}"
        + (f"  [{interaction_label}]" if interaction_label else "")
    )
    if reaction:
        print(f"     {sim_b.name}: \"{reaction}\"")
    print(
        f"     F:{fd:+.1f}→{rel.friendship:.0f}  R:{rd:+.1f}→{rel.romance:.0f}"
        f"  Valence:{valence:+.2f}  [{rel.state_label()}]"
    )
    print(f"     Emotions: {sim_a.name}={emo_a}  {sim_b.name}={emo_b}")
    if memory:
        print(f"     Memory  : \"{memory}\"")
    if reasoning:
        print(f"     Reason  : {reasoning}")


def _on_career_event(engine: SimEngine, **kwargs) -> None:
    sim = kwargs["sim"]
    result = kwargs["result"]
    print(
        f"\n  💼 CAREER EVENT — {sim.name} ({result.get('event_type', '?')})\n"
        f"     {result.get('narrative', '')}\n"
        f"     Performance: {result.get('performance_delta', 0):+.0f} → {sim.career_performance:.0f}"
        f"  |  §{result.get('simoleon_delta', 0):+.0f}"
    )


def _on_life_event(engine: SimEngine, **kwargs) -> None:
    sim_a = kwargs["sim_a"]
    result = kwargs["result"]
    event_type = result.get("event_type", "life event")
    print(
        f"\n  🌟 LIFE EVENT [{event_type}] — {sim_a.name}\n"
        f"     {result.get('narrative', '')}"
    )


def print_summary(engine: SimEngine) -> None:
    print(f"\n{_SEP}")
    print("  SIMULATION SUMMARY")
    print(_SEP)

    for sim in engine.sims:
        print(
            f"\n  {sim.name}  |  {sim.profile['job']}  |  §{sim.simoleons:.0f}"
            f"  |  perf:{sim.career_performance:.0f}  |  LOD:{sim.lod_tier.name}"
        )
        print(f"    Skills : {_skills_line(sim)}")
        if sim.fears:
            print(f"    Fears  : {', '.join(f.label for f in sim.fears)}")

    print(f"\n  {'─'*56}")
    print("  RELATIONSHIPS")
    print(f"  {'─'*56}")
    seen: set[frozenset] = set()
    for a in engine.sims:
        for b in engine.sims:
            if a is b:
                continue
            key = frozenset({a.sim_id, b.sim_id})
            if key in seen:
                continue
            seen.add(key)
            rel = engine.relationships.get(a.sim_id, b.sim_id)
            if rel.interactions == 0:
                continue
            last_mem = rel.memories[-1] if rel.memories else None
            last = (
                f"#{last_mem['id']}  {last_mem['tag']}" if last_mem else "—"
            )
            print(
                f"\n  {a.name} ↔ {b.name}\n"
                f"    Friendship : {rel.friendship:.0f}  [{rel.state_label()}]\n"
                f"    Romance    : {rel.romance:.0f}  [{rel.romance_label()}]\n"
                f"    Interactions: {rel.interactions}\n"
                f"    Last memory: {last}"
            )
    print()


def _on_interaction_queued(engine: SimEngine, **kwargs) -> None:
    sim_a = kwargs["sim_a"]
    sim_b = kwargs["sim_b"]
    interaction = kwargs["interaction"]
    interaction_id = kwargs.get("interaction_id", "")
    rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
    print(
        f"\n  ⚡ QUEUED  #{interaction_id}  {sim_a.name} → {sim_b.name}  [{interaction}]\n"
        f"     Rel: {rel.state_label()}  (F={rel.friendship:.0f})  — LLM thinking…"
    )


def attach(engine: SimEngine) -> None:
    """Subscribe display callbacks to the engine event bus."""
    engine._bus.on("interaction_queued",
                   lambda **kw: _on_interaction_queued(engine, **kw))
    engine._bus.on("interaction_resolved",
                   lambda **kw: _on_interaction_resolved(engine, **kw))
    engine._bus.on("career_event",
                   lambda **kw: _on_career_event(engine, **kw))
    engine._bus.on("life_event",
                   lambda **kw: _on_life_event(engine, **kw))
