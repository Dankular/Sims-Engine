from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode


def fetch_items(api_key: str, sort: str = "ASC") -> dict:
    base = "https://api.torn.com/v2/torn/items"
    params = urlencode({"sort": sort})
    url = f"{base}?{params}"
    req = Request(
        url,
        headers={
            "Authorization": f"ApiKey {api_key}",
            "User-Agent": "TheSimsEngine/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=60) as resp:
            payload = resp.read().decode("utf-8")
        return json.loads(payload)
    except Exception:
        # Compatibility fallback for query-string key style.
        fallback = f"{base}?{urlencode({'sort': sort, 'key': api_key})}"
        with urlopen(
            Request(fallback, headers={"User-Agent": "TheSimsEngine/1.0"}), timeout=60
        ) as resp:
            payload = resp.read().decode("utf-8")
        return json.loads(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Torn items into local dataset")
    parser.add_argument("--key", required=True, help="Torn API key")
    parser.add_argument("--sort", default="ASC", choices=["ASC", "DESC"])
    parser.add_argument("--out", default="datasets/torn_items.json")
    args = parser.parse_args()

    data = fetch_items(args.key, args.sort)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")

    count = len(data.get("items", [])) if isinstance(data, dict) else 0
    print(f"Saved {count} items to {out}")


if __name__ == "__main__":
    main()
