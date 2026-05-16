from datasets.cache import cache_load


def load_atomic_index() -> dict:
    return cache_load("atomic_index") or {}


def query_atomic(interaction: str) -> str | None:
    index = load_atomic_index()
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
    return item.get("event") if isinstance(item, dict) else None
