"""JSON schemas passed to Ollama's format field for grammar-constrained generation."""

ADJUDICATOR_SCHEMA = {
    "type": "object",
    "properties": {
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
    },
    "required": [
        "sim_b_reaction", "friendship_delta", "romance_delta",
        "social_need_restore_a", "social_need_restore_b",
        "fun_restore_a", "fun_restore_b",
        "emotion_a", "emotion_b", "valence",
        "memory_tag", "charisma_xp_a", "comedy_xp_a", "reasoning",
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
