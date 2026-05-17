import json
import re

from llm.backend import LLMBackend
from llm.schemas import ADJUDICATOR_SCHEMA

ADJUDICATOR_SYSTEM_BASE = """You are the social interaction adjudicator for an AI life simulation.
Given two sim profiles, emotional state, relationship context, and an interaction,
determine a realistic outcome.

Always respond with ONLY valid JSON.

Return keys:
- sim_b_reaction
- friendship_delta
- romance_delta
- social_need_restore_a
- social_need_restore_b
- fun_restore_a
- fun_restore_b
- emotion_a
- emotion_b
- valence
- memory_tag
- charisma_xp_a
- comedy_xp_a
- reasoning
- suggested_event (optional) — if the interaction implies a significant life event,
  include this object with keys: type, narrative, visibility, valence, intensity.
  Valid types: birth, death, marriage, divorce, breakup, job_loss, promotion,
  illness, scandal, redemption, moving_out, random_drama.
  Valid visibility: private, witnessed, household, club, public.
  Only include when genuinely warranted by the interaction context."""


def call_adjudicator(backend: LLMBackend, system: str, user: str) -> dict:
    raw = backend.chat(system=system, user=user, schema=ADJUDICATOR_SCHEMA)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if not raw.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            raw = match.group(0)
    return json.loads(raw)
