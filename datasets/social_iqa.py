from datasets.cache import cache_load


def load_social_iqa_index() -> dict:
    return cache_load("social_iqa_index") or {}


def sample_social_iqa(interaction: str) -> str | None:
    index = load_social_iqa_index()
    if not index:
        return None
    candidates = [
        entry
        for w in interaction.lower().split()
        if w in index
        for entry in index[w][:3]
    ]
    if not candidates:
        return None
    import random

    item = random.choice(candidates)
    if not isinstance(item, dict):
        return None
    return item.get("question")
