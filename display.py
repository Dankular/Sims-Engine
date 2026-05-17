"""
display.py — Rich terminal output for The Sims Engine.

Replaces the plain-text print functions with rich panels, tables, and
colour-coded output.  All functions preserve the same call signatures as
the old display.py so __main__.py requires only minor changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from config import GAME_START_HOUR, NEED_CRITICAL, NEED_LOW

if TYPE_CHECKING:
    from engine.engine import SimEngine

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    _RICH = True
except ImportError:
    _RICH = False

_console = Console() if _RICH else None

# ── Colour maps ───────────────────────────────────────────────────────────────
_EMOTION_COLOUR = {
    "joy": "bright_yellow",
    "love": "bright_red",
    "excitement": "bright_cyan",
    "admiration": "cyan",
    "amusement": "yellow",
    "gratitude": "green",
    "optimism": "bright_green",
    "pride": "bright_magenta",
    "relief": "green",
    "approval": "green",
    "caring": "bright_cyan",
    "curiosity": "cyan",
    "surprise": "bright_white",
    "realization": "bright_white",
    "desire": "magenta",
    "neutral": "white",
    "sadness": "blue",
    "grief": "bright_blue",
    "disappointment": "blue",
    "remorse": "blue",
    "anger": "bright_red",
    "annoyance": "red",
    "disgust": "red",
    "disapproval": "red",
    "embarrassment": "magenta",
    "fear": "red",
    "nervousness": "yellow",
    "confusion": "yellow",
}

_EMOTION_EMOJI = {
    "joy": "😄",
    "love": "❤️ ",
    "excitement": "🤩",
    "admiration": "🤩",
    "amusement": "😂",
    "gratitude": "🙏",
    "optimism": "🌟",
    "pride": "😤",
    "relief": "😌",
    "approval": "👍",
    "caring": "🤗",
    "curiosity": "🤔",
    "surprise": "😲",
    "realization": "💡",
    "desire": "😍",
    "neutral": "😐",
    "sadness": "😢",
    "grief": "😭",
    "disappointment": "😞",
    "remorse": "😔",
    "anger": "😠",
    "annoyance": "😒",
    "disgust": "🤢",
    "disapproval": "👎",
    "embarrassment": "😳",
    "fear": "😨",
    "nervousness": "😰",
    "confusion": "😕",
}

_VALENCE_COLOUR = {
    "positive": "bright_green",
    "neutral": "bright_white",
    "negative": "bright_red",
}

_ARC_BADGE = {
    "grief": "[bold bright_blue]GRIEF [/]",
    "burnout": "[bold bright_red]BURNOUT[/]",
    "lonely": "[bold blue]LONELY [/]",
}


def _valence_colour(v: float) -> str:
    if v > 0.15:
        return "bright_green"
    if v < -0.15:
        return "bright_red"
    return "bright_white"


def _need_bar(value: float, width: int = 4) -> str:
    filled = round(value / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    if value < NEED_CRITICAL:
        return f"[bright_red]{bar}[/]"
    if value < NEED_LOW:
        return f"[yellow]{bar}[/]"
    return f"[bright_green]{bar}[/]"


def _arc_badge(sim) -> str:
    from core.arcs import is_lonely

    if getattr(sim, "grief_stage", -1) >= 0:
        stage = sim.grief_stage
        stages = ["deny", "anger", "barg", "dep", "accept"]
        lbl = stages[min(stage, 4)]
        return f"[bold bright_blue]GRIEF:{lbl}[/]"
    if getattr(sim, "_burnout_active", False):
        return "[bold bright_red]BURNOUT[/]"
    if is_lonely(sim):
        return "[bold blue]LONELY[/]"
    goal = getattr(sim, "_active_goal", None)
    if goal:
        return f"[dim cyan]→{goal.action_type[:8]}[/]"
    return "[dim]—[/]"


def _skills_abbr(sim) -> str:
    abbr = {
        "charisma": "CHA",
        "comedy": "COM",
        "cooking": "COK",
        "fitness": "FIT",
        "logic": "LOG",
        "creativity": "CRE",
    }
    return "  ".join(
        f"{abbr.get(k, k[:3].upper())}:[bold]{v:.1f}[/]"
        for k, v in sim.skills.levels.items()
    )


# ── Public print functions ────────────────────────────────────────────────────


def print_sim_profile(sim) -> None:
    o = sim.profile["ocean"]
    if not _RICH:
        _fallback_sim_profile(sim)
        return

    t = Text()
    t.append(f"  {sim.name}", style="bold bright_white")
    t.append(
        f"  |  {sim.profile.get('age', '?')}yo {sim.profile['gender']}"
        f"  |  {sim.profile['job']}  |  §{sim.simoleons:.0f}\n"
    )
    t.append(f"  Aspiration : {sim.profile['aspiration']}\n", style="dim")
    t.append(f"  Traits     : {', '.join(sim.profile['traits'])}\n")
    t.append(
        f"  OCEAN      : O={o['openness']:.2f}  C={o['conscientiousness']:.2f}"
        f"  E={o['extraversion']:.2f}  A={o['agreeableness']:.2f}"
        f"  N={o['neuroticism']:.2f}\n"
    )
    t.append(
        f"  Summary    : {sim.profile.get('self_summary', '')}", style="italic dim"
    )
    _console.print(Panel(t, border_style="dim"))


def print_tick_header(engine: "SimEngine") -> None:
    tick = engine.tick_count
    hour = (GAME_START_HOUR + tick) % 24
    time_label = f"{hour:02d}:00"
    pending = len(engine._pending)
    venue = engine._venue.get("name", "")

    from world.schedule import time_label as tl

    period = tl(hour)

    pending_str = f"  [yellow]⏳ {pending} pending[/]" if pending else ""
    header = (
        f"[bold bright_cyan]TICK {tick:03d}[/]"
        f"  [dim]|[/]  {time_label} [dim]{period}[/]"
        f"  [dim]|[/]  [italic]{venue}[/]"
        f"{pending_str}"
    )
    if _RICH:
        _console.rule(header)
    else:
        print(f"\n{'═' * 62}\n  TICK {tick:03d} | {time_label} | {venue}")
        print("═" * 62)


def print_active_sims(engine: "SimEngine") -> None:
    from sim_types.enums import LODTier

    sims = engine.sims

    if not _RICH:
        _fallback_active_sims(engine)
        return

    tbl = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold dim",
        expand=False,
        padding=(0, 1),
    )
    tbl.add_column("Sim", style="bold", min_width=16)
    tbl.add_column("§", justify="right", min_width=6)
    tbl.add_column("Perf", justify="right", min_width=4)
    tbl.add_column("LOD", min_width=4)
    tbl.add_column("Emotion", min_width=16)
    tbl.add_column("HUN", min_width=4)
    tbl.add_column("ENE", min_width=4)
    tbl.add_column("SOC", min_width=4)
    tbl.add_column("FUN", min_width=4)
    tbl.add_column("Arc / Goal", min_width=14)
    tbl.add_column("Wants", min_width=22)

    for sim in sims:
        emo = sim.emotion.dominant
        emo_col = _EMOTION_COLOUR.get(emo, "white")
        emo_emoji = _EMOTION_EMOJI.get(emo, "😐")
        lod_col = (
            "bright_green"
            if sim.lod_tier == LODTier.ACTIVE
            else ("yellow" if sim.lod_tier.name == "BACKGROUND" else "dim")
        )
        wants_str = (
            ", ".join(f"'{w.description[:18]}'" for w in sim.active_wants[:2]) or "—"
        )

        tbl.add_row(
            sim.name,
            f"§{sim.simoleons:,.0f}",
            f"{sim.career_performance:.0f}",
            f"[{lod_col}]{sim.lod_tier.name[:3]}[/]",
            f"[{emo_col}]{emo_emoji} {emo}[/]",
            _need_bar(sim.needs.hunger),
            _need_bar(sim.needs.energy),
            _need_bar(sim.needs.social),
            _need_bar(sim.needs.fun),
            _arc_badge(sim),
            f"[dim]{wants_str}[/]",
        )

    _console.print(tbl)


def print_summary(engine: "SimEngine") -> None:
    if not _RICH:
        _fallback_summary(engine)
        return

    _console.rule("[bold bright_white]SIMULATION SUMMARY[/]")

    # Sim summary table
    tbl = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        title="Final Sim States",
        expand=False,
        padding=(0, 1),
    )
    tbl.add_column("Sim", style="bold", min_width=16)
    tbl.add_column("Job", min_width=12)
    tbl.add_column("§", justify="right", min_width=7)
    tbl.add_column("Career", justify="right", min_width=6)
    tbl.add_column("Emotion", min_width=14)
    tbl.add_column("Skills", min_width=36)
    tbl.add_column("Fears", min_width=20)

    for sim in engine.sims:
        emo = sim.emotion.dominant
        emo_col = _EMOTION_COLOUR.get(emo, "white")
        fears_str = ", ".join(f.label[:16] for f in sim.fears[:2]) or "—"
        tbl.add_row(
            sim.name,
            sim.profile["job"],
            f"§{sim.simoleons:,.0f}",
            f"{sim.career_performance:.0f}",
            f"[{emo_col}]{_EMOTION_EMOJI.get(emo, '😐')} {emo}[/]",
            _skills_abbr(sim),
            f"[dim]{fears_str}[/]",
        )
    _console.print(tbl)

    # Relationships
    seen: set[frozenset] = set()
    rel_rows = []
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
            rel_rows.append((a, b, rel))

    if rel_rows:
        rtbl = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
            title="Relationships",
            expand=False,
            padding=(0, 1),
        )
        rtbl.add_column("Pair", min_width=28)
        rtbl.add_column("Friendship", justify="right", min_width=12)
        rtbl.add_column("Romance", justify="right", min_width=12)
        rtbl.add_column("State", min_width=14)
        rtbl.add_column("Interactions", justify="right", min_width=4)
        rtbl.add_column("Last memory", min_width=30)

        for a, b, rel in rel_rows:
            f_col = (
                "bright_green"
                if rel.friendship > 50
                else ("bright_red" if rel.friendship < -20 else "white")
            )
            r_col = "bright_magenta" if rel.romance > 30 else "dim"
            last = rel.memories[-1]["tag"][:30] if rel.memories else "—"
            rtbl.add_row(
                f"[bold]{a.name}[/] ↔ [bold]{b.name}[/]",
                f"[{f_col}]{rel.friendship:.0f}[/]",
                f"[{r_col}]{rel.romance:.0f}[/]",
                rel.state_label(),
                str(rel.interactions),
                f"[dim]{last}[/]",
            )
        _console.print(rtbl)

    _console.print()


# ── Event callbacks ───────────────────────────────────────────────────────────


def _on_interaction_resolved(engine: "SimEngine", **kw) -> None:
    sim_a = kw["sim_a"]
    sim_b = kw["sim_b"]
    result = kw["result"]
    valence = kw["valence"]
    tick = kw["tick"]
    iid = kw.get("interaction_id", "")
    action = kw.get("interaction", "")

    rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
    fd = float(result.get("friendship_delta", 0))
    rd = float(result.get("romance_delta", 0))
    reaction = result.get("sim_b_reaction", "")
    memory = result.get("memory_tag", "")
    reasoning = result.get("reasoning", "")
    emo_a = result.get("emotion_a", sim_a.emotion.dominant)
    emo_b = result.get("emotion_b", sim_b.emotion.dominant)

    v_col = _valence_colour(valence)

    if not _RICH:
        _fallback_resolved(sim_a, sim_b, result, valence, tick, iid, rel, action)
        return

    t = Text()
    t.append(f"✅ ", style="bold bright_green")
    t.append(f"[tick {tick}]", style="dim")
    if iid:
        t.append(f" #{iid[:8]}", style="dim")
    t.append(f"  {sim_a.name}", style="bold bright_white")
    t.append(" → ")
    t.append(f"{sim_b.name}", style="bold bright_white")
    if action:
        t.append(f"  [{action}]", style="italic cyan")
    t.append("\n")

    if reaction:
        t.append(f'  {sim_b.name}: "', style="dim")
        t.append(reaction, style="italic")
        t.append('"\n', style="dim")

    # deltas line
    f_col = "bright_green" if fd > 0 else ("bright_red" if fd < 0 else "dim")
    r_col = "bright_magenta" if rd != 0 else "dim"
    t.append(f"  F:", style="dim")
    t.append(f"{fd:+.1f}", style=f_col)
    t.append(f"→{rel.friendship:.0f}", style="dim")
    t.append("  R:", style="dim")
    t.append(f"{rd:+.1f}", style=r_col)
    t.append(f"→{rel.romance:.0f}", style="dim")
    t.append("  Val:")
    t.append(f"{valence:+.2f}", style=v_col)
    t.append(f"  [{rel.state_label()}]\n", style="dim")

    # emotions
    ea_col = _EMOTION_COLOUR.get(emo_a, "white")
    eb_col = _EMOTION_COLOUR.get(emo_b, "white")
    t.append(f"  Emotions: {sim_a.name}=", style="dim")
    t.append(f"{emo_a}", style=ea_col)
    t.append(f"  {sim_b.name}=", style="dim")
    t.append(f"{emo_b}\n", style=eb_col)

    if memory:
        t.append(f'  Memory: "{memory}"\n', style="dim italic")
    if reasoning:
        t.append(f"  Reason: {reasoning}", style="dim")

    _console.print(Panel(t, border_style=v_col, padding=(0, 1)))


def _on_interaction_queued(engine: "SimEngine", **kw) -> None:
    sim_a = kw["sim_a"]
    sim_b = kw["sim_b"]
    action = kw["interaction"]
    iid = kw.get("interaction_id", "")
    rel = engine.relationships.get(sim_a.sim_id, sim_b.sim_id)
    if _RICH:
        _console.print(
            f"  [bold yellow]⚡[/] [dim]#{iid[:8]}[/]  "
            f"[bold]{sim_a.name}[/] → [bold]{sim_b.name}[/]  "
            f"[italic cyan][{action}][/]  "
            f"[dim]{rel.state_label()} (F={rel.friendship:.0f}) — LLM…[/]"
        )
    else:
        print(f"\n  ⚡ QUEUED #{iid} {sim_a.name} → {sim_b.name} [{action}]")


def _on_career_event(engine: "SimEngine", **kw) -> None:
    sim = kw["sim"]
    result = kw["result"]
    delta = result.get("performance_delta", 0)
    col = "bright_green" if delta >= 0 else "bright_red"
    if _RICH:
        _console.print(
            f"\n  [bold]💼 CAREER[/] — [bold]{sim.name}[/]"
            f" ({result.get('event_type', '?')})\n"
            f"  {result.get('narrative', '')}\n"
            f"  Perf [{col}]{delta:+.0f}[/] → {sim.career_performance:.0f}"
            f"  |  §{result.get('simoleon_delta', 0):+.0f}"
        )
    else:
        print(f"\n  💼 CAREER — {sim.name}: {result.get('narrative', '')}")


def _on_life_event(engine: "SimEngine", **kw) -> None:
    sim_a = kw["sim_a"]
    result = kw["result"]
    etype = result.get("event_type", "life event")
    if _RICH:
        _console.print(
            f"\n  [bold bright_magenta]🌟 LIFE EVENT[/] [{etype}] — [bold]{sim_a.name}[/]\n"
            f"  [italic]{result.get('narrative', '')[:160]}[/]"
        )
    else:
        print(f"\n  🌟 LIFE EVENT [{etype}] — {sim_a.name}")


def _on_child_born(engine: "SimEngine", **kw) -> None:
    child = kw["child"]
    parent_a = kw["parent_a"]
    parent_b = kw["parent_b"]
    if _RICH:
        _console.print(
            f"\n  [bold bright_yellow]👶 NEW SIM[/] — [bold]{child.name}[/]"
            f" born to {parent_a.name} & {parent_b.name}\n"
            f"  Traits: {', '.join(child.profile['traits'])}"
            f"  |  Aspiration: {child.profile['aspiration']}"
        )
    else:
        print(f"\n  👶 {child.name} born to {parent_a.name} & {parent_b.name}")


def attach(engine: "SimEngine") -> None:
    engine._bus.on(
        "interaction_queued", lambda **kw: _on_interaction_queued(engine, **kw)
    )
    engine._bus.on(
        "interaction_resolved", lambda **kw: _on_interaction_resolved(engine, **kw)
    )
    engine._bus.on("career_event", lambda **kw: _on_career_event(engine, **kw))
    engine._bus.on("life_event", lambda **kw: _on_life_event(engine, **kw))
    engine._bus.on("child_born", lambda **kw: _on_child_born(engine, **kw))


# ── Plain-text fallbacks (if rich not installed) ──────────────────────────────


def _fallback_sim_profile(sim) -> None:
    o = sim.profile["ocean"]
    print(
        f"\n  {'─' * 56}\n  {sim.name}  |  {sim.profile.get('age', '?')}yo"
        f"  |  {sim.profile['job']}  |  §{sim.simoleons:.0f}\n"
        f"  OCEAN: O={o['openness']} C={o['conscientiousness']}"
        f" E={o['extraversion']} A={o['agreeableness']} N={o['neuroticism']}\n"
        f"  {'─' * 56}"
    )


def _fallback_active_sims(engine: "SimEngine") -> None:
    for sim in engine.sims:
        print(
            f"\n  [{sim.name}]  §{sim.simoleons:.0f}  perf:{sim.career_performance:.0f}"
        )
        wants = "  ".join(f"'{w.description}'" for w in sim.active_wants[:2]) or "none"
        print(f"    Emotion: {sim.emotion.dominant}  |  Wants: {wants}")


def _fallback_resolved(sim_a, sim_b, result, valence, tick, iid, rel, action) -> None:
    print(
        f"\n  ✅ RESOLVED [tick {tick}] #{iid[:8]}  {sim_a.name} → {sim_b.name}  [{action}]"
    )
    print(
        f"     F:{result.get('friendship_delta', 0):+.1f}→{rel.friendship:.0f}"
        f"  R:{result.get('romance_delta', 0):+.1f}→{rel.romance:.0f}"
        f"  Val:{valence:+.2f}"
    )
    if result.get("sim_b_reaction"):
        print(f'     {sim_b.name}: "{result["sim_b_reaction"]}"')


def _fallback_summary(engine: "SimEngine") -> None:
    print(f"\n{'═' * 62}\n  SIMULATION SUMMARY\n{'═' * 62}")
    for sim in engine.sims:
        print(
            f"\n  {sim.name}  |  §{sim.simoleons:.0f}  |  perf:{sim.career_performance:.0f}"
        )
