"""JSON schemas for grammar-constrained generation (llama-server response_format)."""

ADJUDICATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "dialogue":              {"type": "string"},
        "sim_b_reaction":        {"type": "string"},
        "friendship_delta":      {"type": "number"},
        "romance_delta":         {"type": "number"},
        "social_need_restore_a": {"type": "number"},
        "social_need_restore_b": {"type": "number"},
        "fun_restore_a":         {"type": "number"},
        "fun_restore_b":         {"type": "number"},
        "emotion_a":             {"type": "string"},
        "emotion_b":             {"type": "string"},
        "valence":               {"type": "number"},
        "memory_tag":            {"type": "string"},
        "charisma_xp_a":         {"type": "number"},
        "comedy_xp_a":           {"type": "number"},
        "reasoning":             {"type": "string"},
        # Optional life event suggestion — LLM can surface emergent events
        "suggested_event": {
            "type": "object",
            "properties": {
                "type":       {"type": "string"},   # EventType constant
                "narrative":  {"type": "string"},   # human-readable summary
                "visibility": {"type": "string"},   # Visibility constant
                "valence":    {"type": "number"},
                "intensity":  {"type": "number"},
            },
        },
    },
    "required": [
        "sim_b_reaction", "friendship_delta", "romance_delta",
        "social_need_restore_a", "social_need_restore_b",
        "fun_restore_a", "fun_restore_b",
        "emotion_a", "emotion_b", "valence",
        "memory_tag", "charisma_xp_a", "comedy_xp_a",
        # "reasoning" is intentionally optional — small models exhaust token budget
        # writing long reasoning text before closing the JSON object.
    ],
}

CAREER_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "event_type":        {"type": "string"},
        "narrative":         {"type": "string"},
        "performance_delta": {"type": "number"},
        "simoleon_delta":    {"type": "number"},
        "emotion":           {"type": "string"},
        "intensity":         {"type": "number"},
        "duration":          {"type": "integer"},
    },
    "required": [
        "event_type", "narrative", "performance_delta",
        "simoleon_delta", "emotion", "intensity", "duration",
    ],
}

LIFE_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative":         {"type": "string"},
        "emotion_a":         {"type": "string"},
        "emotion_b":         {"type": "string"},
        "simoleon_delta_a":  {"type": "number"},
        "simoleon_delta_b":  {"type": "number"},
        "friendship_delta":  {"type": "number"},
        "romance_delta":     {"type": "number"},
    },
    "required": [
        "narrative", "emotion_a", "emotion_b",
        "simoleon_delta_a", "simoleon_delta_b",
        "friendship_delta", "romance_delta",
    ],
}
