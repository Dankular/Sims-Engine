"""Long-form relationship drama scaffolds from BORU updates."""

from __future__ import annotations

import random

from datasets.cache import cache_load, cache_save

_CACHE_KEY = "boru_arcs"


def load_boru_arcs() -> list[dict]:
    cached = cache_load(_CACHE_KEY)
    if cached:
        return cached
    arcs: list[dict] = []
    try:
        from datasets import load_dataset

        ds = load_dataset(
            "derek-thomas/processed-bestofredditorupdates",
            split="train",
            streaming=True,
            trust_remote_code=True,
        )
        for row in ds:
            if len(arcs) >= 800:
                break
            p1 = str(
                row.get("part_1") or row.get("post") or row.get("original") or ""
            ).strip()
            upd = str(row.get("update") or row.get("part_2") or "").strip()
            fin = str(row.get("final_update") or row.get("final") or "").strip()
            if len(p1) < 40 or len(upd) < 20:
                continue
            arcs.append(
                {
                    "inciting": p1[:320],
                    "escalation": upd[:320],
                    "resolution": fin[:320]
                    if fin
                    else "Open ending with uncertain resolution.",
                }
            )
    except Exception:
        pass
    if arcs:
        cache_save(_CACHE_KEY, arcs)
    return arcs


def sample_arc() -> dict | None:
    arcs = load_boru_arcs()
    return random.choice(arcs) if arcs else None
