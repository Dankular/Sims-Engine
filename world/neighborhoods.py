from __future__ import annotations

from dataclasses import dataclass, field
import random


@dataclass
class Lot:
    lot_id: str
    size: tuple[int, int]
    type: str
    zoning: str
    ownership: str
    public_access: str
    occupants: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    venue_assignment: str = "generic"
    opening_hours: tuple[int, int] = (0, 24)


@dataclass
class Neighborhood:
    id: str
    lots: list[Lot] = field(default_factory=list)
    public_spaces: list[str] = field(default_factory=list)
    atmosphere: str = "neutral"
    traversal_rules: dict = field(
        default_factory=lambda: {
            "walking": True,
            "vehicle": True,
            "teleportation": True,
        }
    )
    npc_population: int = 0
    district_identity: dict = field(
        default_factory=lambda: {
            "wealth": 0.5,
            "culture": 0.5,
            "crime": 0.2,
            "supernatural_presence": 0.1,
            "tourism": 0.3,
        }
    )


@dataclass
class District:
    id: str
    neighborhoods: list[Neighborhood] = field(default_factory=list)
    climate: str = "temperate"
    terrain: str = "urban"


@dataclass
class WorldNode:
    id: str
    districts: list[District] = field(default_factory=list)
    climate: str = "temperate"
    terrain: str = "mixed"
    travel_rules: dict = field(
        default_factory=lambda: {"cost_factor": 1.0, "segmented_loading": True}
    )
    simulation_scope: str = "hybrid_streaming"


SERVICE_ROLES = [
    "cashier",
    "bartender",
    "janitor",
    "entertainer",
    "chef",
    "receptionist",
    "vendor",
    "security",
]
VENUE_TYPES = [
    "park",
    "gym",
    "nightclub",
    "library",
    "restaurant",
    "cafe",
    "spa",
    "museum",
    "beach",
    "cemetery",
    "retail_store",
    "school",
    "hospital",
    "police_station",
    "recreation_center",
]


