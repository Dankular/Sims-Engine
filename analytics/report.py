"""
analytics/report.py — Post-run matplotlib analytics report.

Call generate(tracker, output_dir) after the simulation ends.
Produces a folder of PNGs + a summary JSON covering:
  1. relationship_trajectories.png  — friendship/romance over ticks
  2. ocean_drift.png                — personality change (baseline → final)
  3. valence_distribution.png       — histogram of interaction outcomes
  4. emotion_timeline.png           — per-sim emotion heatmap over time
  5. arc_timeline.png               — arc activations (grief/burnout/lonely)
  6. simoleon_trajectory.png        — wealth curves per sim
"""
from __future__ import annotations

import json
import warnings
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analytics.tracker import SimTracker

warnings.filterwarnings("ignore", category=UserWarning)

# Colour palette
_OCEAN_COLOURS = {
    "openness":          "#5B9BD5",
    "conscientiousness": "#ED7D31",
    "extraversion":      "#A9D18E",
    "agreeableness":     "#FFC000",
    "neuroticism":       "#FF6B6B",
}
_ARC_COLOURS = {0: "#FFFFFF", 1: "#B8D4E8", 2: "#FFCC80", 3: "#CF9FBF"}
_ARC_LABELS  = {0: "none", 1: "lonely", 2: "burnout", 3: "grief"}

_EMOTION_CMAP = "RdYlGn"   # red=negative, green=positive


