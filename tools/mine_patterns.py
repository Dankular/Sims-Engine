"""
tools/mine_patterns.py - Behavioral pattern miner for observation logs.

Reads a JSONL interaction log produced by observation_run.py and extracts:

  1. Action outcome stats       - per action: count, mean/std valence, mean deltas
  2. Category performance       - per category: ranking by mean valence
  3. Dead actions               - catalog entries never chosen in this run
  4. Emergent chains            - 2/3-gram action sequences with their outcomes
  5. Stage transition analysis  - what triggered each stage change, dwell times
  6. Personality correlations   - OCEAN bucket -> category affinity
  7. Dataset seed performance   - which seed types were chosen and how they did
  8. Consent event analysis     - context around withdrawn/given signals
  9. Improvement suggestions    - concrete, actionable list

Usage
-----
  python tools/mine_patterns.py reports/run_001.jsonl
  python tools/mine_patterns.py reports/run_001.jsonl --out reports/catalog_001.json
  python tools/mine_patterns.py reports/run_001.jsonl --text     # human-readable only
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# -- Helpers -------------------------------------------------------------------

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0

def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals))

def _bucket_ocean(v: float) -> str:
    if v < 0.35:
        return "low"
    if v < 0.65:
        return "mid"
    return "high"


# -- Loader --------------------------------------------------------------------

def load_records(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


# -- Analysis functions --------------------------------------------------------

def action_stats(records: list[dict]) -> dict[str, dict]:
    # Always re-infer category from the action string so improved keyword rules
    # apply retroactively to existing logs.
    try:
        from engine.observer import _infer_category as _cat
    except Exception:
        def _cat(x: str) -> str:
            return "unknown"

    by_action: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        key = r["interaction"][:80]
        by_action[key].append(r)
    out = {}
    for action, rs in by_action.items():
        valences = [r["valence"] for r in rs]
        fds = [r["friendship_delta"] for r in rs]
        rds = [r["romance_delta"] for r in rs]
        out[action] = {
            "count": len(rs),
            "category": _cat(action),           # re-infer — always up-to-date
            "seed_type": rs[0].get("seed_type", "catalog"),
            "mean_valence": round(_mean(valences), 3),
            "std_valence": round(_std(valences), 3),
            "mean_friendship_delta": round(_mean(fds), 3),
            "mean_romance_delta": round(_mean(rds), 3),
        }
    return dict(sorted(out.items(), key=lambda x: x[1]["count"], reverse=True))


def category_performance(action_stats: dict[str, dict]) -> list[dict]:
    by_cat: dict[str, list[float]] = defaultdict(list)
    by_cat_count: dict[str, int] = defaultdict(int)
    for action, s in action_stats.items():
        cat = s["category"]
        # Weight each action's mean valence by its count
        by_cat[cat].extend([s["mean_valence"]] * s["count"])
        by_cat_count[cat] += s["count"]
    rows = []
    for cat, vals in by_cat.items():
        rows.append({
            "category": cat,
            "mean_valence": round(_mean(vals), 3),
            "std_valence": round(_std(vals), 3),
            "total_count": by_cat_count[cat],
            "unique_actions": sum(1 for s in action_stats.values() if s["category"] == cat),
        })
    return sorted(rows, key=lambda x: x["mean_valence"], reverse=True)


def dead_actions(action_stats: dict[str, dict]) -> list[dict]:
    try:
        from config import INTERACTION_TYPES
    except ImportError:
        return []
    seen = {a.lower() for a in action_stats}
    dead = []
    for cat, actions in INTERACTION_TYPES.items():
        for action in actions:
            if action.lower() not in seen:
                dead.append({"action": action, "category": cat})
    return dead


def emergent_chains(records: list[dict], min_count: int = 2) -> list[dict]:
    """Find action N-grams (2 and 3) per sim pair, ranked by frequency x mean valence."""
    # Build per-pair sequences sorted by tick
    pair_seqs: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        key = tuple(sorted([r["sim_a"]["id"], r["sim_b"]["id"]]))
        pair_seqs[key].append(r)
    for seq in pair_seqs.values():
        seq.sort(key=lambda x: x["tick"])

    bigrams: dict[tuple, list[float]] = defaultdict(list)
    trigrams: dict[tuple, list[float]] = defaultdict(list)

    for seq in pair_seqs.values():
        actions = [r["interaction"][:60] for r in seq]
        valences = [r["valence"] for r in seq]
        for i in range(len(actions) - 1):
            bigrams[(actions[i], actions[i+1])].append(valences[i+1])
        for i in range(len(actions) - 2):
            trigrams[(actions[i], actions[i+1], actions[i+2])].append(valences[i+2])

    def _score(vals: list[float]) -> float:
        return len(vals) * _mean(vals)

    chains = []
    for gram, vals in list(bigrams.items()) + list(trigrams.items()):
        if len(vals) >= min_count:
            chains.append({
                "chain": list(gram),
                "count": len(vals),
                "mean_valence": round(_mean(vals), 3),
                "score": round(_score(vals), 3),
            })
    return sorted(chains, key=lambda x: x["score"], reverse=True)[:40]


def stage_transitions(records: list[dict]) -> dict:
    transitions: dict[str, list[dict]] = defaultdict(list)
    dwell: dict[str, list[int]] = defaultdict(list)

    # Track dwell times per pair per stage
    pair_stage_entry: dict[tuple, dict[str, int]] = defaultdict(dict)

    for r in records:
        pair = tuple(sorted([r["sim_a"]["id"], r["sim_b"]["id"]]))
        sb = r.get("stage_before", "small_talk")
        sa = r.get("stage_after", "small_talk")
        tick = r["tick"]

        if r.get("stage_changed"):
            key = f"{sb}->{sa}"
            transitions[key].append({
                "trigger": r["interaction"][:60],
                "valence": r["valence"],
                "friendship": r["sim_a"]["friendship"],
                "romance": r["sim_a"]["romance"],
            })

        # Dwell: time from entry to next change
        if pair not in pair_stage_entry or sb not in pair_stage_entry[pair]:
            pair_stage_entry[pair][sb] = tick
        if r.get("stage_changed"):
            entry_tick = pair_stage_entry[pair].get(sb, tick)
            dwell[sb].append(tick - entry_tick)
            pair_stage_entry[pair][sa] = tick

    out = {}
    for key, evts in transitions.items():
        valences = [e["valence"] for e in evts]
        trigger_counts = Counter(e["trigger"] for e in evts)
        out[key] = {
            "count": len(evts),
            "mean_valence": round(_mean(valences), 3),
            "top_triggers": [{"action": a, "count": c}
                             for a, c in trigger_counts.most_common(3)],
        }

    dwell_stats = {
        stage: {
            "mean_turns": round(_mean(vals), 1),
            "min_turns": min(vals),
            "max_turns": max(vals),
        }
        for stage, vals in dwell.items()
        if vals
    }
    return {"transitions": out, "dwell_stats": dwell_stats}


def personality_correlations(records: list[dict]) -> dict:
    """Map OCEAN buckets -> category -> mean valence."""
    dims = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
    # bucket_dim_cat -> [valences]
    data: dict[tuple, list[float]] = defaultdict(list)
    for r in records:
        cat = r.get("category", "unknown")
        ocean = r["sim_a"].get("ocean", {})
        for dim in dims:
            bucket = _bucket_ocean(float(ocean.get(dim, 0.5)))
            data[(dim, bucket, cat)].append(r["valence"])

    # For each dim x bucket, find top and bottom categories
    out: dict[str, dict] = {}
    for dim in dims:
        dim_data: dict[str, dict[str, list[float]]] = {
            "low": defaultdict(list),
            "mid": defaultdict(list),
            "high": defaultdict(list),
        }
        for (d, bucket, cat), vals in data.items():
            if d == dim:
                dim_data[bucket][cat].extend(vals)
        dim_out: dict[str, Any] = {}
        for bucket, cats in dim_data.items():
            if not cats:
                continue
            ranked = sorted(
                ((cat, round(_mean(vs), 3), len(vs)) for cat, vs in cats.items() if vs),
                key=lambda x: x[1],
                reverse=True,
            )
            dim_out[bucket] = [
                {"category": c, "mean_valence": mv, "count": n}
                for c, mv, n in ranked[:4]
            ]
        out[dim] = dim_out
    return out


def seed_performance(records: list[dict]) -> list[dict]:
    by_seed: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_seed[r.get("seed_type", "catalog")].append(r["valence"])
    rows = []
    for seed, vals in by_seed.items():
        rows.append({
            "seed_type": seed,
            "count": len(vals),
            "mean_valence": round(_mean(vals), 3),
            "std_valence": round(_std(vals), 3),
        })
    return sorted(rows, key=lambda x: x["count"], reverse=True)


def consent_analysis(records: list[dict]) -> dict:
    withdrawn = [r for r in records if r.get("consent") == "withdrawn"]
    given = [r for r in records if r.get("consent") == "given"]
    return {
        "given_count": len(given),
        "withdrawn_count": len(withdrawn),
        "given_mean_valence": round(_mean([r["valence"] for r in given]), 3) if given else None,
        "withdrawn_mean_valence": round(_mean([r["valence"] for r in withdrawn]), 3) if withdrawn else None,
        "withdrawn_triggers": [r["interaction"][:60] for r in withdrawn[:10]],
        "stage_at_withdrawal": Counter(r.get("stage_before") for r in withdrawn).most_common(4),
    }


def improvement_suggestions(
    astats: dict,
    dead: list[dict],
    chains: list[dict],
    stage_info: dict,
    cat_perf: list[dict],
) -> list[dict]:
    suggestions = []

    # Dead actions
    for d in dead[:15]:
        suggestions.append({
            "type": "dead_action",
            "priority": "medium",
            "action": d["action"],
            "category": d["category"],
            "suggestion": (
                f"'{d['action']}' was never chosen. "
                "Consider: (a) tighten gating so it surfaces only when highly relevant, "
                "or (b) add an explicit weight boost in _apply_stage_weights for the right stage."
            ),
        })

    # Underperformers
    for action, s in astats.items():
        if s["count"] >= 3 and s["mean_valence"] < 0.25 and s["category"] not in ("mean", "toxic"):
            suggestions.append({
                "type": "underperformer",
                "priority": "high",
                "action": action[:60],
                "category": s["category"],
                "mean_valence": s["mean_valence"],
                "count": s["count"],
                "suggestion": (
                    f"Mean valence {s['mean_valence']:.2f} over {s['count']} uses. "
                    "Consider: tightening conditions (relationship threshold / trait gate), "
                    "or replacing with a stronger dataset seed."
                ),
            })

    # Stars - actions that consistently produce high outcomes
    for action, s in astats.items():
        if s["count"] >= 5 and s["mean_valence"] > 0.65:
            suggestions.append({
                "type": "star_action",
                "priority": "low",
                "action": action[:60],
                "category": s["category"],
                "mean_valence": s["mean_valence"],
                "count": s["count"],
                "suggestion": (
                    f"Mean valence {s['mean_valence']:.2f} over {s['count']} uses. "
                    "Consider boosting base weight in choose_interaction "
                    "or promoting to a stage-locked high-priority slot."
                ),
            })

    # Emergent chains
    for chain in chains[:8]:
        if chain["mean_valence"] > 0.55 and chain["count"] >= 3:
            suggestions.append({
                "type": "emergent_chain",
                "priority": "medium",
                "chain": chain["chain"],
                "count": chain["count"],
                "mean_valence": chain["mean_valence"],
                "suggestion": (
                    "This action sequence emerged naturally and produced good outcomes. "
                    "Consider adding to ACTION_CHAIN_BOOST config or as an explicit "
                    "multi-step goal for sims with the relevant aspiration."
                ),
            })

    # Stage dwell issues
    dwell = stage_info.get("dwell_stats", {})
    for stage, ds in dwell.items():
        if stage != "affectionate_intent" and ds.get("mean_turns", 0) > 5:
            suggestions.append({
                "type": "long_dwell",
                "priority": "medium",
                "stage": stage,
                "mean_turns": ds["mean_turns"],
                "suggestion": (
                    f"Sims spend avg {ds['mean_turns']:.1f} turns in '{stage}' before advancing. "
                    "Consider lowering the dwell threshold in _advance_conversation_stage, "
                    "or checking whether momentum thresholds are too conservative."
                ),
            })
        if ds.get("mean_turns", 99) < 1.2:
            suggestions.append({
                "type": "instant_skip",
                "priority": "low",
                "stage": stage,
                "mean_turns": ds["mean_turns"],
                "suggestion": (
                    f"Stage '{stage}' is being skipped after <1.2 turns avg. "
                    "Consider raising the dwell threshold so sims spend meaningful time here."
                ),
            })

    # Weak categories
    for row in cat_perf:
        if row["total_count"] >= 5 and row["mean_valence"] < 0.3 and row["category"] not in ("mean", "toxic"):
            suggestions.append({
                "type": "weak_category",
                "priority": "medium",
                "category": row["category"],
                "mean_valence": row["mean_valence"],
                "count": row["total_count"],
                "suggestion": (
                    f"Category '{row['category']}' mean valence={row['mean_valence']:.2f}. "
                    "Consider: reviewing gating conditions, adding stronger dataset seeds, "
                    "or revisiting the action strings in config.py."
                ),
            })

    return sorted(suggestions, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["priority"]])


# -- Report --------------------------------------------------------------------

def print_report(report: dict) -> None:
    r = report
    print(f"\n{'='*64}")
    print(f"  BEHAVIOR CATALOG  - {r['meta']['source']}")
    print(f"  {r['meta']['n_records']} interactions  -  "
          f"{r['meta']['n_sims']} sims  -  {r['meta']['n_ticks']} ticks")
    print(f"{'='*64}\n")

    print("CATEGORY PERFORMANCE (by mean valence)")
    print(f"  {'Category':<20} {'MeanV':>6}  {'Count':>6}")
    print(f"  {'-'*36}")
    for row in r["category_performance"]:
        bar = "#" * int(max(0, row["mean_valence"]) * 12)
        print(f"  {row['category']:<20} {row['mean_valence']:>+6.3f}  {row['total_count']:>6}  {bar}")

    print(f"\nTOP 15 ACTIONS (by count)")
    print(f"  {'Action':<45} {'Cat':<14} {'MeanV':>6} {'N':>5}")
    print(f"  {'-'*74}")
    for action, s in list(r["action_stats"].items())[:15]:
        short = action[:43]
        print(f"  {short:<45} {s['category']:<14} {s['mean_valence']:>+6.3f} {s['count']:>5}")

    print(f"\nDEAD ACTIONS ({len(r['dead_actions'])} never chosen)")
    for d in r["dead_actions"][:12]:
        print(f"  [{d['category']}]  {d['action']}")
    if len(r["dead_actions"]) > 12:
        print(f"  - and {len(r['dead_actions'])-12} more")

    print(f"\nEMERGENT CHAINS (top 10 by freqxvalence)")
    for chain in r["emergent_chains"][:10]:
        arrow = " -> ".join(a[:25] for a in chain["chain"])
        print(f"  [{chain['count']}x  v={chain['mean_valence']:+.3f}]  {arrow}")

    print(f"\nSTAGE TRANSITIONS")
    for trans, info in r["stage_analysis"]["transitions"].items():
        top_trig = info["top_triggers"][0]["action"][:40] if info["top_triggers"] else "?"
        print(f"  {trans:<35} {info['count']:>3}x  v={info['mean_valence']:+.3f}"
              f"  trigger: '{top_trig}'")

    print(f"\nDATASET SEED PERFORMANCE")
    for row in r["seed_performance"]:
        print(f"  {row['seed_type']:<25} v={row['mean_valence']:+.3f}  n={row['count']}")

    consent = r["consent_analysis"]
    print(f"\nCONSENT  given={consent['given_count']}  "
          f"withdrawn={consent['withdrawn_count']}")
    if consent["withdrawn_triggers"]:
        print("  Triggers for withdrawal:")
        for t in consent["withdrawn_triggers"][:4]:
            print(f"    {t[:60]}")

    print(f"\nIMPROVEMENT SUGGESTIONS ({len(r['improvement_suggestions'])})")
    for i, sug in enumerate(r["improvement_suggestions"][:15], 1):
        tag = sug["type"].upper()
        pri = sug["priority"].upper()
        subject = sug.get("action") or sug.get("category") or str(sug.get("chain", ""))
        if isinstance(subject, list):
            subject = " -> ".join(str(s)[:20] for s in subject)
        subject = str(subject)[:50]
        print(f"  {i:2d}. [{pri}] {tag}: {subject}")
        print(f"      {sug['suggestion'][:120]}")
    if len(r["improvement_suggestions"]) > 15:
        print(f"  - and {len(r['improvement_suggestions'])-15} more in the JSON report")

    print(f"\n{'='*64}\n")


# -- Main ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Mine patterns from observation log")
    parser.add_argument("log",      help="Path to JSONL observation log")
    parser.add_argument("--out",    default=None, help="Output JSON report path")
    parser.add_argument("--text",   action="store_true", help="Print report only, skip JSON")
    parser.add_argument("--chains-min", type=int, default=2, help="Min count for chain detection")
    args = parser.parse_args()

    print(f"Loading {args.log}...", end=" ", flush=True)
    records = load_records(args.log)
    print(f"{len(records)} records")

    if not records:
        print("No records found. Did the observation run produce any interactions?")
        sys.exit(1)

    n_sims = len({r["sim_a"]["id"] for r in records} | {r["sim_b"]["id"] for r in records})
    n_ticks = max((r["tick"] for r in records), default=0)

    print("Computing analyses...", end=" ", flush=True)
    astats = action_stats(records)
    cat_perf = category_performance(astats)
    dead = dead_actions(astats)
    chains = emergent_chains(records, min_count=args.chains_min)
    stage_info = stage_transitions(records)
    pers_corr = personality_correlations(records)
    seed_perf = seed_performance(records)
    consent = consent_analysis(records)
    suggestions = improvement_suggestions(astats, dead, chains, stage_info, cat_perf)
    print("done")

    report = {
        "meta": {
            "source": args.log,
            "n_records": len(records),
            "n_sims": n_sims,
            "n_ticks": n_ticks,
        },
        "category_performance": cat_perf,
        "action_stats": astats,
        "dead_actions": dead,
        "emergent_chains": chains,
        "stage_analysis": stage_info,
        "personality_correlations": pers_corr,
        "seed_performance": seed_perf,
        "consent_analysis": consent,
        "improvement_suggestions": suggestions,
    }

    print_report(report)

    if not args.text:
        out_path = args.out or args.log.replace(".jsonl", "_catalog.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Full report saved: {out_path}")


if __name__ == "__main__":
    main()
