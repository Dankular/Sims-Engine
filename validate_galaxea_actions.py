from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HF_REPO = "OpenGalaxea/Galaxea-Open-World-Dataset"
HF_TREE_URL = (
    "https://huggingface.co/api/datasets/"
    f"{HF_REPO}/tree/main/lerobot?recursive=false&expand=false"
)


VERB_INTENT_MAP: dict[str, str] = {
    "adjust": "utility",
    "arrange": "tidy",
    "boil": "cook",
    "check": "shopping",
    "clean": "clean",
    "collect": "collect",
    "connect": "repair",
    "cook": "cook",
    "cooking": "cook",
    "declutter": "tidy",
    "dispose": "trash",
    "dry": "laundry",
    "enter": "move",
    "exit": "move",
    "fold": "laundry",
    "hang": "laundry",
    "heat": "cook",
    "insert": "place",
    "iron": "laundry",
    "lift": "move",
    "make": "craft",
    "open": "open_close",
    "opening": "open_close",
    "organize": "tidy",
    "pack": "store",
    "pick": "pick_place",
    "place": "place",
    "plug": "utility",
    "pour": "serve",
    "pull": "move",
    "push": "move",
    "pushing": "move",
    "put": "store",
    "replace": "replace",
    "retrieving": "retrieve",
    "retrieval": "retrieve",
    "ring": "social",
    "serve": "serve",
    "sliding": "move",
    "sort": "tidy",
    "steam": "cook",
    "stir": "cook",
    "stir-fry": "cook",
    "stir-frying": "cook",
    "storage": "store",
    "store": "store",
    "switch": "utility",
    "take": "retrieve",
    "taking": "retrieve",
    "tidy": "tidy",
    "toast": "cook",
    "turn": "utility",
    "twist": "open_close",
    "uncap": "open_close",
    "use": "utility",
    "wash": "clean",
    "washing": "clean",
    "water": "serve",
    "wearing": "dress",
    "wipe": "clean",
}

VENUE_KEYWORDS: dict[str, str] = {
    "kitchen": "kitchen",
    "fridge": "kitchen",
    "refrigerator": "kitchen",
    "microwave": "kitchen",
    "air_fryer": "kitchen",
    "stir-fry": "kitchen",
    "rice": "kitchen",
    "pot": "kitchen",
    "bed": "home",
    "bedroom": "home",
    "sofa": "home",
    "toilet": "bathroom",
    "bathroom": "bathroom",
    "mirror": "bathroom",
    "desk": "office",
    "notebook": "office",
    "pen": "office",
    "landline": "office",
    "shelf": "retail_store",
    "checkout": "retail_store",
    "beverage": "retail_store",
    "shop": "retail_store",
    "store": "retail_store",
}

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "to",
    "of",
    "on",
    "in",
    "with",
    "for",
    "from",
    "up",
    "out",
    "at",
    "or",
}


def _hf_get_json(url: str, token: str, timeout: int = 60) -> Any:
    req = Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "TheSimsEngine/1.0"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_task_archives(token: str) -> list[str]:
    data = _hf_get_json(HF_TREE_URL, token=token)
    if not isinstance(data, list):
        raise ValueError("Unexpected HF API response for dataset tree")
    archives: list[str] = []
    for rec in data:
        if isinstance(rec, dict):
            path = str(rec.get("path", ""))
            if path.startswith("lerobot/") and path.endswith(".tar.gz"):
                archives.append(path)
    if not archives:
        raise ValueError("No task archives found under lerobot/")
    return archives