def generate(tracker: "SimTracker", output_dir: str | Path = "reports") -> Path:
    """Generate all charts and return the output directory path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(output_dir) / ts
    out.mkdir(parents=True, exist_ok=True)

    ticks = tracker.ticks
    if not ticks:
        return out

    n_ticks = len(ticks)
    sim_names = sorted(tracker.emotions.keys())

    # ── 1. Relationship Trajectories ─────────────────────────────────────────
    pairs = list(tracker.friendship.keys())
    if pairs:
        fig, axes = plt.subplots(len(pairs), 1,
                                 figsize=(12, 3 * max(len(pairs), 1)),
                                 squeeze=False)
        fig.suptitle("Relationship Trajectories", fontsize=14, fontweight="bold")
        for idx, pair in enumerate(pairs):
            ax = axes[idx][0]
            f_vals = tracker.friendship[pair]
            r_vals = tracker.romance[pair]
            t = ticks[:len(f_vals)]
            ax.plot(t, f_vals, color="#4472C4", label="Friendship", linewidth=2)
            ax.plot(t, r_vals, color="#ED7D31", label="Romance",    linewidth=2)
            ax.axhline(0, color="#888", linewidth=0.5, linestyle="--")
            ax.set_title(f"{pair[0]} ↔ {pair[1]}", fontsize=11)
            ax.set_ylabel("Score")
            ax.set_ylim(-100, 100)
            ax.legend(fontsize=8)
            ax.grid(True, alpha=0.3)
            # annotate final values
            if f_vals:
                ax.annotate(f"F={f_vals[-1]:.0f}", xy=(t[-1], f_vals[-1]),
                            fontsize=8, color="#4472C4",
                            xytext=(3, 3), textcoords="offset points")
            if r_vals:
                ax.annotate(f"R={r_vals[-1]:.0f}", xy=(t[-1], r_vals[-1]),
                            fontsize=8, color="#ED7D31",
                            xytext=(3, -10), textcoords="offset points")
        fig.tight_layout()
        fig.savefig(out / "relationship_trajectories.png", dpi=120)
        plt.close(fig)

    # ── 2. OCEAN Drift ────────────────────────────────────────────────────────
    traits = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
    if tracker.ocean_baseline and tracker.ocean_history:
        fig, axes = plt.subplots(1, len(sim_names),
                                 figsize=(5 * len(sim_names), 5),
                                 squeeze=False)
        fig.suptitle("OCEAN Personality Drift  (baseline → final)", fontsize=14, fontweight="bold")
        for idx, name in enumerate(sim_names):
            ax = axes[0][idx]
            baseline = tracker.ocean_baseline.get(name, {})
            history  = tracker.ocean_history.get(name, [])
            final    = history[-1] if history else baseline
            x = range(len(traits))
            b_vals = [baseline.get(t, 0) for t in traits]
            f_vals = [final.get(t, 0)    for t in traits]
            width = 0.35
            bars_b = ax.bar([i - width/2 for i in x], b_vals, width,
                            label="Baseline", color="#B8CCE4", edgecolor="white")
            bars_f = ax.bar([i + width/2 for i in x], f_vals, width,
                            label="Final",    color="#4472C4", edgecolor="white")
            # drift arrows
            for i, (bv, fv) in enumerate(zip(b_vals, f_vals)):
                delta = fv - bv
                if abs(delta) > 0.005:
                    colour = "#C00000" if delta < 0 else "#00B050"
                    ax.annotate(f"{delta:+.3f}",
                                xy=(i + width/2, max(bv, fv) + 0.02),
                                fontsize=7, ha="center", color=colour, fontweight="bold")
            ax.set_xticks(list(x))
            ax.set_xticklabels([t[:4].upper() for t in traits], fontsize=8)
            ax.set_ylim(0, 1.15)
            ax.set_title(name, fontsize=11)
            ax.legend(fontsize=7)
            ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(out / "ocean_drift.png", dpi=120)
        plt.close(fig)

    # ── 3. Valence Distribution ───────────────────────────────────────────────
    valences = [i["valence"] for i in tracker.interactions]
    if valences:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.hist(valences, bins=20, range=(-1, 1),
                color="#4472C4", edgecolor="white", alpha=0.85)
        ax.axvline(0, color="#C00000", linewidth=1.5, linestyle="--", label="neutral")
        mean_v = sum(valences) / len(valences)
        ax.axvline(mean_v, color="#00B050", linewidth=1.5, linestyle="-",
                   label=f"mean={mean_v:+.2f}")
        ax.set_xlabel("Interaction Valence")
        ax.set_ylabel("Count")
        ax.set_title(f"Interaction Valence Distribution  ({len(valences)} interactions)",
                     fontsize=13, fontweight="bold")
        ax.legend()
        ax.grid(True, axis="y", alpha=0.3)
        # colour-coded bars
        for patch in ax.patches:
            x_center = patch.get_x() + patch.get_width() / 2
            patch.set_facecolor("#C00000" if x_center < 0 else
                                "#FFC000" if x_center < 0.3 else "#00B050")
            patch.set_alpha(0.75)
        fig.tight_layout()
        fig.savefig(out / "valence_distribution.png", dpi=120)
        plt.close(fig)

    # ── 4. Emotion Timeline Heatmap ───────────────────────────────────────────
    if sim_names and ticks:
        emo_matrix = np.array([
            tracker.emotions.get(name, [0.5] * n_ticks)[:n_ticks]
            for name in sim_names
        ])
        fig, ax = plt.subplots(figsize=(max(12, n_ticks * 0.4), len(sim_names) * 0.9 + 1.5))
        im = ax.imshow(emo_matrix, aspect="auto", cmap=_EMOTION_CMAP,
                       vmin=0, vmax=1, interpolation="nearest")
        ax.set_yticks(range(len(sim_names)))
        ax.set_yticklabels(sim_names, fontsize=10)
        ax.set_xlabel("Tick")
        ax.set_title("Emotion Timeline  (green=positive, red=negative)", fontsize=13, fontweight="bold")
        # tick labels every 5
        tick_step = max(1, n_ticks // 20)
        ax.set_xticks(range(0, n_ticks, tick_step))
        ax.set_xticklabels(ticks[::tick_step], fontsize=7, rotation=45)
        plt.colorbar(im, ax=ax, label="Valence (0=neg, 1=pos)", fraction=0.015)
        fig.tight_layout()
        fig.savefig(out / "emotion_timeline.png", dpi=120)
        plt.close(fig)

    # ── 5. Arc Activation Timeline ────────────────────────────────────────────
    if sim_names and ticks:
        arc_matrix = np.array([
            tracker.arc_states.get(name, [0] * n_ticks)[:n_ticks]
            for name in sim_names
        ])
        fig, ax = plt.subplots(figsize=(max(12, n_ticks * 0.4), len(sim_names) * 0.9 + 2))
        cmap = matplotlib.colors.ListedColormap(
            [_ARC_COLOURS[k] for k in sorted(_ARC_COLOURS)]
        )
        im = ax.imshow(arc_matrix, aspect="auto", cmap=cmap,
                       vmin=-0.5, vmax=3.5, interpolation="nearest")
        ax.set_yticks(range(len(sim_names)))
        ax.set_yticklabels(sim_names, fontsize=10)
        ax.set_xlabel("Tick")
        ax.set_title("Arc Activation Timeline", fontsize=13, fontweight="bold")
        tick_step = max(1, n_ticks // 20)
        ax.set_xticks(range(0, n_ticks, tick_step))
        ax.set_xticklabels(ticks[::tick_step], fontsize=7, rotation=45)
        legend_patches = [
            mpatches.Patch(color=_ARC_COLOURS[k], label=_ARC_LABELS[k])
            for k in sorted(_ARC_COLOURS)
        ]
        ax.legend(handles=legend_patches, loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(out / "arc_timeline.png", dpi=120)
        plt.close(fig)

    # ── 6. Simoleon Trajectories ──────────────────────────────────────────────
    if tracker.simoleons:
        fig, ax = plt.subplots(figsize=(12, 4))
        colours = plt.cm.tab10.colors
        for i, name in enumerate(sim_names):
            vals = tracker.simoleons.get(name, [])
            t = ticks[:len(vals)]
            ax.plot(t, vals, label=name, linewidth=2, color=colours[i % len(colours)])
        ax.set_xlabel("Tick")
        ax.set_ylabel("§ Simoleons")
        ax.set_title("Simoleon Trajectories", fontsize=13, fontweight="bold")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(out / "simoleon_trajectory.png", dpi=120)
        plt.close(fig)

    # ── Summary JSON ──────────────────────────────────────────────────────────
    summary = {
        "run_timestamp": ts,
        "ticks_simulated": len(ticks),
        "total_interactions": len(tracker.interactions),
        "total_life_events":  len(tracker.life_events),
        "total_career_events": len(tracker.career_events),
        "mean_valence": round(sum(valences) / len(valences), 3) if valences else None,
        "positive_interactions": sum(1 for v in valences if v > 0.1),
        "negative_interactions": sum(1 for v in valences if v < -0.1),
        "sims": [
            {
                "name": name,
                "ocean_baseline": tracker.ocean_baseline.get(name, {}),
                "ocean_final": (tracker.ocean_history.get(name, [{}])[-1]),
                "final_simoleons": round(tracker.simoleons.get(name, [0])[-1], 1) if tracker.simoleons.get(name) else 0,
                "arc_activations": {
                    "lonely":  tracker.arc_states.get(name, []).count(1),
                    "burnout": tracker.arc_states.get(name, []).count(2),
                    "grief":   tracker.arc_states.get(name, []).count(3),
                },
            }
            for name in sim_names
        ],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    return out
