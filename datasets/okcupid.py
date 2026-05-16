from datasets.cache import cache_load


def load_okcupid_essays() -> list[str]:
    return cache_load("okcupid_essays") or []


def sample_okcupid_essay() -> str | None:
    essays = load_okcupid_essays()
    if not essays:
        return None
    import random

    return random.choice(essays)
