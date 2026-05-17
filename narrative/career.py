"""narrative/career.py — Career events wired through CareerManager."""
import json
import random

from world.careers import CAREER_CATALOGUE, career_from_job_title


def run_career_event(backend, system_prompt: str, sim) -> dict | None:
    career_id = getattr(sim, "career_id", None) or career_from_job_title(sim.profile.get("job", ""))
    career = CAREER_CATALOGUE.get(career_id)
    career_name = career.name if career else sim.profile.get("job", "unknown career")
    title = "Unknown"
    if career:
        lev = career.get_level(sim.career_level, getattr(sim, "career_branch", "base"))
        if lev:
            title = lev.title

    prompt = (
        "Return JSON with these keys for a career event. All values must be the exact types shown:\n"
        '  "event_type": string\n'
        '  "narrative": string (one sentence)\n'
        '  "performance_delta": number (e.g. 5 or -3)\n'
        '  "simoleon_delta": number (e.g. 200 or -50)\n'
        '  "emotion": string\n'
        '  "intensity": number between 0.0 and 1.0\n'
        '  "duration": integer number of ticks (e.g. 4)\n'
        f"Sim: {sim.name}, career: {career_name}, title: {title}, "
        f"level: {sim.career_level}, performance: {sim.career_performance:.0f}"
    )
    from llm.schemas import CAREER_EVENT_SCHEMA
    try:
        raw = backend.chat(
            system=system_prompt, user=prompt, max_tokens=250, temperature=0.85,
            schema=CAREER_EVENT_SCHEMA,
        )
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception:
        return None
