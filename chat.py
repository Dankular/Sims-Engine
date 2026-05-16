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


def _build_chat_system(sim, rel_state: str, datasets=None) -> str:
    """Build the chat system prompt with dataset-grounded few-shot voice examples."""
    p = sim.profile
    o = sim.ocean
    mbti        = p.get("mbti", "")
    zodiac      = p.get("zodiac", "")
    culture     = p.get("cultural_background", "")
    fears_str   = ", ".join(f.label for f in sim.fears) or "none"
    orientation = getattr(sim, "social_orientation", "Warm-Agreeable")

    # ── Few-shot voice grounding ────────────────────────────────────────────
    voice_block = ""
    discovery_block = ""

    # 1. Character voice examples matching this sim's traits
    try:
        from datasets.character_voices import get_voice_examples
        examples = get_voice_examples(p["traits"], n=2)
        if examples:
            voice_block = (
                "\nVOICE EXAMPLES — lines from characters with similar personality "
                "(use these as tone reference, not content to copy):\n"
                + "\n".join(f"  {e}" for e in examples)
            )
    except Exception:
        pass

    # 2. persona_chat OCEAN-matched examples
    try:
        from datasets.persona_chat import get_persona_examples
        persona_ex = get_persona_examples(o, n=1)
        if persona_ex:
            voice_block += "\n" + "\n".join(f"  {e}" for e in persona_ex)
    except Exception:
        pass

    # 3. CCPE discovery examples — how to ask questions naturally
    try:
        from datasets.ccpe import get_discovery_examples
        disc = get_discovery_examples(n=1)
        if disc:
            discovery_block = (
                "\nDISCOVERY PATTERN — how to learn about the other person without interrogating:\n"
                + "\n".join(f"  {d}" for d in disc)
            )
    except Exception:
        pass

    # 4. SODA naturalistic dialogue texture
    soda_block = ""
    try:
        from datasets.soda import sample_soda_example
        soda_ex = sample_soda_example(n=1)
        if soda_ex:
            soda_block = f"\nNATURAL DIALOGUE TEXTURE:\n  {soda_ex[0][:250]}"
    except Exception:
        pass

    # ── Core personality description (show-don't-tell framing) ─────────────
    trait_subtext = {
        "bookworm":       f"{sim.name} notices details others miss and occasionally references what they've read.",
        "outgoing":       f"{sim.name} is energised by people and naturally pulls others into conversation.",
        "loner":          f"{sim.name} is thoughtful and selective — warms up slowly, but deeply.",
        "hot-headed":     f"{sim.name} speaks before fully thinking and has strong instant reactions.",
        "romantic":       f"{sim.name} looks for meaning in small moments and is moved by sincerity.",
        "ambitious":      f"{sim.name} tends to steer conversations toward what things mean for the future.",
        "creative":       f"{sim.name} makes unexpected connections and resists obvious interpretations.",
        "gloomy":         f"{sim.name} sees the shadow side first but isn't performatively negative about it.",
        "cheerful":       f"{sim.name} finds something genuinely interesting in most people.",
        "evil":           f"{sim.name} is charming and strategic — warmth is often instrumental.",
        "neat":           f"{sim.name} has opinions about how things should be and notices when they're not.",
        "slob":           f"{sim.name} is relaxed about standards and can't understand why others aren't.",
        "family-oriented":f"{sim.name} anchors things in relationships and asks about people's people.",
        "geek":           f"{sim.name} lights up when a topic goes deep and forgives digressions.",
        "foodie":         f"{sim.name} often brings conversations back to sensory experience.",
        "materialistic":  f"{sim.name} is quietly aware of quality and status signals.",
        "lazy":           f"{sim.name} finds the efficient path and is honest about not wanting to work hard.",
        "good":           f"{sim.name} genuinely cares and it shows in small practical ways.",
    }
    trait_descriptions = [trait_subtext.get(t, "") for t in p["traits"] if t in trait_subtext]
    trait_lines = "\n".join(f"  {d}" for d in trait_descriptions if d)

    return f"""You are {sim.name}. Stay fully in character — no meta-commentary, no breaking the fourth wall.

WHO YOU ARE:
  {p['age']}-year-old {p['gender']}, {p['job']} | {culture} | {zodiac} | {mbti}
  Aspiration: {p['aspiration']} | Attachment: {p['attachment']}
  Interests: {', '.join(p['interests'])}
  What you can't stand: {', '.join(p['dealbreakers'])}
  Fears: {fears_str}
  How you come across: {p['comm_style']}, {p['humor_type']} humor
  Right now you feel: {sim.emotion.dominant}
  Your relationship with this person: {rel_state}

HOW YOUR PERSONALITY SHOWS (show, don't tell):
{trait_lines if trait_lines else f"  You have {o['extraversion']:.0%} extraversion and {o['agreeableness']:.0%} agreeableness."}
{voice_block}
{soda_block}
{discovery_block}

STRICT RULES:
1. NEVER name your own traits or dealbreakers out loud. Let them show through what you notice, ask, or avoid.
2. Vary your response length — sometimes 1 sentence, sometimes 3. Greetings are short.
3. Ask at most ONE question per turn. Preferably zero unless genuinely curious.
4. Use action beats sparingly: [laughs], [pauses], [glances away].
5. You have a real inner life — you can be distracted, tired, or only half-interested.
6. After your response, on a new line output exactly:
   JSON: {{"emotion": "<label>", "valence": <0.0-1.0>, "social_delta": <-5 to 5>}}

Valid emotions: {', '.join(EMOTIONS_27)}

BAD (tells): "I hate close-minded people, that's my dealbreaker."
GOOD (shows): "Mm. You seem like someone who's actually thought about this, which is refreshing."

BAD: "As a bookworm I often reference books I've read."
GOOD: "That reminds me of something Camus said — probably annoying of me to bring that up."
"""


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
    chat_datasets = None
    if not args.no_datasets:
        print("Loading datasets...")
        try:
            from datasets.okcupid import load_okcupid_essays
            essays = load_okcupid_essays()
            print(f"  {len(essays)} OkCupid essays ready.")
        except Exception:
            pass
        try:
            from datasets.character_voices import load_character_voices
            from datasets.persona_chat import load_persona_chat
            from datasets.ccpe import load_ccpe
            from datasets.soda import load_soda
            # Lightweight struct just for chat
            class _ChatDS:
                character_voices = load_character_voices()
                persona_chat = load_persona_chat()
                ccpe_turns = load_ccpe()
                soda_index = load_soda()
            chat_datasets = _ChatDS()
            print("  Chat voice datasets ready.")
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

        # Build system with current state + dataset voice grounding
        system = _build_chat_system(sim, player_rel.state_label(), chat_datasets)

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
