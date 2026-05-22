from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile


REPO = "https://github.com/alvinunreal/openpets"


def main() -> int:
    datasets = Path("datasets")
    datasets.mkdir(exist_ok=True)
    out_file = datasets / "openpets_catalog.json"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "openpets"
        subprocess.run(["git", "clone", "--depth", "1", REPO, str(root)], check=True)

        fixture = root / "apps" / "desktop" / "catalog.v2.fixture.json"
        if not fixture.exists():
            raise RuntimeError("OpenPets fixture catalog not found")

        payload = json.loads(fixture.read_text(encoding="utf-8"))
        pets = payload.get("pets", []) if isinstance(payload, dict) else []
        if not isinstance(pets, list):
            pets = []

        norm: list[dict] = []
        elite_kw = {"dragon", "phoenix", "myth", "legend", "spirit", "demon"}
        rare_kw = {"wizard", "robot", "ghost", "alien", "vampire"}

        for row in pets:
            if not isinstance(row, dict):
                continue
            species = str(row.get("id", "")).strip().lower()
            if not species:
                continue
            display_name = str(row.get("displayName", species)).strip()
            text = f"{species} {display_name}".lower()

            rarity = "common"
            value = 120.0
            if any(k in text for k in elite_kw):
                rarity = "rare"
                value = 900.0
            elif any(k in text for k in rare_kw):
                rarity = "uncommon"
                value = 320.0

            norm.append(
                {
                    "species": species,
                    "display_name": display_name,
                    "rarity": rarity,
                    "value": value,
                    "description": str(row.get("description", "")),
                    "preview": str(row.get("preview", "")),
                    "zip": str(row.get("zip", "")),
                }
            )

    final = {"pets": sorted(norm, key=lambda x: x["species"])}
    out_file.write_text(json.dumps(final, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "pets": len(final["pets"]), "out": str(out_file)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
