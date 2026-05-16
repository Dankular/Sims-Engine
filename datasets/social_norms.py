from config import SOCIAL_NORMS_COUNT
from datasets.cache import cache_load


def load_social_norms() -> list[str]:
    return cache_load("social_norms") or []


def sample_norms(n: int = SOCIAL_NORMS_COUNT) -> list[str]:
    pool = load_social_norms()
    if not pool:
        return []
    import random

    return random.sample(pool, min(n, len(pool)))
