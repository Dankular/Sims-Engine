from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random


@dataclass
class PetRecord:
    pet_id: str
    name: str
    species: str
    rarity: str
    value: float
    hunger: float = 70.0
    fun: float = 65.0
    energy: float = 70.0
    cleanliness: float = 75.0
    mood: str = "content"
    bond: float = 40.0
    neglect_ticks: int = 0
    recovery_ticks: int = 0


class PetManager:
    def __init__(self) -> None:
        self.catalog: list[dict] = self._load_catalog()
        self._id_counter = 0
        self._bowl_food_by_lot: dict[str, float] = {}

    def _load_catalog(self) -> list[dict]:
        p = Path("datasets/openpets_catalog.json")
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("pets"), list):
                    rows = [
                        r
                        for r in data["pets"]
                        if isinstance(r, dict) and r.get("species")
                    ]
                    if rows:
                        return rows
            except Exception:
                pass
        return self._seed_catalog()

    def _seed_catalog(self) -> list[dict]:
        # OpenPets ingestion placeholder catalog (can be overwritten by importer)
        return [
            {"species": "cat", "rarity": "common", "value": 120.0},
            {"species": "dog", "rarity": "common", "value": 140.0},
            {"species": "fox", "rarity": "uncommon", "value": 260.0},
            {"species": "owl", "rarity": "uncommon", "value": 300.0},
            {"species": "dragonling", "rarity": "rare", "value": 900.0},
            {"species": "slime", "rarity": "common", "value": 80.0},
        ]

    def list_catalog(self) -> list[dict]:
        return list(self.catalog)

    def adopt_pet(self, sim, species: str | None = None) -> dict:
        choices = [
            c for c in self.catalog if species is None or c["species"] == species
        ]
        if not choices:
            return {"ok": False, "reason": "no_species_match"}
        row = random.choice(choices)
        pet = self._new_pet(row)
        self._attach_pet(sim, pet)
        return {"ok": True, "pet": self._pet_state(pet), "cost": 0.0, "mode": "adopt"}

    def buy_pet(self, sim, species: str | None = None) -> dict:
        choices = [
            c for c in self.catalog if species is None or c["species"] == species
        ]
        if not choices:
            return {"ok": False, "reason": "no_species_match"}
        row = random.choice(choices)
        cost = float(row.get("value", 0.0))
        if float(getattr(sim, "simoleons", 0.0)) < cost:
            return {"ok": False, "reason": "insufficient_funds"}
        sim.simoleons -= cost
        pet = self._new_pet(row)
        self._attach_pet(sim, pet)
        return {
            "ok": True,
            "pet": self._pet_state(pet),
            "cost": round(cost, 2),
            "mode": "buy",
            "simoleons": round(sim.simoleons, 2),
        }

    def feed_pet(self, sim, pet_id: str) -> dict:
        pets = getattr(sim, "pet_records", {})
        pet = pets.get(pet_id)
        if pet is None:
            return {"ok": False, "reason": "pet_not_found"}
        pet.hunger = min(100.0, pet.hunger + 28.0)
        pet.mood = "happy"
        pet.bond = min(100.0, pet.bond + 4.0)
        return {"ok": True, "pet": self._pet_state(pet)}

    def pet_pet(self, sim, pet_id: str) -> dict:
        pets = getattr(sim, "pet_records", {})
        pet = pets.get(pet_id)
        if pet is None:
            return {"ok": False, "reason": "pet_not_found"}
        pet.bond = min(100.0, pet.bond + 7.0)
        pet.fun = min(100.0, pet.fun + 4.0)
        pet.mood = "loved"
        return {"ok": True, "pet": self._pet_state(pet)}

    def play_with_pet(self, sim, pet_id: str) -> dict:
        pets = getattr(sim, "pet_records", {})
        pet = pets.get(pet_id)
        if pet is None:
            return {"ok": False, "reason": "pet_not_found"}
        pet.fun = min(100.0, pet.fun + 26.0)
        pet.energy = max(0.0, pet.energy - 10.0)
        pet.bond = min(100.0, pet.bond + 5.0)
        pet.mood = "playful"
        return {"ok": True, "pet": self._pet_state(pet)}

    def refill_food_bowl(self, sim, lot_layout, lot_id: str) -> dict:
        if not self._has_pet_bowl(lot_layout, lot_id):
            return {"ok": False, "reason": "no_pet_bowl"}
        cost = 18.0
        if float(getattr(sim, "simoleons", 0.0)) < cost:
            return {"ok": False, "reason": "insufficient_funds"}
        sim.simoleons -= cost
        self._bowl_food_by_lot[lot_id] = min(
            100.0, self._bowl_food_by_lot.get(lot_id, 0.0) + 45.0
        )
        return {
            "ok": True,
            "lot_id": lot_id,
            "bowl_food": round(self._bowl_food_by_lot.get(lot_id, 0.0), 1),
            "cost": cost,
            "simoleons": round(sim.simoleons, 2),
        }

    def tick(self, engine) -> None:
        for sim in engine.sims:
            lot_id = str(getattr(sim, "household_id", "") or "")
            pets = list(getattr(sim, "pet_records", {}).values())
            if not pets:
                continue
            for pet in list(getattr(sim, "pet_records", {}).values()):
                pet.hunger = max(0.0, pet.hunger - 0.8)
                pet.fun = max(0.0, pet.fun - 0.55)
                pet.energy = max(0.0, pet.energy - 0.45)
                pet.cleanliness = max(0.0, pet.cleanliness - 0.35)

                if self._has_pet_bowl(engine.lot_layout, lot_id):
                    available = self._bowl_food_by_lot.get(lot_id, 0.0)
                    if pet.hunger < 45 and available > 0:
                        eat = min(8.0, available)
                        self._bowl_food_by_lot[lot_id] = max(0.0, available - eat)
                        pet.hunger = min(100.0, pet.hunger + eat * 2.0)

                if pet.hunger < 20:
                    sim.emotion.add("concern", 0.25, duration=2, source="pet_hungry")
                    pet.neglect_ticks += 1
                else:
                    pet.neglect_ticks = max(0, pet.neglect_ticks - 1)

                # Autonomous care behavior (can be disabled for deterministic tests)
                if not bool(getattr(sim, "disable_pet_autocare", False)):
                    if pet.hunger < 35:
                        self.feed_pet(sim, pet.pet_id)
                    elif pet.fun < 25:
                        self.play_with_pet(sim, pet.pet_id)
                    elif pet.bond < 35:
                        self.pet_pet(sim, pet.pet_id)

                # Neglect consequences and recovery arcs
                if pet.neglect_ticks >= 10:
                    pet.bond = max(0.0, pet.bond - 2.5)
                    sim.emotion.add("guilt", 0.35, duration=3, source="pet_neglect")
                if pet.neglect_ticks >= 22:
                    # runaway event
                    sim.pet_ids = [x for x in sim.pet_ids if not x.endswith(pet.pet_id)]
                    sim.pet_records.pop(pet.pet_id, None)
                    sim.emotion.add("sadness", 0.7, duration=8, source="pet_ran_away")
                    continue

                if pet.hunger > 70 and pet.fun > 70 and pet.cleanliness > 65:
                    pet.recovery_ticks += 1
                    if pet.recovery_ticks % 6 == 0:
                        pet.bond = min(100.0, pet.bond + 2.0)
                        sim.emotion.add("pride", 0.2, duration=2, source="pet_recovery")
                else:
                    pet.recovery_ticks = max(0, pet.recovery_ticks - 1)

                pet.mood = self._derive_mood(pet)

    def _new_pet(self, row: dict) -> PetRecord:
        self._id_counter += 1
        pid = f"pet_{self._id_counter:05d}"
        species = str(row.get("species", "pet"))
        return PetRecord(
            pet_id=pid,
            name=f"{species.title()} {self._id_counter}",
            species=species,
            rarity=str(row.get("rarity", "common")),
            value=float(row.get("value", 0.0)),
        )

    def _attach_pet(self, sim, pet: PetRecord) -> None:
        if not hasattr(sim, "pet_records"):
            sim.pet_records = {}
        sim.pet_records[pet.pet_id] = pet
        sim.pet_ids.append(f"{pet.species}:{pet.pet_id}")

    def _pet_state(self, pet: PetRecord) -> dict:
        return {
            "pet_id": pet.pet_id,
            "name": pet.name,
            "species": pet.species,
            "rarity": pet.rarity,
            "value": round(pet.value, 2),
            "hunger": round(pet.hunger, 1),
            "fun": round(pet.fun, 1),
            "energy": round(pet.energy, 1),
            "cleanliness": round(pet.cleanliness, 1),
            "mood": pet.mood,
            "bond": round(pet.bond, 1),
            "neglect_ticks": int(pet.neglect_ticks),
            "recovery_ticks": int(pet.recovery_ticks),
        }

    def bowl_state(self, lot_id: str) -> dict:
        return {
            "lot_id": lot_id,
            "bowl_food": round(self._bowl_food_by_lot.get(lot_id, 0.0), 1),
        }

    def _has_pet_bowl(self, lot_layout, lot_id: str) -> bool:
        if not lot_id:
            return False
        layout = lot_layout._placements.get(lot_id, {})
        for items in layout.values():
            for item in items:
                name = str(item.get("name", "")).lower()
                sub = str(item.get("sub_type", "")).lower()
                typ = str(item.get("type", "")).lower()
                if (
                    "bowl" in name
                    or sub in {"pet", "pet_bowl", "pet_feeder"}
                    or "feeder" in name
                    or typ in {"pet", "pet_supply"}
                ):
                    return True
        return False

    def _derive_mood(self, pet: PetRecord) -> str:
        if pet.hunger < 20:
            return "hungry"
        if pet.fun < 25:
            return "bored"
        if pet.energy < 20:
            return "sleepy"
        if pet.cleanliness < 30:
            return "dirty"
        if pet.bond > 80:
            return "affectionate"
        return "content"
