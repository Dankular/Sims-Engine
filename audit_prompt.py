"""
audit_prompt.py — Measure what actually lands in each adjudicator prompt call.

Patches build_adjudicator_system and get_interaction_context to intercept
each section and record its character count, then fires one real tick.

Run:  python audit_prompt.py
"""
from __future__ import annotations
import sys, textwrap
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Build a minimal engine ────────────────────────────────────────────────────
print("Loading datasets + engine...")
from datasets.loader import load_all_datasets
from identity.profile_factory import generate_sim_profile
from core.sim import Sim
from world.households import assign_households
from persistence.sqlite import PersistenceLayer
from engine.engine import SimEngine
from llm.backend import OllamaBackend

datasets = load_all_datasets()
sims = [Sim(generate_sim_profile(okcupid_essays=datasets.okcupid_essays)) for _ in range(2)]
assign_households(sims)

# Dummy backend — records the prompt but never calls Ollama
class CapturingBackend:
    last_system = ""
    last_user = ""
    sections: dict[str, int] = {}

    def chat(self, system, user, max_tokens=800, temperature=0.7, schema=None):
        self.last_system = system
        self.last_user = user
        return '{"sim_b_reaction":"ok","friendship_delta":1,"romance_delta":0,"social_need_restore_a":5,"social_need_restore_b":5,"fun_restore_a":2,"fun_restore_b":2,"emotion_a":"joy","emotion_b":"neutral","valence":0.6,"memory_tag":"said hello","charisma_xp_a":0,"comedy_xp_a":0,"reasoning":"test"}'

backend = CapturingBackend()
engine = SimEngine(sims=sims, llm=backend, datasets=datasets, db=PersistenceLayer())

# ── Patch context builder to record section sizes ─────────────────────────────
import llm.context as ctx_mod

_orig_interaction_context = ctx_mod.get_interaction_context
_orig_adjudicator_system  = ctx_mod.build_adjudicator_system

section_log: list[tuple[str, int, str]] = []  # (name, chars, preview)

def _wrap_interaction_context(interaction, sim_a, sim_b, datasets=None,
                               memory_store=None, current_tick=0):
    parts_before = []

    # Monkey-patch the internal helpers temporarily to log each section
    import datasets.atomic as at_mod
    import datasets.social_iqa as iqa_mod
    orig_atomic = at_mod.query_atomic
    orig_iqa    = iqa_mod.sample_social_iqa

    atomic_result = [None]
    iqa_result    = [None]

    def _logged_atomic(inter):
        r = orig_atomic(inter)
        atomic_result[0] = r
        return r
    def _logged_iqa(inter):
        r = orig_iqa(inter)
        iqa_result[0] = r
        return r

    at_mod.query_atomic       = _logged_atomic
    iqa_mod.sample_social_iqa = _logged_iqa

    result = _orig_interaction_context(
        interaction, sim_a, sim_b,
        datasets=datasets, memory_store=memory_store, current_tick=current_tick
    )

    at_mod.query_atomic       = orig_atomic
    iqa_mod.sample_social_iqa = orig_iqa

    # Log each sub-section
    if memory_store:
        mem_chunk = ""
        for line in result.split("\n"):
            if line.startswith("RELEVANT MEMORIES") or line.startswith("LONG-TERM"):
                mem_chunk += line + "\n"
        if mem_chunk:
            section_log.append(("memory_store", len(mem_chunk), mem_chunk[:80]))

    if "RECENT DIALOGUE" in result:
        for chunk in result.split("\n"):
            if "RECENT DIALOGUE" in chunk:
                section_log.append(("dialogue_buffer", result.count("\n  ["), "last N turns"))
                break

    if atomic_result[0]:
        section_log.append(("atomic/COMET", len(f"ATOMIC: {atomic_result[0]}"),
                            f"ATOMIC: {str(atomic_result[0])[:60]}"))
    if iqa_result[0]:
        section_log.append(("social_iqa", len(f"SOCIAL_IQA: {iqa_result[0]}"),
                            f"SOCIAL_IQA: {str(iqa_result[0])[:60]}"))

    if "PERSONA EXAMPLES" in result:
        for line in result.split("\n"):
            if line.startswith("PERSONA EXAMPLES"):
                section_log.append(("persona_chat", len(line), line[:80]))

    if "Vulnerable sim" in result:
        section_log.append(("vulnerable_flag", 45, "Vulnerable sim present; apply empathetic reasoning."))

    return result


