from datasets.cache import cache_load


def load_dialogue_actions() -> list[str]:
    return cache_load("dialogue_actions") or []


def sample_dialogue_action() -> str | None:
    actions = load_dialogue_actions()
    if not actions:
        return None
    import random

    return random.choice(actions)
