from datasets.cache import cache_load


def load_social_iqa_index() -> dict:
    return cache_load("social_iqa_index") or {}


def sample_social_iqa(interaction: str) -> str | None:
    """
    Return a social IQA scenario as 'context → question → answer' so the model
    gets a complete social reasoning example, not a bare decontextualised question.
    Requires at least 2 keyword matches to avoid noise from single-word accidents.
    """
    index = load_social_iqa_index()
    if not index:
        return None

    import random
    words = [w for w in interaction.lower().split() if len(w) > 3]

    # Score candidates by number of matched keywords — require ≥2 hits
    scored: dict[int, list] = {}
    for w in words:
        for entry in index.get(w, [])[:5]:
            h = id(entry) if not isinstance(entry, dict) else hash(entry.get("context", ""))
            scored.setdefault(h, [entry, 0])
            scored[h][1] += 1

    good = [v[0] for v in scored.values() if v[1] >= 2]
    if not good:
        # Relax to 1 hit but only if we have a long interaction phrase
        good = [v[0] for v in scored.values()] if len(words) >= 3 else []
    if not good:
        return None

    item = random.choice(good)
    if not isinstance(item, dict):
        return None

    context = item.get("context", "").strip()
    question = item.get("question", "").strip()
    label    = str(item.get("label", "1"))
    answer   = item.get({"1": "answerA", "2": "answerB", "3": "answerC"}.get(label, "answerA"), "")

    if not context or not question:
        return None
    if answer:
        return f'"{context}" — {question} → "{answer}"'
    return f'"{context}" — {question}'