class NeighborhoodSystem:
    def __init__(self) -> None:
        self.world = self._make_world()
        self.travel_log: list[dict] = []
        self.background_status: dict[str, dict] = {}
        self.businesses: dict[str, dict] = {}
        self.rentals: dict[str, dict] = {}
        self.hidden_lots: dict[str, dict] = {}
        self.service_npcs: list[dict] = []

    def _make_world(self) -> WorldNode:
        neighborhoods = []
        for nidx in range(3):
            lots = []
            for lidx in range(6):
                lot_type = random.choice(
                    ["residential", "community", "business", "rental", "hidden"]
                )
                zoning = lot_type
                ownership = (
                    "public_owned"
                    if lot_type in {"community", "hidden"}
                    else "household_owned"
                )
                access = (
                    "public"
                    if lot_type in {"community", "business"}
                    else "household_access"
                )
                venue = (
                    random.choice(VENUE_TYPES)
                    if lot_type in {"community", "business"}
                    else "generic"
                )
                lots.append(
                    Lot(
                        lot_id=f"lot_{nidx}_{lidx}",
                        size=(random.choice([20, 30, 40]), random.choice([15, 20, 30])),
                        type=lot_type,
                        zoning=zoning,
                        ownership=ownership,
                        public_access=access,
                        venue_assignment=venue,
                        opening_hours=(6, 23)
                        if lot_type in {"community", "business"}
                        else (0, 24),
                    )
                )
            neighborhoods.append(
                Neighborhood(
                    id=f"neighborhood_{nidx}",
                    lots=lots,
                    public_spaces=[f"square_{nidx}", f"greenway_{nidx}"],
                    atmosphere=random.choice(["quiet", "vibrant", "touristy", "artsy"]),
                    npc_population=random.randint(8, 25),
                )
            )
        d = District(id="district_0", neighborhoods=neighborhoods)
        return WorldNode(id="world_main", districts=[d])

    def tick(self, engine) -> None:
        self._assign_lots_to_households(engine)
        self._background_sim(engine)
        self._travel_sim(engine)
        self._service_spawn(engine)
        self._business_sim(engine)
        self._rental_sim(engine)
        self._hidden_lot_unlocks(engine)

    def _assign_lots_to_households(self, engine) -> None:
        res_lots = [
            l
            for n in self.world.districts[0].neighborhoods
            for l in n.lots
            if l.type in {"residential", "rental"}
        ]
        for hh in engine.households:
            if any(hh.id in l.occupants for l in res_lots):
                continue
            if not res_lots:
                break
            lot = random.choice(res_lots)
            if hh.id not in lot.occupants:
                lot.occupants.append(hh.id)

    def _background_sim(self, engine) -> None:
        for sim in engine.sims:
            if getattr(sim, "lod_tier", None) and str(sim.lod_tier).endswith("DORMANT"):
                sim.needs.energy = max(0.0, sim.needs.energy - 0.1)
                sim.career_performance = min(100.0, sim.career_performance + 0.05)
                self.background_status[sim.sim_id] = {
                    "need_decay": 0.1,
                    "work_progress": 0.05,
                    "relationship_updates": 0.0,
                    "skill_progress": 0.01,
                    "travel": False,
                }

    def _travel_sim(self, engine) -> None:
        methods = [
            "walking",
            "vehicle",
            "taxi",
            "bike",
            "teleportation",
            "subway",
            "broom",
            "boat",
        ]
        lots = [l for n in self.world.districts[0].neighborhoods for l in n.lots]
        if len(lots) < 2:
            return
        for sim in engine.sims:
            if random.random() < 0.03:
                origin = random.choice(lots)
                destination = random.choice(
                    [l for l in lots if l.lot_id != origin.lot_id]
                )
                method = random.choice(methods)
                travel_time = random.randint(1, 5)
                travel_cost = (
                    0.0 if method in {"walking", "bike"} else random.uniform(1.0, 8.0)
                )
                sim.simoleons = max(0.0, sim.simoleons - travel_cost)
                self.travel_log.append(
                    {
                        "tick": engine.tick_count,
                        "sim_id": sim.sim_id,
                        "origin": origin.lot_id,
                        "destination": destination.lot_id,
                        "method": method,
                        "travel_time": travel_time,
                        "travel_cost": round(travel_cost, 2),
                    }
                )

    def _service_spawn(self, engine) -> None:
        self.service_npcs.clear()
        for n in self.world.districts[0].neighborhoods:
            for lot in n.lots:
                if lot.type not in {"community", "business"}:
                    continue
                if random.random() < 0.5:
                    self.service_npcs.append(
                        {
                            "lot_id": lot.lot_id,
                            "role": random.choice(SERVICE_ROLES),
                            "spawn_conditions": {
                                "time_of_day": "day"
                                if random.random() < 0.7
                                else "night"
                            },
                            "work_schedule": list(lot.opening_hours),
                        }
                    )

    def _business_sim(self, engine) -> None:
        for sim in engine.sims:
            for biz in getattr(sim, "owned_businesses", []):
                rec = self.businesses.setdefault(
                    f"{sim.sim_id}:{biz}",
                    {
                        "owner": sim.sim_id,
                        "employees": random.randint(0, 4),
                        "revenue": 0.0,
                        "lot": random.choice(
                            [
                                l.lot_id
                                for n in self.world.districts[0].neighborhoods
                                for l in n.lots
                                if l.type in {"business", "community"}
                            ]
                            or ["none"]
                        ),
                        "operating_hours": [8, 22],
                    },
                )
                gain = random.uniform(10.0, 60.0)
                rec["revenue"] += gain
                sim.simoleons += gain * 0.2

    def _rental_sim(self, engine) -> None:
        rental_lots = [
            l
            for n in self.world.districts[0].neighborhoods
            for l in n.lots
            if l.type == "rental"
        ]
        for lot in rental_lots:
            rec = self.rentals.setdefault(
                lot.lot_id,
                {
                    "tenants": list(lot.occupants),
                    "lease_rules": {"duration": 30},
                    "landlord": "city",
                    "maintenance": random.uniform(0.0, 1.0),
                    "rent_value": round(random.uniform(50.0, 220.0), 2),
                },
            )
            rec["maintenance"] = max(
                0.0, min(1.0, rec["maintenance"] + random.uniform(-0.03, 0.03))
            )

    def _hidden_lot_unlocks(self, engine) -> None:
        hidden = [
            l
            for n in self.world.districts[0].neighborhoods
            for l in n.lots
            if l.type == "hidden"
        ]
        for lot in hidden:
            if lot.lot_id in self.hidden_lots:
                continue
            unlock = random.random() < 0.02
            self.hidden_lots[lot.lot_id] = {
                "unlock_conditions": "exploration or occult trigger",
                "special_spawns": ["rare_npc", "unique_resource"],
                "unlocked": unlock,
            }

    def state_dict(self) -> dict:
        return {
            "world": {
                "id": self.world.id,
                "climate": self.world.climate,
                "terrain": self.world.terrain,
                "travel_rules": dict(self.world.travel_rules),
                "simulation_scope": self.world.simulation_scope,
                "districts": [
                    {
                        "id": d.id,
                        "climate": d.climate,
                        "terrain": d.terrain,
                        "neighborhoods": [
                            {
                                "id": n.id,
                                "atmosphere": n.atmosphere,
                                "npc_population": n.npc_population,
                                "district_identity": dict(n.district_identity),
                                "public_spaces": list(n.public_spaces),
                                "lots": [
                                    {
                                        "id": l.lot_id,
                                        "size": list(l.size),
                                        "type": l.type,
                                        "zoning": l.zoning,
                                        "ownership": l.ownership,
                                        "public_access": l.public_access,
                                        "occupants": list(l.occupants),
                                        "venue_assignment": l.venue_assignment,
                                        "opening_hours": list(l.opening_hours),
                                    }
                                    for l in n.lots
                                ],
                            }
                            for n in d.neighborhoods
                        ],
                    }
                    for d in self.world.districts
                ],
            },
            "travel_log": list(self.travel_log[-30:]),
            "background_status": dict(self.background_status),
            "service_npcs": list(self.service_npcs),
            "businesses": dict(self.businesses),
            "rentals": dict(self.rentals),
            "hidden_lots": dict(self.hidden_lots),
        }
