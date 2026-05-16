import json

from llm.schemas import LIFE_EVENT_SCHEMA


def run_life_event_llm(
    backend, system_prompt: str, sim_a, sim_b, event_type: str, context: str
) -> dict | None:
    sims_ctx = f"Sim A: {sim_a.name}"
    if sim_b:
        sims_ctx += f"; Sim B: {sim_b.name}"
    prompt = (
        f"Generate {event_type} for {sims_ctx}. Context: {context}. "
        "Return JSON with narrative,emotion_a,emotion_b,simoleon_delta_a,simoleon_delta_b,friendship_delta,romance_delta"
    )
    try:
        raw = backend.chat(
            system=system_prompt, user=prompt, max_tokens=250, temperature=0.85,
            schema=LIFE_EVENT_SCHEMA,
        )
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception:
        return None
