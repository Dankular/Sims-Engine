from datasets.cache import cache_load


def load_empath_index() -> dict:
    return cache_load("empath_index") or {}


def sample_empathetic_utterance(
    emotion: str, index: dict | None = None
) -> str | None:
    import random
    idx = index if index is not None else load_empath_index()
    if not idx:
        return None
    pool = idx.get(emotion.lower(), [])
    if not pool:
        pool = [line for lines in idx.values() for line in lines]
    return random.choice(pool) if pool else None
