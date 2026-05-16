"""
Populate missing .sim_cache entries from HuggingFace datasets.

Usage:
    python -m sim_v2.datasets.populate
    python -m sim_v2.datasets.populate --only okcupid emotion convai2
"""
import argparse
import logging
from collections import defaultdict

from config import (
    HF_CONVAI2_DATASET,
    HF_EMOTION_DATASET,
    HF_OKCUPID_DATASET,
)
from datasets.cache import cache_load, cache_save

logger = logging.getLogger(__name__)

EMOTION_LABELS = {0: "sadness", 1: "joy", 2: "love", 3: "anger", 4: "fear", 5: "surprise"}
MAX_PER_EMOTION = 200
MAX_ESSAYS = 5000
MAX_CONVAI2 = 2000


def populate_okcupid() -> None:
    if cache_load("okcupid_essays"):
        logger.info("okcupid_essays already cached — skipping")
        return
    logger.info("Downloading %s …", HF_OKCUPID_DATASET)
    from datasets import load_dataset
    ds = load_dataset(HF_OKCUPID_DATASET, split="train", trust_remote_code=True)
    essay_cols = [c for c in ds.column_names if "essay" in c.lower() or "about" in c.lower() or "text" in c.lower()]
    if not essay_cols:
        essay_cols = ds.column_names[:3]
    essays = []
    for row in ds:
        for col in essay_cols:
            val = row.get(col, "")
            if isinstance(val, str) and len(val) > 40:
                essays.append(val.strip())
        if len(essays) >= MAX_ESSAYS:
            break
    cache_save("okcupid_essays", essays)
    logger.info("okcupid_essays: saved %d essays", len(essays))


def populate_emotion() -> None:
    if cache_load("emotion_calib"):
        logger.info("emotion_calib already cached — skipping")
        return
    logger.info("Downloading %s …", HF_EMOTION_DATASET)
    from datasets import load_dataset
    ds = load_dataset(HF_EMOTION_DATASET, split="train")
    buckets: dict[str, list[str]] = defaultdict(list)
    for row in ds:
        label = row.get("label")
        text = row.get("text", "")
        if label is None or not text:
            continue
        name = EMOTION_LABELS.get(int(label))
        if name and len(buckets[name]) < MAX_PER_EMOTION:
            buckets[name].append(text.strip())
    cache_save("emotion_calib", dict(buckets))
    logger.info("emotion_calib: saved %d emotions, %d total texts",
                len(buckets), sum(len(v) for v in buckets.values()))


def populate_convai2() -> None:
    existing = cache_load("convai2_seeds")
    if existing:
        logger.info("convai2_seeds already cached — skipping")
        return
    logger.info("Downloading %s …", HF_CONVAI2_DATASET)
    from datasets import load_dataset
    try:
        ds = load_dataset(HF_CONVAI2_DATASET, split="train", trust_remote_code=True)
    except Exception:
        ds = load_dataset(HF_CONVAI2_DATASET, "full", split="train", trust_remote_code=True)
    seeds = []
    for row in ds:
        dialog = row.get("dialog") or []
        if isinstance(dialog, list) and dialog:
            first = dialog[0]
            text = first.get("text", "") if isinstance(first, dict) else str(first)
            if text and len(text) > 5:
                seeds.append(text.strip())
        if len(seeds) >= MAX_CONVAI2:
            break
    cache_save("convai2_seeds", seeds)
    logger.info("convai2_seeds: saved %d seeds", len(seeds))


_POPULATORS = {
    "okcupid": populate_okcupid,
    "emotion": populate_emotion,
    "convai2": populate_convai2,
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Populate .sim_cache from HuggingFace datasets")
    parser.add_argument(
        "--only", nargs="*", choices=list(_POPULATORS), metavar="KEY",
        help="Populate only these keys (default: all missing)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if cache already exists"
    )
    args = parser.parse_args()

    targets = args.only or list(_POPULATORS)

    if args.force:
        from config import CACHE_DIR
        for key in targets:
            p = CACHE_DIR / f"{key}.json"
            if p.exists():
                p.unlink()
                logger.info("Cleared %s", p.name)

    for key in targets:
        _POPULATORS[key]()


if __name__ == "__main__":
    main()
