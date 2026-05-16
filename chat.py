#!/usr/bin/env python3
"""
chat.py — Have a conversation with a single generated Sim.

Usage:
    python chat.py
    python chat.py --backend ollama --no-datasets

Commands during chat:
    /state   — show full sim state (needs, skills, fears)
    /profile — re-display the sim's profile
    /quit    — exit
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import EMOTIONS_27

# ── Colours (ANSI, degrade gracefully on Windows if needed) ──────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_MAGENTA = "\033[35m"
_BLUE   = "\033[34m"
_WHITE  = "\033[37m"

NEED_NAMES = ["hunger", "energy", "social", "fun", "hygiene", "comfort"]


def _c(colour: str, text: str) -> str:
    return f"{colour}{text}{_RESET}"


def _need_bar(val: float, name: str) -> str:
    icon = "🟢" if val >= 65 else "🟡" if val >= 35 else "🔴"
    return f"{name[:3].upper()}:{icon}{int(val):3d}"


def _needs_line(sim) -> str:
    return "  ".join(_need_bar(getattr(sim.needs, n), n) for n in NEED_NAMES)


def _build_chat_system(sim, rel_state: str) -> str:
    p = sim.profile
    o = sim.ocean
    mbti      = p.get("mbti", "")
    zodiac    = p.get("zodiac", "")
    culture   = p.get("cultural_background", "")
    fears_str = ", ".join(f.label for f in sim.fears) or "none"
    orientation = getattr(sim, "social_orientation", "Warm-Agreeable")
    return f"""You are roleplaying as {sim.name} in an AI life simulation.

CHARACTER PROFILE:
  Name: {sim.name} | Age: {p['age']} | Gender: {p['gender']}
  Job: {p['job']} | Aspiration: {p['aspiration']}
  Traits: {', '.join(p['traits'])}
  Interests: {', '.join(p['interests'])}
  Dealbreakers: {', '.join(p['dealbreakers'])}
  OCEAN: O={o['openness']} C={o['conscientiousness']} E={o['extraversion']} A={o['agreeableness']} N={o['neuroticism']}
  MBTI: {mbti} | Zodiac: {zodiac} | Culture: {culture}
  Humor: {p['humor_type']} | Communication: {p['comm_style']}
  Attachment: {p['attachment']} | Social orientation: {orientation}
  Active fears: {fears_str}
  Summary: {p.get('self_summary', '')}

CURRENT STATE:
  Emotion: {sim.emotion.dominant} (valence={sim.emotion.dominant_valence:.2f})
  Relationship with you: {rel_state}

RULES:
1. Respond AS {sim.name} — fully in character, no meta-commentary.
2. Keep responses 1-4 sentences. Natural, conversational tone.
3. True to their traits: {', '.join(p['traits'])}.
4. After your in-character response, on a NEW LINE output exactly:
   JSON: {{"emotion": "<one of the 27 labels>", "valence": <0.0-1.0>, "social_delta": <-5 to +5>}}
5. Valid emotion labels: {', '.join(EMOTIONS_27[:10])} ... (27 total)