def canonicalize_task_name(task_name: str) -> str:
    name = task_name
    for suffix in (".tar.gz",):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    name = name.replace("__", "_")
    name = re.sub(r"\d{6,}", "", name)
    name = re.sub(r"(?:_\d{2,4})+$", "", name)
    name = re.sub(r"_+", "_", name).strip("_")
    fixes = {
        "0n": "On",
        "Turn_0n": "Turn_On",
        "Sstorage": "Storage",
        "sstorage": "Storage",
        "0250703": "",
    }
    for bad, good in fixes.items():
        name = name.replace(bad, good)
    name = re.sub(r"(?i)\btorage\b", "Storage", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name


def _title_to_action(title: str) -> str:
    return title.replace("_", " ").strip().lower()


def _tokenize_for_tags(title: str) -> set[str]:
    parts = [p.lower() for p in title.split("_") if p]
    return {p for p in parts if p not in STOPWORDS}


def _infer_intent(title: str) -> str:
    first = title.split("_", 1)[0].lower() if title else ""
    return VERB_INTENT_MAP.get(first, "utility")


def _infer_venue(tokens: set[str]) -> str:
    for key, venue in VENUE_KEYWORDS.items():
        if key in tokens:
            return venue
    return "home"


def _infer_sociality(tokens: set[str]) -> str:
    if {"ring", "checkout", "counter", "serve"} & tokens:
        return "social"
    return "solo"


def _infer_confidence(title: str, tokens: set[str]) -> float:
    conf = 0.6
    if title and title.split("_", 1)[0].lower() in VERB_INTENT_MAP:
        conf += 0.2
    if any(k in tokens for k in VENUE_KEYWORDS):
        conf += 0.1
    if any(ch.isdigit() for ch in title):
        conf -= 0.05
    return round(max(0.05, min(0.99, conf)), 2)


def build_action_catalog(archives: list[str]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    by_action: dict[str, dict[str, Any]] = {}
    for archive_path in archives:
        base = archive_path.split("/", 1)[-1]
        canonical = canonicalize_task_name(base)
        action_text = _title_to_action(canonical)
        tokens = _tokenize_for_tags(canonical)
        intent = _infer_intent(canonical)
        venue = _infer_venue(tokens)
        sociality = _infer_sociality(tokens)
        confidence = _infer_confidence(canonical, tokens)
        rec = {
            "source_archive": archive_path,
            "canonical_task": canonical,
            "action_text": action_text,
            "intent": intent,
            "venue_tag": venue,
            "sociality": sociality,
            "confidence": confidence,
            "tokens": sorted(tokens),
        }
        normalized.append(rec)

        key = action_text
        agg = by_action.setdefault(
            key,
            {
                "action_text": action_text,
                "intent": intent,
                "venue_tag": venue,
                "sociality": sociality,
                "confidence": confidence,
                "sources": [],
            },
        )
        agg["sources"].append(archive_path)
        agg["confidence"] = round(max(agg["confidence"], confidence), 2)

    verb_counts = Counter(
        rec["canonical_task"].split("_", 1)[0].lower()
        for rec in normalized
        if rec["canonical_task"]
    )
    intent_counts = Counter(rec["intent"] for rec in normalized)
    venue_counts = Counter(rec["venue_tag"] for rec in normalized)
    noisy = [
        rec
        for rec in normalized
        if "202" in rec["canonical_task"]
        or any(t in rec["canonical_task"].lower() for t in ("0n", "ss", "0250703"))
    ]

    return {
        "source": {
            "dataset": HF_REPO,
            "task_archive_count": len(archives),
        },
        "summary": {
            "normalized_actions": len(normalized),
            "unique_action_text": len(by_action),
            "top_verbs": verb_counts.most_common(20),
            "intent_distribution": dict(intent_counts),
            "venue_distribution": dict(venue_counts),
            "likely_noisy_entries": len(noisy),
        },
        "actions": sorted(by_action.values(), key=lambda x: x["action_text"]),
        "normalized_records": normalized,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and normalize Galaxea Open-World tasks into Sims action catalog."
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("HF_TOKEN", ""),
        help="Hugging Face token (or set HF_TOKEN env var)",
    )
    parser.add_argument(
        "--out",
        default="datasets/open_world_actions.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("Missing token. Pass --token or set HF_TOKEN.")

    try:
        archives = fetch_task_archives(args.token)
    except HTTPError as exc:
        raise SystemExit(f"HF API error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise SystemExit(f"Network error: {exc.reason}") from exc

    payload = build_action_catalog(archives)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    summary = payload["summary"]
    print(f"Saved action catalog to {out_path}")
    print(f"Archives: {len(archives)}")
    print(f"Unique action text: {summary['unique_action_text']}")
    print(f"Likely noisy entries: {summary['likely_noisy_entries']}")
    print(f"Top verbs: {summary['top_verbs'][:10]}")


if __name__ == "__main__":
    main()