def _wrap_adjudicator_system(norms, datasets=None, interaction=""):
    result = _orig_adjudicator_system(norms, datasets=datasets, interaction=interaction)

    # Measure each section of the system prompt
    base_end = result.find("\n\nSOCIAL NORMS:")
    if base_end == -1:
        base_end = result.find("\n\nEMOTION")
    base_chars = base_end if base_end > 0 else len(result)
    section_log.append(("system_base", base_chars, "adjudicator role + JSON schema instructions"))

    if "SOCIAL NORMS:" in result:
        block = result[result.find("SOCIAL NORMS:"):result.find("\n\nEMOTION")]
        section_log.append(("social_norms (3 sampled)", len(block), block[:80]))

    if "EMOTION CALIBRATION" in result:
        ec_start = result.find("EMOTION CALIBRATION")
        ec_end   = result.find("\n\nETHICS", ec_start)
        ec_end   = ec_end if ec_end > 0 else ec_start + 1500
        block    = result[ec_start:ec_end]
        section_log.append(("emotion_calib (dair-ai/emotion)", len(block), block[:80]))

    if "ETHICS CALIBRATION" in result:
        eth_start = result.find("ETHICS CALIBRATION")
        block     = result[eth_start:]
        section_log.append(("ethics_norms (hendrycks/ethics)", len(block), block[:80]))

    return result


ctx_mod.get_interaction_context  = _wrap_interaction_context
ctx_mod.build_adjudicator_system = _orig_adjudicator_system  # let engine call wrap via engine.py import

# Re-patch in engine namespace too
import engine.engine as eng_mod
eng_mod.get_interaction_context  = _wrap_interaction_context
eng_mod.build_adjudicator_system = _wrap_adjudicator_system

# ── Fire one tick ─────────────────────────────────────────────────────────────
print("Running one tick to capture a real adjudicator prompt...\n")
engine.run_tick()
import time; time.sleep(0.5)  # let thread pool settle

sys_prompt = backend.last_system
usr_prompt = backend.last_user

if not sys_prompt:
    print("No LLM call captured (no ACTIVE interaction this tick). Try again.")
    sys.exit(0)

# ── Report ────────────────────────────────────────────────────────────────────
total_chars = len(sys_prompt) + len(usr_prompt)
total_tokens_est = total_chars // 4  # rough 4 chars/token

print("=" * 60)
print("  ADJUDICATOR PROMPT BREAKDOWN")
print(f"  Total: {total_chars:,} chars  (~{total_tokens_est:,} tokens est.)")
print("=" * 60)

print(f"\n{'SYSTEM PROMPT':45}  {len(sys_prompt):>6} chars")
print("-" * 60)

# Parse system prompt sections manually
sections = [
    ("  Base role + JSON schema",
     sys_prompt[:sys_prompt.find("SOCIAL NORMS") if "SOCIAL NORMS" in sys_prompt
                else sys_prompt.find("EMOTION") if "EMOTION" in sys_prompt else len(sys_prompt)]),
]
if "SOCIAL NORMS:" in sys_prompt:
    start = sys_prompt.find("SOCIAL NORMS:")
    end   = sys_prompt.find("\n\nEMOTION", start)
    sections.append(("  social_norms  (3×norm sampled)", sys_prompt[start:end if end>0 else start+500]))
if "EMOTION CALIBRATION" in sys_prompt:
    start = sys_prompt.find("EMOTION CALIBRATION")
    end   = sys_prompt.find("\n\nETHICS", start)
    sections.append(("  emotion_calib (dair-ai/emotion)", sys_prompt[start:end if end>0 else len(sys_prompt)]))
if "ETHICS CALIBRATION" in sys_prompt:
    start = sys_prompt.find("ETHICS CALIBRATION")
    sections.append(("  ethics_norms  (hendrycks/ethics)", sys_prompt[start:]))

for label, text in sections:
    pct = len(text) / len(sys_prompt) * 100
    bar = "#" * int(pct / 3)
    print(f"  {label:42}  {len(text):>5} chars  {pct:4.0f}%  {bar}")

print(f"\n{'USER PROMPT':45}  {len(usr_prompt):>6} chars")
print("-" * 60)

