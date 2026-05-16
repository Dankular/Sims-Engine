import json

from config import CACHE_DIR


def _cache_path(key: str):  # used by populate.py
    return CACHE_DIR / f"{key}.json"


def cache_load(key: str):
    path = _cache_path(key)
    if path.exists():
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    return None


def cache_save(key: str, data) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    with _cache_path(key).open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, separators=(",", ":"))


def clear_dataset_cache() -> None:
    if CACHE_DIR.exists():
        for file in CACHE_DIR.glob("*.json"):
            file.unlink()
