import concurrent.futures
import json
import logging
import os
import re

from llm.backend import LLMBackend
from llm.schemas import ADJUDICATOR_SCHEMA

logger = logging.getLogger(__name__)

# Hard timeout for LLM calls.  When exceeded the deterministic fallback is
# used instead — game correctness is preserved, LLM provides flavor only.
_LLM_TIMEOUT = float(os.environ.get("SIM_V2_ADJ_TIMEOUT", "8.0"))

# Thread pool used for timeout enforcement (separate from engine's pool).
_timeout_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="adj_to")

ADJUDICATOR_SYSTEM_BASE = """You are the social interaction adjudicator for an AI life simulation.
Given two sim profiles, emotional state, relationship context, and an interaction,
determine a realistic outcome.

Always respond with ONLY valid JSON. Keep ALL string values concise:
- dialogue: The actual words spoken. Use format: Name: line / Name: line
  NO inner quotes. Use first names. Draw on traits, lived experiences, opinions, context datasets.
  Each line ≤ 15 words. Be specific — reference the topic, not generic platitudes.
- sim_b_reaction: one sentence describing what physically happens (body language, tone). ≤ 15 words.
- memory_tag: ≤ 5 words (e.g. "first_laugh", "awkward_silence")
- emotion_a / emotion_b: single emotion word
- reasoning: ≤ 15 words

Return keys:
- dialogue
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


def _repair_json(raw: str) -> str:
    """Best-effort fix for the most common model mistakes."""
    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # Extract first JSON object if wrapped in prose
    if not raw.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            raw = m.group(0)
    # Fix unescaped double-quotes inside JSON string values.
    # Replace any " that isn't: start-of-value, end-of-value, or already escaped.
    # Strategy: for each string value, escape inner double quotes.
    def _fix_string_value(m: re.Match) -> str:
        key = m.group(1)
        val = m.group(2)
        # escape any unescaped double quotes inside val
        val = re.sub(r'(?<!\\)"', r'\\"', val)
        return f'"{key}": "{val}"'
    raw = re.sub(r'"(\w+)":\s*"((?:[^"\\]|\\.)*)"', _fix_string_value, raw)
    return raw


def _deterministic_fallback(interaction: str) -> dict:
    """
    Game-correct result when the LLM times out or errors.

    Uses the same Beta-distribution logic as MockBackend so outcomes are
    statistically meaningful rather than hardcoded zeros.  LLM is flavor;
    this guarantees correctness.
    """
    from llm.mock_backend import mock_adjudicate
    return mock_adjudicate(interaction)


def call_adjudicator(
    backend: LLMBackend,
    system: str,
    user: str,
    interaction: str = "",
    timeout: float | None = None,
) -> dict:
    """
    Call the LLM adjudicator with a hard timeout.

    If the LLM call exceeds `timeout` seconds (default: SIM_V2_ADJ_TIMEOUT env var,
    fallback 8 s) the deterministic fallback is returned immediately.
    The LLM result is discarded when it eventually arrives.

    `interaction` is used only by the fallback for category inference.
    """
    max_tokens = int(os.environ.get("SIM_V2_ADJ_MAX_TOKENS", "400"))
    deadline = timeout if timeout is not None else _LLM_TIMEOUT

    def _call() -> dict:
        raw = backend.chat(
            system=system,
            user=user,
            max_tokens=max_tokens,
            schema=ADJUDICATOR_SCHEMA,
        )
        raw = _repair_json(raw)
        return json.loads(raw)

    fut = _timeout_pool.submit(_call)
    try:
        return fut.result(timeout=deadline)
    except concurrent.futures.TimeoutError:
        logger.warning("[Adjudicator] LLM timeout (%.1fs) — using deterministic fallback", deadline)
        fut.cancel()
        return _deterministic_fallback(interaction)
    except Exception as exc:
        logger.warning("[Adjudicator] LLM error (%s) — using deterministic fallback", exc)
        return _deterministic_fallback(interaction)