# Parse user prompt sections
usr_sections = []
for marker, label in [
    ("=== SIM A ===",        "  sim_a profile block"),
    ("=== SIM B ===",        "  sim_b profile block"),
    ("=== RELATIONSHIP",     "  relationship state"),
    ("=== ENVIRONMENT ===",  "  venue + time"),
    ("=== CONTEXTUAL",       "  contextual knowledge (datasets)"),
    ("=== INTERACTION ===",  "  interaction line"),
]:
    if marker in usr_prompt:
        start = usr_prompt.find(marker)
        # find next marker
        next_starts = [
            usr_prompt.find(m, start + 1)
            for m, _ in [
                ("=== SIM A ===",""), ("=== SIM B ===",""),
                ("=== RELATIONSHIP",""), ("=== ENVIRONMENT ===",""),
                ("=== CONTEXTUAL",""), ("=== INTERACTION ===",""),
            ]
            if usr_prompt.find(m, start + 1) > 0
        ]
        end = min(next_starts) if next_starts else len(usr_prompt)
        usr_sections.append((label, usr_prompt[start:end]))

for label, text in usr_sections:
    pct = len(text) / len(usr_prompt) * 100
    bar = "#" * int(pct / 3)
    print(f"  {label:42}  {len(text):>5} chars  {pct:4.0f}%  {bar}")

# Contextual section breakdown
if "=== CONTEXTUAL KNOWLEDGE ===" in usr_prompt:
    ctx_start = usr_prompt.find("=== CONTEXTUAL KNOWLEDGE ===")
    ctx_end   = usr_prompt.find("=== INTERACTION ===", ctx_start)
    ctx_block = usr_prompt[ctx_start:ctx_end if ctx_end > 0 else len(usr_prompt)]
    print(f"\n  Contextual knowledge breakdown:")
    for tag, ds_label in [
        ("RELEVANT MEMORIES",   "    memory_store (ChromaDB recall)"),
        ("LONG-TERM MEMORY",    "    long_term_memory"),
        ("RECENT DIALOGUE",     "    dialogue_buffer"),
        ("ATOMIC:",             "    atomic / COMET"),
        ("SOCIAL_IQA:",         "    social_iqa"),
        ("PERSONA EXAMPLES",    "    persona_chat"),
        ("EMPATHETIC CONTEXT",  "    empath_index  [NEW]"),
        ("SITUATIONAL EXAMPLE", "    dialogue_actions  [NEW]"),
        ("Vulnerable sim",      "    vulnerable_flag"),
    ]:
        if tag in ctx_block:
            # find the block for this tag
            ts = ctx_block.find(tag)
            # next tag start
            others = [ctx_block.find(t, ts+1) for t, _ in [
                ("RELEVANT MEMORIES",""),("LONG-TERM MEMORY",""),
                ("RECENT DIALOGUE",""),("ATOMIC:",""),
                ("SOCIAL_IQA:",""),("PERSONA EXAMPLES",""),
                ("Vulnerable sim",""),
            ] if ctx_block.find(t, ts+1) > 0]
            te = min(others) if others else len(ctx_block)
            chunk = ctx_block[ts:te]
            print(f"  {ds_label:44}  {len(chunk):>4} chars")

print()
print("=" * 60)
print("  DATASETS THAT NEVER REACH THE LLM PROMPT")
print("  (used for scheduling / post-processing only)")
print("=" * 60)
not_in_prompt = [
    ("okcupid_essays",    "profile generation (OCEAN scoring)"),
    ("convai2_seeds",     "choose_interaction seeding"),
    ("daily_dialog_index","choose_interaction venue-topic seeding"),
    ("moral_stories",     "choose_interaction / life event seeding"),
    ("moral_choice",      "choose_interaction moral dilemmas"),
    ("aita_index",        "post-LLM reputation (apply_resolved)"),
    ("orientation_examples","social orientation drift (sim.tick)"),
    ("jokes_by_tier",     "choose_interaction joke seeding"),
    ("hippocorpus",       "narrative story writer"),
    ("persuasion_args",   "choose_interaction + apply_resolved mod"),
    ("confessions_index", "choose_interaction confession seeding"),
    ("ei_scenarios",      "choose_interaction EI scenario seeding"),
    ("mental_chat_index", "choose_interaction deep support"),
]
print()
print("  NOW IN PROMPT (moved from scheduler-only):")
print(f"  {'empath_index':28}  EMPATHETIC CONTEXT — emotion-matched situational story")
print(f"  {'dialogue_actions':28}  SITUATIONAL EXAMPLE — social reaction anchor")
for name, usage in not_in_prompt:
    print(f"  {name:28}  {usage}")
print()
