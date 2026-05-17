from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SECTION_RE = re.compile(r"^\[(\d+(?:\.\d+)*)\]\s+(.*)$")
PRICE_RE = re.compile(r"^§([\d,]+)$")


def parse_items_txt(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    current_section = "Misc"
    entries: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        sec = SECTION_RE.match(line)
        if sec:
            current_section = sec.group(2).strip()
            i += 1
            continue
        if not line:
            i += 1
            continue

        if i + 1 < len(lines):
            p = PRICE_RE.match(lines[i + 1].strip())
            if p:
                name = line
                price = float(p.group(1).replace(",", ""))
                desc_lines = []
                j = i + 2
                while j < len(lines):
                    nxt = lines[j].strip()
                    if not nxt:
                        break
                    if SECTION_RE.match(nxt):
                        break
                    if PRICE_RE.match(nxt):
                        break
                    desc_lines.append(nxt)
                    j += 1
                desc = " ".join(desc_lines)[:500]
                entries.append(
                    {
                        "name": name,
                        "price": price,
                        "section": current_section,
                        "description": desc,
                    }
                )
                i = j
                continue
        i += 1
    return entries


def merge_into_catalog(catalog_path: Path, parsed: list[dict]) -> dict:
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        items = []

    existing_names = {str(it.get("name", "")).strip().lower() for it in items}
    max_id = max((int(it.get("id", 0) or 0) for it in items), default=0)
    next_id = max_id + 1
    added = 0

    for rec in parsed:
        key = rec["name"].strip().lower()
        if not key or key in existing_names:
            continue
        item = {
            "id": next_id,
            "name": rec["name"],
            "description": rec["description"],
            "effect": "",
            "requirement": "",
            "image": "",
            "type": "Furniture",
            "sub_type": rec["section"],
            "is_masked": False,
            "is_tradable": True,
            "is_found_in_city": False,
            "value": {
                "vendor": {"country": "SimNation", "name": "BuildBuy"},
                "buy_price": rec["price"],
                "sell_price": rec["price"] * 0.55,
                "market_price": rec["price"],
            },
            "circulation": 0,
            "details": {"category": rec["section"], "source": "items_txt"},
        }
        items.append(item)
        existing_names.add(key)
        next_id += 1
        added += 1

    payload["items"] = items
    payload["enrichment"] = {
        "source": "items.txt",
        "added": added,
        "total": len(items),
    }
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich Torn items catalog with items.txt")
    ap.add_argument("--items-txt", default=r"C:\Users\sysadmin\Desktop\items.txt")
    ap.add_argument("--catalog", default="datasets/torn_items.json")
    ap.add_argument("--out", default="datasets/torn_items.json")
    args = ap.parse_args()

    txt_path = Path(args.items_txt)
    cat_path = Path(args.catalog)
    out_path = Path(args.out)

    parsed = parse_items_txt(txt_path)
    merged = merge_into_catalog(cat_path, parsed)
    out_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(
        f"Parsed {len(parsed)} entries from {txt_path}; total catalog now {len(merged.get('items', []))}."
    )


if __name__ == "__main__":
    main()
