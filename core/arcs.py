"""
core/arcs.py — Long-running behavioural arcs that span multiple ticks.

Systems implemented here:
  1. Grief arc      — 5-stage Kübler-Ross progression (8 ticks)
  2. Loneliness arc — social drought tracking + escalating intensity
  3. Burnout arc    — high-perf + low-energy crash detection
  4. Habit formation — action repetition → need decay reduction
  5. Dream system   — sleep tick → hippocorpus dream narrative

Trauma (OCEAN drift) is handled in engine/engine.py since it fires
on resolved interactions, not per-tick.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim

# ── 1. GRIEF ARC ──────────────────────────────────────────────────────────────

GRIEF_STAGES = [
    ("denial",      "confusion",    0.45),   # stage 0
    ("anger",       "anger",        0.25),   # stage 1
    ("bargaining",  "nervousness",  0.35),   # stage 2
    ("depression",  "grief",        0.15),   # stage 3
    ("acceptance",  "relief",       0.60),   # stage 4
]
GRIEF_TICKS_PER_STAGE = 2
GRIEF_ENERGY_PENALTY  = 0.5   # extra energy decay multiplier during grief
GRIEF_SOCIAL_PENALTY  = 0.6   # extra social decay during grief


def grief_tick(sim: "Sim") -> None:
    """Advance the grief arc by one tick, apply stage emotion and penalties."""
    stage = getattr(sim, "grief_stage", -1)
    if stage < 0:
        return

    sim._grief_tick_count = getattr(sim, "_grief_tick_count", 0) + 1
    stage_index = min(stage, len(GRIEF_STAGES) - 1)
    name, emotion, valence = GRIEF_STAGES[stage_index]

    # Apply stage emotion each tick
    sim.emotion.add(emotion, 0.6, duration=3, source=f"grief:{name}")

    # Extra need decay during grief
    sim.needs.energy = max(0, sim.needs.energy - 1.5 * GRIEF_ENERGY_PENALTY)
    sim.needs.social = max(0, sim.needs.social - 1.5 * GRIEF_SOCIAL_PENALTY)

    # Advance stage every GRIEF_TICKS_PER_STAGE ticks
    if sim._grief_tick_count >= GRIEF_TICKS_PER_STAGE:
        sim._grief_tick_count = 0
        sim.grief_stage += 1
        if sim.grief_stage >= len(GRIEF_STAGES):
            # Arc complete — final acceptance
            sim.grief_stage = -1
            sim.grief_target = ""
            sim.emotion.add("relief", 0.5, duration=6, source="grief:accepted")


def start_grief(sim: "Sim", target: str) -> None:
    """Trigger the grief arc for a Sim. target is what/who was lost."""
    sim.grief_stage = 0
    sim.grief_target = target
    sim._grief_tick_count = 0
    sim.emotion.add("confusion", 0.8, duration=4, source="grief:onset")


def grief_unlocks_reminisce(sim: "Sim") -> bool:
    """After grief completes, 'fond memory' reminisce is unlocked for one tick."""
    return getattr(sim, "grief_stage", -1) == -1 and getattr(sim, "grief_target", "") != ""


# ── 2. LONELINESS ARC ─────────────────────────────────────────────────────────

LONELINESS_THRESHOLD      = 8    # ticks without social interaction
LONELINESS_REP_DECAY      = 0.3  # reputation lost per drought tick beyond threshold
LONELINESS_SOCIAL_BOOST   = 0.4  # extra social need pressure multiplier


def loneliness_tick(sim: "Sim", had_interaction: bool) -> None:
    """Update loneliness counter. Called every tick by engine."""
    if had_interaction:
        sim._social_drought_ticks = 0
    else:
        sim._social_drought_ticks = getattr(sim, "_social_drought_ticks", 0) + 1

    drought = sim._social_drought_ticks
    if drought >= LONELINESS_THRESHOLD:
        # Reputation decay from social withdrawal
        sim.reputation_score = max(-100,
            sim.reputation_score - LONELINESS_REP_DECAY)
        # Emotion: sadness/loneliness
        if drought % 3 == 0:
            sim.emotion.add("sadness", 0.4, duration=4, source="loneliness")


def is_lonely(sim: "Sim") -> bool:
    return getattr(sim, "_social_drought_ticks", 0) >= LONELINESS_THRESHOLD


# ── 3. BURNOUT ARC ────────────────────────────────────────────────────────────

BURNOUT_PERF_THRESHOLD  = 75
BURNOUT_ENERGY_THRESHOLD = 35
BURNOUT_TRIGGER_TICKS   = 6
BURNOUT_RECOVERY_THRESHOLD = 60
BURNOUT_RECOVERY_TICKS  = 4


def burnout_tick(sim: "Sim") -> None:
    """Update burnout counter. Called every tick."""
    high_perf = sim.career_performance > BURNOUT_PERF_THRESHOLD
    low_energy = sim.needs.energy < BURNOUT_ENERGY_THRESHOLD

    if high_perf and low_energy:
        sim._high_perf_low_energy_ticks = getattr(sim, "_high_perf_low_energy_ticks", 0) + 1
    else:
        sim._high_perf_low_energy_ticks = 0

    if getattr(sim, "_burnout_active", False):
        # Recovery check
        sim._burnout_recovery_ticks = getattr(sim, "_burnout_recovery_ticks", 0)
        if sim.needs.fun >= BURNOUT_RECOVERY_THRESHOLD and sim.needs.energy >= BURNOUT_RECOVERY_THRESHOLD:
            sim._burnout_recovery_ticks += 1
            if sim._burnout_recovery_ticks >= BURNOUT_RECOVERY_TICKS:
                sim._burnout_active = False
                sim._burnout_recovery_ticks = 0
                sim.emotion.add("relief", 0.7, duration=6, source="burnout:recovered")
        else:
            sim._burnout_recovery_ticks = 0
        # Burnout effects: fun decays faster
        sim.needs.fun = max(0, sim.needs.fun - 1.0)


def should_trigger_burnout(sim: "Sim") -> bool:
    """
    System 10 — Detect burnout via NLI when threshold is close;
    fall back to tick counter for clear-cut cases.
    """
    counter_triggered = (
        getattr(sim, "_high_perf_low_energy_ticks", 0) >= BURNOUT_TRIGGER_TICKS
        and not getattr(sim, "_burnout_active", False)
    )
    if counter_triggered:
        return True

    # Near-threshold: NLI state description for nuanced detection
    ticks = getattr(sim, "_high_perf_low_energy_ticks", 0)
    if ticks < BURNOUT_TRIGGER_TICKS // 2 or getattr(sim, "_burnout_active", False):
        return False
    try:
        from llm.small_models import zero_shot_classify
        state = (
            f"Sim career_performance={sim.career_performance:.0f}, "
            f"energy={sim.needs.energy:.0f}, fun={sim.needs.fun:.0f}, "
            f"emotion={sim.emotion.dominant}, "
            f"high_perf_low_energy_streak={ticks}_ticks"
        )
        result = zero_shot_classify(
            state,
            ["experiencing burnout from overwork", "energised and performing well"],
            threshold=0.65,
        )
        return result is not None and "burnout" in result[0]
    except Exception:
        return False


def is_lonely_nli(sim: "Sim") -> bool:
    """
    System 10 — NLI-augmented loneliness detection for near-threshold states.
    Supplements the tick counter in is_lonely().
    """
    if is_lonely(sim):
        return True
    drought = getattr(sim, "_social_drought_ticks", 0)
    if drought < LONELINESS_THRESHOLD // 2:
        return False
    try:
        from llm.small_models import zero_shot_classify
        state = (
            f"Sim has had no social interaction for {drought} ticks. "
            f"Social need={sim.needs.social:.0f}/100, emotion={sim.emotion.dominant}."
        )
        result = zero_shot_classify(
            state,
            ["feeling lonely and socially isolated", "socially fulfilled"],
            threshold=0.62,
        )
        return result is not None and "lonely" in result[0]
    except Exception:
        return False


def apply_burnout(sim: "Sim") -> None:
    """Fire the burnout event on a Sim."""
    sim.career_performance = max(0, sim.career_performance - 20)
    sim._burnout_active = True
    sim._high_perf_low_energy_ticks = 0
    sim._burnout_recovery_ticks = 0
    sim.emotion.add("sadness", 0.8, duration=8, source="burnout")
    sim.emotion.add("annoyance", 0.6, duration=6, source="burnout:irritability")


# ── 4. HABIT FORMATION ────────────────────────────────────────────────────────

HABIT_THRESHOLD     = 5    # repetitions to form a habit
INGRAINED_THRESHOLD = 20   # repetitions for ingrained habit

HABIT_NEED_DECAY_REDUCTION = 0.15  # 15% less decay for the associated need
HABIT_SATISFACTION_BONUS   = 0.05  # extra need restoration per habit level


def register_action_history(sim: "Sim", action: str) -> None:
    """Record that this sim performed an action. Called post-interaction."""
    if not hasattr(sim, "action_history"):
        sim.action_history = {}
    sim.action_history[action] = sim.action_history.get(action, 0) + 1


def get_habit_level(sim: "Sim", action: str) -> int:
    """0 = no habit, 1 = habit (5+), 2 = ingrained (20+)."""
    count = getattr(sim, "action_history", {}).get(action, 0)
    if count >= INGRAINED_THRESHOLD:
        return 2
    if count >= HABIT_THRESHOLD:
        return 1
    return 0


def habit_cooldown_modifier(sim: "Sim", action: str) -> float:
    """Return cooldown multiplier: ingrained habits have halved cooldown."""
    level = get_habit_level(sim, action)
    return 0.5 if level == 2 else 1.0


# ── 5. DREAM SYSTEM ───────────────────────────────────────────────────────────

DREAM_CHANCE     = 0.40   # probability during low-energy sleep tick
DREAM_ENERGY_MAX = 15     # sim must have energy below this to sleep/dream


def maybe_generate_dream(sim: "Sim") -> str | None:
    """
    If a Sim's energy is critically low (sleep state) and RNG fires,
    return a dream narrative string. None otherwise.
    """
    if sim.needs.energy > DREAM_ENERGY_MAX:
        return None
    if random.random() > DREAM_CHANCE:
        return None

    # Nightmare: highest-severity fear (personalised, Gap 8)
    if sim.fears and random.random() < 0.5:
        fear = max(sim.fears, key=lambda f: f.severity)
        try:
            from datasets.hippocorpus import sample_narrative_scaffold
            scaffold = sample_narrative_scaffold(-0.7, sim.ocean.get("openness", 0.5))
            return (
                f"[DREAM — nightmare] {sim.name} dreams vividly about '{fear.label}' "
                f"(severity {fear.severity:.2f}). "
                f"Tone: {scaffold[:150] if scaffold else 'fragmented and anxious.'}"
            )
        except Exception:
            return (
                f"[DREAM — nightmare] {sim.name} dreams about '{fear.label}' "
                f"(severity {fear.severity:.2f})."
            )

    # Wish-fulfillment: highest-priority active want (personalised, Gap 8)
    if sim.active_wants:
        want = max(sim.active_wants, key=lambda w: w.priority)
        try:
            from datasets.hippocorpus import sample_narrative_scaffold
            scaffold = sample_narrative_scaffold(0.75, sim.ocean.get("openness", 0.5))
            return (
                f"[DREAM — wish] {sim.name} dreams of fulfilling '{want.description}'. "
                f"Tone: {scaffold[:150] if scaffold else 'warm and hopeful.'}"
            )
        except Exception:
            return f"[DREAM — wish] {sim.name} dreams of '{want.description}'."

    return None
