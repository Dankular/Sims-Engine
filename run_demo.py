"""
run_demo.py — End-to-end world simulation demo.

Shows how sims interact with every major system: economy, bank, stocks,
contracts, blockchain, beliefs, pressure engine, institutions, collateral,
emergence analytics, and the ACID ledger.

Run:
    python run_demo.py
    python run_demo.py --beats 30 --sims 8
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Argument parsing ──────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Sims Engine world demo")
parser.add_argument("--beats",    type=int,   default=20,  help="Heartbeat beats to simulate")
parser.add_argument("--sims",     type=int,   default=6,   help="Number of sims")
parser.add_argument("--dt",       type=float, default=1800, help="Simulated seconds per beat (default 30 min)")
parser.add_argument("--quiet",    action="store_true",     help="Suppress per-event output")
args = parser.parse_args()

SIM_SECONDS_PER_BEAT = args.dt   # 30 real minutes per beat → 20 beats = 10 sim hours

# ── Engine bootstrap ──────────────────────────────────────────────────────────
print("\n" + "="*70)
print("  SIMS ENGINE — WORLD INTEGRATION DEMO")
print("="*70)
print(f"  Sims: {args.sims}  |  Beats: {args.beats}  |  {SIM_SECONDS_PER_BEAT/60:.0f} sim-min/beat")
print(f"  Total simulated time: {args.beats * SIM_SECONDS_PER_BEAT / 3600:.1f} hours")
print("="*70 + "\n")

from llm.mock_backend import MockLLMBackend
from engine.engine import SimEngine
from core.sim import Sim
from identity.profile_factory import generate_sim_profile

# ── Create sims with defined personalities ────────────────────────────────────
PROFILES = [
    # Name override, aspiration, OCEAN nudges, starting_funds
    ("Maya Rivera",    "Fortune",    {"openness": 0.85, "conscientiousness": 0.78}, 3500.0),
    ("Jake Torres",    "Popularity", {"extraversion": 0.92, "agreeableness": 0.80}, 1200.0),
    ("Dr. Chen Wei",   "Knowledge",  {"openness": 0.95, "conscientiousness": 0.90}, 5000.0),
    ("Lena Kovacs",    "Creativity", {"openness": 0.88, "neuroticism": 0.60},       800.0),
    ("Marcus Shaw",    "Athletics",  {"conscientiousness": 0.40, "extraversion": 0.75}, 400.0),
    ("Sofia Reyes",    "Romance",    {"agreeableness": 0.90, "neuroticism": 0.65},  2100.0),
    ("Darius Cole",    "Wealth",     {"conscientiousness": 0.30, "neuroticism": 0.70}, 150.0),
    ("Priya Sharma",   "Family",     {"agreeableness": 0.85, "openness": 0.72},     3800.0),
][:args.sims]

sims_list: list[Sim] = []
for i in range(args.sims):
    profile = generate_sim_profile()
    name, aspiration, ocean_nudges, starting_funds = PROFILES[i % len(PROFILES)]
    profile["name"]       = name
    profile["aspiration"] = aspiration
    profile["income"]     = random.choice(["low", "medium", "high"])
    profile["ocean"].update({k: max(0.05, min(0.95, v)) for k, v in ocean_nudges.items()})
    sim = Sim(profile)
    sim.simoleons = starting_funds
    sims_list.append(sim)

backend = MockLLMBackend()
eng = SimEngine(sims=sims_list, llm=backend, bg_llm=backend)

# ── Event listeners — capture everything ─────────────────────────────────────
event_log: list[dict] = []

def _log(evt_type: str, **kw):
    event_log.append({"type": evt_type, "ts": time.time(), **kw})

for evt in [
    "gig_completed", "item_crafted", "item_sold", "interaction_resolved",
    "milestone_achieved", "bank_deposit_matured", "collateral_posted",
    "collateral_released", "margin_call", "hard_consequence_imposed",
    "pressure_event", "rumor_event", "negotiation_accepted",
    "hr_sanction", "agreement_fulfilled", "loan_default",
    "heartbeat", "chain_block",
]:
    eng._bus.on(evt, lambda _t=evt, **kw: _log(_t, **kw))

# ── Seed initial state ────────────────────────────────────────────────────────
from persistence.ledger import (
    TX_SALARY, TX_BANK_DEPOSIT, TX_STOCK_PURCHASE, TX_SHOP_PURCHASE,
    TX_CONTRACT_LOAN_RECV
)
from config import BANK_TERMS

print("[ WORLD SETUP ]")
print("-"*50)

# Give each sim starting salary credit via ledger
for sim in sims_list:
    eng._tx(sim, sim.simoleons, TX_SALARY, description="starting funds")
    sim.simoleons = max(sim.simoleons, 0)  # already set, just register
    eng.bank.ensure_account(sim.sim_id)
    print(f"  {sim.name:<18} §{sim.simoleons:>8,.2f}  [{sim.profile['aspiration']:<12}]  "
          f"O:{sim.ocean['openness']:.2f} C:{sim.ocean['conscientiousness']:.2f} "
          f"E:{sim.ocean['extraversion']:.2f}")

print()

# Fortune/Wealth sims open bank deposits
depositors = [s for s in sims_list if s.profile.get("aspiration") in ("Fortune","Wealth","Knowledge")]
for sim in depositors:
    if sim.simoleons > 500:
        term  = random.choice(["1_week","2_weeks","1_month"])
        amount = round(sim.simoleons * random.uniform(0.2, 0.4), 2)
        try:
            dep = eng.bank.open_deposit(sim, term, amount, eng)
            print(f"  {sim.name} opened {BANK_TERMS[term]['label']} deposit: "
                  f"§{amount:,.2f} @ {BANK_TERMS[term]['apr']*100:.1f}% APR")
        except Exception:
            pass

# Give Dr. Chen Wei a retail business (they're wealthy + conscientiousness)
dr_chen = next((s for s in sims_list if "Chen" in s.name), None)
if dr_chen and dr_chen.simoleons > 2000:
    dr_chen.owned_businesses = ["retail"]
    dr_chen.simoleons -= 5200
    print(f"  {dr_chen.name} purchased a retail business (§5200)")

# Give Darius a loan (he's cash-poor with Fortune aspiration)
darius = next((s for s in sims_list if "Darius" in s.name), None)
if darius and len(sims_list) > 1:
    lender = next((s for s in sims_list if s is not darius and s.simoleons > 500), None)
    if lender:
        eng._tx(darius, 300.0, TX_CONTRACT_LOAN_RECV,
                counterpart=lender.sim_id, description="peer loan from "+lender.name)
        print(f"  {lender.name} loaned §300 to {darius.name}")

print()

# ── Main simulation loop ──────────────────────────────────────────────────────
print("[ SIMULATION RUNNING ]")
print("-"*50)

interaction_count  = 0
gig_completions    = 0
pressure_events    = 0
anomalies_flagged  = 0
bank_maturities    = 0
collateral_events  = 0

for beat in range(1, args.beats + 1):

    # One full heartbeat beat at the configured simulated dt
    now = time.time()
    eng.heartbeat._beat(SIM_SECONDS_PER_BEAT, now)

    # --- Stock price events from activity ---
    eng._stock_event("shop_purchase", 1.0)
    if beat % 3 == 0:
        eng._stock_event("high_social", random.uniform(0.8, 1.5))
    if beat % 5 == 0:
        eng._stock_event("property_purchase", 1.2)

    # --- Autonomous spending — sims visit shops proportional to needs ---
    for sim in sims_list:
        if sim.needs.hunger < 30 and sim.simoleons > 30:
            eng._tx(sim, -random.uniform(15, 35), TX_SHOP_PURCHASE,
                    counterpart="restaurant", description="lunch")
        if sim.needs.fun < 25 and sim.simoleons > 20:
            eng._tx(sim, -random.uniform(10, 25), TX_SHOP_PURCHASE,
                    counterpart="cinema", description="entertainment")

    # --- Count events this beat ---
    beat_events = [e for e in event_log if e.get("type") in
                   ("gig_completed","interaction_resolved","pressure_event",
                    "bank_deposit_matured","collateral_posted","margin_call")]

    # --- Progress output ---
    sim_hours = beat * SIM_SECONDS_PER_BEAT / 3600
    balances  = sorted(sims_list, key=lambda s: -s.simoleons)
    richest   = balances[0]
    poorest   = balances[-1]

    if not args.quiet:
        print(f"  Beat {beat:02d} | {sim_hours:5.1f} sim-hrs | "
              f"Richest: {richest.name[:14]:<14} §{richest.simoleons:>8,.2f} | "
              f"Poorest: {poorest.name[:14]:<14} §{poorest.simoleons:>7,.2f}")

# ── Final report ──────────────────────────────────────────────────────────────
print()
print("="*70)
print("  WORLD STATE REPORT")
print("="*70)

# 1. Sim wealth standings
print("\n[ WEALTH STANDINGS ]")
standings = sorted(sims_list, key=lambda s: -s.simoleons)
for rank, sim in enumerate(standings, 1):
    locked   = eng.bank.total_locked(sim.sim_id)
    stocks   = eng.stocks.portfolio(sim.sim_id).get("value", 0) if hasattr(eng, "stocks") else 0
    net      = sim.simoleons + locked + float(stocks)
    print(f"  #{rank} {sim.name:<18} "
          f"wallet=§{sim.simoleons:>8,.2f}  "
          f"bank=§{locked:>7,.2f}  "
          f"net=§{net:>8,.2f}")

# 2. Ledger breakdown for top earner
print(f"\n[ LEDGER TRACE — {standings[0].name} ]")
fl = eng.financial_ledger
income  = fl.income_breakdown(standings[0].sim_id)
expense = fl.expense_breakdown(standings[0].sim_id)
print(f"  Income by source:")
for tx_type, total in sorted(income.items(), key=lambda x: -x[1]):
    print(f"    {tx_type:<30} §{total:>10,.2f}")
print(f"  Expense by type:")
for tx_type, total in sorted(expense.items(), key=lambda x: -x[1]):
    print(f"    {tx_type:<30} §{total:>10,.2f}")
velocity = fl.wealth_velocity(standings[0].sim_id)
print(f"  Wealth velocity: §{velocity:+.2f}/sec  (§{velocity*3600:+,.0f}/hr)")
anomalies = fl.anomalies()
if anomalies:
    print(f"  Flagged transactions: {len(anomalies)}")
    for a in anomalies[:3]:
        print(f"    {a.tx_type:<25} §{a.amount:+12,.2f}  '{a.description[:40]}'")

# 3. Bank state
print("\n[ CITY BANK ]")
bank_stats = eng.bank.stats()
print(f"  Accounts:        {bank_stats['total_accounts']}")
print(f"  Active deposits: {bank_stats['active_deposits']}")
print(f"  Total locked:    §{bank_stats['total_locked_sim']:,.2f}")
print()
for sim in sims_list:
    deps = eng.bank.active_deposits(sim.sim_id)
    matured = eng.bank.matured_ready(sim.sim_id)
    if deps or matured:
        for d in deps:
            print(f"  {sim.name:<18} LOCKED   §{d.principal:>8,.2f}  {d.term_key:<10}  "
                  f"matures in {d.days_remaining:.1f} days  APR {d.apr*100:.1f}%")
        for d in matured:
            print(f"  {sim.name:<18} MATURED  §{d.matured_amount:>8,.2f}  {d.term_key:<10}  "
                  f"interest §{d.interest_earned:.4f}  READY TO WITHDRAW")

# 4. Stock market
print("\n[ STOCK MARKET ]")
try:
    prices = eng.stocks.state().get("prices", {})
    for ticker, price in sorted(prices.items(), key=lambda x: -x[1]):
        holdings = sum(1 for s in sims_list
                       if eng.stocks._holdings.get(s.sim_id, {}).get(ticker, 0) > 0)
        print(f"  {ticker:<6} §{price:>8.4f}   {holdings} holders")
except Exception as e:
    print(f"  (stocks unavailable: {e})")

# 5. Blockchain
print("\n[ SIMCHAIN ]")
chain_summary = eng.chain.summary()
print(f"  Height:          {chain_summary['height']} blocks")
print(f"  Total wallets:   {chain_summary['total_wallets']}")
print(f"  Pending txs:     {chain_summary['pending_txs']}")
print(f"  Contracts:       {chain_summary['contracts']}")
chain_sm = eng.chain.get_contract("stock_market")
if chain_sm:
    top_ticker = max(chain_sm.prices().items(), key=lambda x: x[1])
    print(f"  Top on-chain stock: {top_ticker[0]} §{top_ticker[1]:.4f}")

# 6. Sim needs
print("\n[ SIM NEEDS & EMOTIONS ]")
for sim in sims_list:
    n = sim.needs
    sleeping = " [SLEEPING]" if getattr(sim, "_sleeping", False) else ""
    print(f"  {sim.name:<18} H:{n.hunger:4.0f} E:{n.energy:4.0f} S:{n.social:4.0f} "
          f"F:{n.fun:4.0f} Hy:{n.hygiene:4.0f}  "
          f"emotion={sim.emotion.dominant:<12} §{sim.simoleons:>8,.2f}{sleeping}")

# 7. Relationships
print("\n[ RELATIONSHIP NETWORK ]")
for sim in sims_list:
    friends = []
    rivals  = []
    for other in sims_list:
        if other.sim_id == sim.sim_id:
            continue
        rel = eng.relationships.get(sim.sim_id, other.sim_id)
        if rel.friendship >= 45:
            friends.append(f"{other.name.split()[0]}(F{rel.friendship:.0f})")
        elif rel.friendship < 0:
            rivals.append(f"{other.name.split()[0]}(F{rel.friendship:.0f})")
    if friends or rivals:
        print(f"  {sim.name:<18}  friends: {', '.join(friends) or '-':<35}  "
              f"rivals: {', '.join(rivals) or '-'}")

# 8. Beliefs
print("\n[ SIM BELIEFS (top causal models) ]")
for sim in sims_list:
    beliefs = getattr(sim, "beliefs", None)
    if beliefs:
        causal = [(k, v) for k, v in beliefs._causal.items()]
        if causal:
            best = sorted(causal, key=lambda x: -x[1].confidence)[:2]
            for (action, target, outcome), cb in best:
                print(f"  {sim.name:<18} believes '{action[:25]}' → "
                      f"'{outcome}' (conf={cb.confidence:.2f}, val={cb.valence:+.2f})")

# 9. Pressure events
print("\n[ PRESSURE EVENTS ]")
pressure_log = eng.pressure_engine.recent_events(n=10)
if pressure_log:
    for ev in pressure_log[-5:]:
        sim = eng._sim_lookup.get(ev.get("sim_id",""))
        name = sim.name if sim else ev.get("sim_id","?")[:8]
        print(f"  {name:<18} [{ev['event_type']:<22}] {ev.get('narrative','')[:55]}")
else:
    print("  (no pressure events yet)")

# 10. Collateral
print("\n[ COLLATERAL STATUS ]")
coll_stats = eng.collateral.stats()
print(f"  Active collateral cases: {coll_stats['active_collateral_sims']}")
print(f"  Liquidations:            {coll_stats['liquidations']}")
for sim in sims_list:
    rec = eng.collateral.active_for(sim.sim_id)
    if rec:
        print(f"  {sim.name:<18} COLLATERAL ACTIVE — credit §{rec.credit_extended:.2f}  "
              f"assets: {[a.description[:20] for a in rec.assets]}")

# 11. Institutions
print("\n[ INSTITUTIONAL SANCTIONS ]")
inst_stats = eng.institutions.stats()
print(f"  Total sanctions: {inst_stats['total_sanctions']}")
if inst_stats["by_type"]:
    for stype, count in sorted(inst_stats["by_type"].items(), key=lambda x: -x[1]):
        print(f"    {stype:<30} {count}x")
recent = eng.institutions.recent(n=5)
for r in recent:
    sim = eng._sim_lookup.get(r["sim_id"])
    name = sim.name if sim else r["sim_id"][:8]
    print(f"  {name:<18} [{r['institution']:<18}] {r['sanction']:<25} "
          f"§{r['amount']:.2f}")

# 12. Emergence analytics
print("\n[ EMERGENCE ANALYTICS ]")
snap = eng.emergence.latest()
if snap:
    print(f"  Policy diversity:    {snap['policy_diversity']:.4f}  "
          f"(0=all same type, 1=fully diverse)")
    print(f"  Wealth inequality:   {snap['inequality']:.4f}  "
          f"(Gini, 0=equal, 1=one sim has everything)")
    print(f"  Social mobility:     {snap['social_mobility']:.4f}  "
          f"(1=lots of rank movement)")
    print(f"  Novelty score:       {snap['novelty_score']:.4f}  "
          f"(fraction of new event types)")
    earners = fl.top_earners(limit=3)
    print(f"  Top earners (total income since start):")
    for e in earners:
        sim = eng._sim_lookup.get(e["sim_id"])
        name = sim.name if sim else e["sim_id"][:8]
        print(f"    {name:<18} §{e['total_income']:>10,.2f}")
else:
    print("  (snapshot not yet taken — run more beats)")

# 13. Identity drift
print("\n[ IDENTITY DRIFT ]")
for sim in sims_list:
    mag = eng.trait_drift.drift_magnitude(sim)
    if mag > 0.001:
        summ = eng.trait_drift.summary(sim)
        drifted = {k: round(summ["current"][k] - summ["baseline"][k], 3)
                   for k in summ["current"]
                   if abs(summ["current"][k] - summ["baseline"][k]) > 0.002}
        if drifted:
            print(f"  {sim.name:<18} drift={mag:.4f}  {drifted}")

# 14. Rumor network
print("\n[ RUMOR NETWORK ]")
rumors = eng.rumor_network.active_rumors()
if rumors:
    for r in rumors[:5]:
        print(f"  about={r['about'][:8]}  [{r['predicate'][:30]}]  "
              f"conf={r['conf']:.2f}  hops={r['hops']}  "
              f"{'(MISTAKEN)' if r['mistaken'] else ''}")
else:
    print("  (no active rumors)")

# 15. Ledger summary
print("\n[ LEDGER SUMMARY ]")
ledger_sum = fl.summary()
print(f"  Total transactions: {ledger_sum['total_transactions']}")
print(f"  Flagged anomalies:  {ledger_sum['flagged']}")
print(f"  Total volume:       §{ledger_sum['total_volume']:,.2f}")

# 16. Event bus summary
print("\n[ EVENTS CAPTURED ]")
by_type: dict[str, int] = defaultdict(int)
for ev in event_log:
    by_type[ev["type"]] += 1
for etype, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
    print(f"  {etype:<35} {cnt:>4}x")

print()
print("="*70)
print(f"  Simulation complete. {args.beats} beats × {SIM_SECONDS_PER_BEAT/60:.0f} min = "
      f"{args.beats*SIM_SECONDS_PER_BEAT/3600:.1f} sim-hours.")
print("="*70 + "\n")
