"""
show_prompt.py — Capture and print a real adjudicator prompt in full.
Run: python show_prompt.py
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from datasets.loader import load_all_datasets
from identity.profile_factory import generate_sim_profile
from core.sim import Sim
from world.households import assign_households
from persistence.sqlite import PersistenceLayer
from engine.engine import SimEngine

datasets = load_all_datasets()
sims = [Sim(generate_sim_profile(okcupid_essays=datasets.okcupid_essays)) for _ in range(2)]
assign_households(sims)

class CapturingBackend:
    system = ""; user = ""
    def chat(self, system, user, **kw):
        CapturingBackend.system = system
        CapturingBackend.user   = user
        return '{"sim_b_reaction":"ok","friendship_delta":1,"romance_delta":0,"social_need_restore_a":5,"social_need_restore_b":5,"fun_restore_a":2,"fun_restore_b":2,"emotion_a":"joy","emotion_b":"neutral","valence":0.6,"memory_tag":"said hello","charisma_xp_a":0,"comedy_xp_a":0,"reasoning":"test"}'

engine = SimEngine(sims=sims, llm=CapturingBackend(), datasets=datasets, db=PersistenceLayer())
engine.heartbeat.beat_once()

import time; time.sleep(0.3)

sys_p = CapturingBackend.system
usr_p = CapturingBackend.user

if not sys_p:
    print("No LLM call this tick — run again."); sys.exit(0)

DIV  = "=" * 70
SDIV = "-" * 70

def section(title, text, colour=""):
    lines = text.strip().split("\n")
    print(f"\n{DIV}")
    print(f"  {title}  [{len(text)} chars]")
    print(SDIV)
    for line in lines:
        print("  " + line)

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
print(f"\n{'#'*70}")
print(f"  FULL ADJUDICATOR PROMPT")
print(f"  System: {len(sys_p)} chars   User: {len(usr_p)} chars   Total: {len(sys_p)+len(usr_p)} chars")
print(f"{'#'*70}")

# Split system into labelled sections
sys_parts = [
    ("BASE ROLE + JSON SCHEMA", sys_p[:sys_p.find("SOCIAL NORMS") if "SOCIAL NORMS" in sys_p else sys_p.find("EMOTION")]),
]
if "SOCIAL NORMS:" in sys_p:
    s = sys_p.find("SOCIAL NORMS:")
    e = sys_p.find("\n\nEMOTION", s)
    sys_parts.append(("SOCIAL NORMS  [social_norms dataset, 15 sampled]", sys_p[s:e if e > 0 else s+1200]))
if "EMOTION CALIBRATION" in sys_p:
    s = sys_p.find("EMOTION CALIBRATION")
    e = sys_p.find("\n\nETHICS", s)
    sys_parts.append(("EMOTION CALIBRATION  [dair-ai/emotion]", sys_p[s:e if e > 0 else len(sys_p)]))
if "ETHICS CALIBRATION" in sys_p:
    s = sys_p.find("ETHICS CALIBRATION")
    sys_parts.append(("ETHICS CALIBRATION  [hendrycks/ethics]", sys_p[s:]))

for title, text in sys_parts:
    section(title, text)

# ── USER PROMPT ───────────────────────────────────────────────────────────────
markers = [
    ("=== SIM A ===",              "SIM A PROFILE"),
    ("=== SIM B ===",              "SIM B PROFILE"),
    ("=== RELATIONSHIP (A→B) ===", "RELATIONSHIP STATE"),
    ("=== ENVIRONMENT ===",        "ENVIRONMENT / VENUE"),
    ("=== CONTEXTUAL KNOWLEDGE ===","CONTEXTUAL KNOWLEDGE  [dataset injections]"),
    ("=== INTERACTION ===",        "INTERACTION"),
]

for i, (marker, title) in enumerate(markers):
    if marker not in usr_p:
        continue
    start = usr_p.find(marker)
    # find next marker
    ends = [usr_p.find(m, start + 1) for m, _ in markers if usr_p.find(m, start + 1) > 0]
    end = min(ends) if ends else len(usr_p)
    section(title, usr_p[start:end])

# ── CONTEXTUAL SECTION DETAIL ─────────────────────────────────────────────────
if "=== CONTEXTUAL KNOWLEDGE ===" in usr_p:
    ctx_s = usr_p.find("=== CONTEXTUAL KNOWLEDGE ===")
    ctx_e = usr_p.find("=== INTERACTION ===", ctx_s)
    ctx   = usr_p[ctx_s:ctx_e if ctx_e > 0 else len(usr_p)]

    print(f"\n{DIV}")
    print(f"  CONTEXTUAL KNOWLEDGE BREAKDOWN  (what each dataset contributed)")
    print(SDIV)
    tags = [
        ("RELEVANT MEMORIES",   "memory_store"),
        ("LONG-TERM MEMORY",    "long_term_memory"),
        ("RECENT DIALOGUE",     "dialogue_buffer"),
        ("ATOMIC:",             "atomic/COMET"),
        ("SOCIAL_IQA:",         "social_iqa"),
        ("EMPATHETIC CONTEXT",  "empath_index  [NEW]"),
        ("SITUATIONAL EXAMPLE", "dialogue_actions  [NEW]"),
        ("PERSONA EXAMPLES",    "persona_chat"),
        ("Vulnerable sim",      "vulnerable_flag"),
    ]
    for tag, label in tags:
        if tag in ctx:
            s = ctx.find(tag)
            others = [ctx.find(t, s+1) for t, _ in tags if ctx.find(t, s+1) > 0]
            e = min(others) if others else len(ctx)
            chunk = ctx[s:e].strip()
            print(f"\n  [{label}]  {len(chunk)} chars")
            print(f"  {chunk[:200]}")

print(f"\n{'#'*70}\n")
