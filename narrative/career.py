import json

from llm.schemas import CAREER_EVENT_SCHEMA


def run_career_event(backend, system_prompt: str, sim) -> dict | None:
    prompt = (
        "Return JSON with these keys for a career event. All values must be the exact types shown:\n"
        '  "event_type": string\n'
        '  "narrative": string (one sentence)\n'
        '  "performance_delta": number (e.g. 5 or -3)\n'
        '  "simoleon_delta": number (e.g. 200 or -50)\n'
        '  "emotion": string\n'
        '  "intensity": number between 0.0 and 1.0\n'
        '  "duration": integer number of ticks (e.g. 4)\n'
        f"Sim: {sim.name}, job: {sim.profile['job']}"
    )
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