Example format:
That's such an interesting question! I've been thinking about the same thing.
JSON: {{"emotion": "curiosity", "valence": 0.75, "social_delta": 2}}"""


def _parse_response(raw: str) -> tuple[str, dict]:
    """Split sim dialogue from the trailing JSON line."""
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
    json_match = re.search(r"JSON:\s*(\{[\s\S]*?\})", raw)
    if json_match:
        dialogue = raw[:json_match.start()].strip()
        try:
            state_update = json.loads(json_match.group(1))
        except Exception:
            state_update = {}
    else:
        dialogue = raw.strip()
        state_update = {}
    return dialogue, state_update


def _display_header(sim) -> None:
    p = sim.profile
    o = sim.ocean
    w = 66
    print(f"\n{'═' * w}")
    print(_c(_BOLD + _CYAN, f"  CHAT WITH: {sim.name}"))
    print(f"  {p['age']}yo {p['gender']}  |  {p['job']}  |  §{sim.simoleons:.0f}")
    mbti   = p.get("mbti", "")
    zodiac = p.get("zodiac", "")
    culture = p.get("cultural_background", "")
    print(f"  MBTI: {mbti}  |  {zodiac}  |  {culture}")
    print(f"  Traits: {', '.join(p['traits'])}")
    print(f"  Aspiration: {p['aspiration']}  |  Attachment: {p['attachment']}")
    print(f"  OCEAN: O={o['openness']} C={o['conscientiousness']} "
          f"E={o['extraversion']} A={o['agreeableness']} N={o['neuroticism']}")
    print(f"  Summary: {p.get('self_summary','')[:80]}")
    print(f"{'─' * w}")
    print(f"  Commands: /state  /profile  /quit")
    print(f"{'═' * w}\n")


def _display_state_bar(sim, rel_label: str) -> None:
    emo_icon = "😄" if sim.emotion.dominant_valence > 0.6 else \
               "😞" if sim.emotion.dominant_valence < 0.4 else "😐"
    print(_c(_DIM, f"  [{sim.name}] {emo_icon} {sim.emotion.dominant}  "
             f"|  Rel: {rel_label}  |  {_needs_line(sim)}"))


def _display_full_state(sim) -> None:
    print(f"\n{'─' * 50}")
    print(_c(_BOLD, f"  {sim.name} — Full State"))
    print(f"  Emotion : {sim.emotion.dominant} (val={sim.emotion.dominant_valence:.2f})")
    print(f"  Needs   : {_needs_line(sim)}")
    print(f"  Skills  : " + "  ".join(
        f"{k[:3].upper()}:{v:.1f}" for k, v in sim.skills.levels.items()))
    print(f"  Fears   : {', '.join(f.label for f in sim.fears) or 'none'}")
    print(f"  Simoleons: §{sim.simoleons:.0f}  Career: {sim.career_performance:.0f}/100")
    print(f"  Social orientation: {getattr(sim, 'social_orientation', '?')}")
    rep = getattr(sim, "reputation_score", 0)
    ei  = getattr(sim, "ei_reputation", 0)
    cr  = getattr(sim, "creative_reputation", 0)
    print(f"  Reputation: {rep:+.0f}  EI Rep: {ei:+.0f}  Creative Rep: {cr:.0f}")
    print(f"{'─' * 50}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with a Sim")
    parser.add_argument("--backend", default="ollama",
                        choices=["ollama", "llama-server", "llama-cpp"])
    parser.add_argument("--ollama-model", default=None)
    parser.add_argument("--no-datasets", action="store_true")
    args = parser.parse_args()

    if args.ollama_model:
        os.environ["SIM_V2_OLLAMA_MODEL"] = args.ollama_model

    # Generate sim
    essays: list[str] = []
    if not args.no_datasets:
        print("Loading datasets (for OCEAN scoring)...")
        try:
            from datasets.okcupid import load_okcupid_essays
            essays = load_okcupid_essays()
            print(f"  {len(essays)} OkCupid essays ready.")
        except Exception:
            pass

    from identity.profile_factory import generate_sim_profile
    from core.sim import Sim
    profile = generate_sim_profile(okcupid_essays=essays or None)
    sim = Sim(profile)

    # LLM backend
    from llm.backend import create_backend
    llm = create_backend(args.backend)

    # Relationship tracking with the player
    from core.relationships import RelationshipRecord
    player_rel = RelationshipRecord()
    history: list[dict] = []   # conversation history for context

    _display_header(sim)

    print(_c(_DIM, "  (Datasets not loaded — using OCEAN-derived personality only)\n")
          if args.no_datasets else "")

    while True:
        _display_state_bar(sim, player_rel.state_label())
        try:
            user_input = input(_c(_BOLD + _GREEN, "\nYou: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/quit", "/q", "quit", "exit"):
            print(_c(_DIM, f"\n  {sim.name} waves goodbye. See you next time.\n"))
            break

        if user_input.lower() == "/state":
            _display_full_state(sim)
            continue

        if user_input.lower() == "/profile":
            _display_header(sim)
            continue

        # Build system with current state
        system = _build_chat_system(sim, player_rel.state_label())

        # Build conversation context (last 6 turns)
        context_lines = "\n".join(
            f"{'You' if m['role'] == 'user' else sim.name}: {m['content']}"
            for m in history[-6:]
        )
        user_msg = (
            f"Previous conversation:\n{context_lines}\n\n"
            f"You: {user_input}"
            if history else f"You: {user_input}"
        )

        # Call LLM
        try:
            raw = llm.chat(system=system, user=user_msg, max_tokens=300, temperature=0.85)
        except Exception as exc:
            print(_c(_RED, f"\n  [Error calling LLM: {exc}]\n"))
            continue

        dialogue, state_update = _parse_response(raw)

        # Update sim state from JSON
        new_emotion = state_update.get("emotion", "")
        valence     = float(state_update.get("valence", sim.emotion.dominant_valence))
        social_d    = float(state_update.get("social_delta", 0))

        if new_emotion and new_emotion in EMOTIONS_27:
            sim.emotion.add(new_emotion, abs(valence - 0.5) + 0.3, duration=4, source="chat")
        sim.needs.restore("social", max(0, social_d * 3))
        player_rel.apply_deltas(social_d, 0)

        # Advance one mini-tick
        sim.needs.tick(sim.ocean)

        # Print response
        print()
        print(_c(_BOLD + _CYAN, f"{sim.name}:") + f" {dialogue}")

        # Store history
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": dialogue})

    # Print farewell summary
    print(f"\n{'─' * 50}")
    print(_c(_BOLD, "  Conversation Summary"))
    print(f"  Turns: {len(history) // 2}")
    print(f"  Final relationship: {player_rel.state_label()}")
    print(f"    Friendship: {player_rel.friendship:+.1f}")
    print(f"  {sim.name}'s final emotion: {sim.emotion.dominant}")
    print(f"{'─' * 50}\n")


if __name__ == "__main__":
    main()
