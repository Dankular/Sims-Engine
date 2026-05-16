from datasets.cache import cache_load


def load_convai2_seeds() -> list[str]:
    return cache_load("convai2_seeds") or []
