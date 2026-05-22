"""
engine/spatial.py — Spatial travel system that makes geography matter.

Manages per-sim lot/district locations, a travel queue, and migration logic.
The TravelSystem is designed to be instantiated inside SimEngine.__init__
and ticked each run_tick() call via .tick(engine).

Scheduler integration:
  engine.spatial.proximity_score(sim_a_id, sim_b_id) → float
  Used by pick_interaction_pair() / choose_interaction() to weight nearby sims.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

# ── Travel record ─────────────────────────────────────────────────────────────

@dataclass
class TravelRecord:
    sim_id: str
    origin_lot: str
    destination_lot: str
    destination_district: str
    arrival_tick: int
    purpose: str  # "work", "social", "migration", "leisure"


# ── Travel system ─────────────────────────────────────────────────────────────

class TravelSystem:
    """
    Spatial travel system.  Tracks which lot / district every sim occupies,
    routes in-transit sims via a tick-based travel queue, and runs periodic
    migration checks so sims flee low-desirability districts.

    Lifecycle
    ---------
    1. Instantiate: ``self.spatial = TravelSystem()``
    2. Call ``self.spatial.tick(engine)`` from ``SimEngine.run_tick()``.
       On the very first tick, ``initialize_from_engine()`` is called automatically.

    Scheduler hook
    --------------
    ``proximity_score(sim_a_id, sim_b_id)`` returns a 0..1 float that the
    scheduler multiplies against pair-selection weights.
    """

    # Travel cost constants (ticks)
    TRAVEL_COST_SAME_DISTRICT: int = 1
    TRAVEL_COST_ADJ_DISTRICT: int = 3
    TRAVEL_COST_FAR_DISTRICT: int = 6

    # Migration thresholds
    MIGRATION_DESIRABILITY_THRESHOLD: float = 0.35
    MIGRATION_CHECK_INTERVAL: int = 30
    MIGRATION_WEALTH_MIN: float = 500.0
    MIGRATION_CHANCE: float = 0.20

    # History cap
    _MIGRATION_LOG_MAX: int = 50

    def __init__(self) -> None:
        # district_id → {neighbor_district_id: travel_cost}
        self._district_adjacency: dict[str, dict[str, float]] = {}
        # sim_id → lot_id
        self._sim_locations: dict[str, str] = {}
        # sim_id → district_id
        self._sim_districts: dict[str, str] = {}
        # in-transit sims
        self._travel_queue: list[TravelRecord] = []
        # last N migration events
        self._migration_log: list[dict] = []

        # Internal helpers rebuilt on first tick
        self._lot_to_district: dict[str, str] = {}   # lot_id → district_id
        self._lot_to_neighborhood: dict[str, str] = {}  # lot_id → neighborhood_id
        # neighborhood_id → district_id
        self._neighborhood_to_district: dict[str, str] = {}
        # district_id → district_identity dict
        self._district_identity: dict[str, dict] = {}
        # district_id → list[lot_id]
        self._district_lots: dict[str, list[str]] = {}
        # work-venue lots (venue_assignment in {"business", "community"})
        self._work_lots: list[str] = []

        self._initialized: bool = False

    # ── Initialization ────────────────────────────────────────────────────────

    def initialize_from_engine(self, engine: "SimEngine") -> None:
        """
        Read engine.neighborhoods to build adjacency graph and assign each sim
        an initial lot.  Called automatically on the first tick.
        """
        ns = getattr(engine, "neighborhoods", None)
        if ns is None:
            logger.warning("spatial: no neighborhoods system found — skipping init")
            self._initialized = True
            return

        # Walk the world graph to gather lots, neighborhoods, and districts
        all_district_ids: list[str] = []
        for district in ns.world.districts:
            d_id = district.id
            all_district_ids.append(d_id)
            self._district_lots[d_id] = []

            # Aggregate identity across neighborhoods (average)
            identity_acc: dict[str, float] = {}
            identity_count = 0
            for neighborhood in district.neighborhoods:
                n_id = neighborhood.id
                self._neighborhood_to_district[n_id] = d_id
                for lot in neighborhood.lots:
                    self._lot_to_district[lot.lot_id] = d_id
                    self._lot_to_neighborhood[lot.lot_id] = n_id
                    self._district_lots[d_id].append(lot.lot_id)
                    if lot.venue_assignment in {"business", "community"}:
                        self._work_lots.append(lot.lot_id)
                # Accumulate district_identity
                for k, v in neighborhood.district_identity.items():
                    identity_acc[k] = identity_acc.get(k, 0.0) + v
                identity_count += 1

            if identity_count:
                self._district_identity[d_id] = {
                    k: v / identity_count for k, v in identity_acc.items()
                }
            else:
                self._district_identity[d_id] = {
                    "wealth": 0.5, "culture": 0.5,
                    "crime": 0.2, "tourism": 0.3,
                }

        # Build adjacency: ring topology with cross-links for even indices
        n = len(all_district_ids)
        for i, d_id in enumerate(all_district_ids):
            self._district_adjacency[d_id] = {}
            for j, other_id in enumerate(all_district_ids):
                if i == j:
                    continue
                dist = min(abs(i - j), n - abs(i - j))  # ring distance
                if dist == 1:
                    cost = self.TRAVEL_COST_ADJ_DISTRICT
                elif i % 2 == 0 and j % 2 == 0:
                    # Cross-links between even-indexed districts
                    cost = self.TRAVEL_COST_ADJ_DISTRICT
                else:
                    cost = self.TRAVEL_COST_FAR_DISTRICT
                self._district_adjacency[d_id][other_id] = float(cost)

        # Assign each sim a starting lot
        all_lots = list(self._lot_to_district.keys())
        if not all_lots:
            logger.warning("spatial: no lots found in world — sims have no location")
            self._initialized = True
            return

        for sim in engine.sims:
            # Respect current_lot_id if already set by NeighborhoodSystem
            existing = getattr(sim, "current_lot_id", None)
            if existing and existing in self._lot_to_district:
                lot_id = existing
            else:
                lot_id = random.choice(all_lots)
                sim.current_lot_id = lot_id
            self._sim_locations[sim.sim_id] = lot_id
            self._sim_districts[sim.sim_id] = self._lot_to_district[lot_id]

        self._initialized = True
        logger.debug(
            "spatial: initialized — %d districts, %d lots, %d sims placed",
            len(all_district_ids), len(all_lots), len(engine.sims),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def proximity_score(self, sim_a_id: str, sim_b_id: str) -> float:
        """
        Return a 0..1 score reflecting how close two sims are geographically.
        Used by the scheduler to weight interaction pair selection.

          1.0  — same lot
          0.7  — same neighborhood
          0.4  — same district
          0.1  — different district
        """
        lot_a = self._sim_locations.get(sim_a_id)
        lot_b = self._sim_locations.get(sim_b_id)
        if lot_a is None or lot_b is None:
            return 0.5  # unknown → neutral

        if lot_a == lot_b:
            return 1.0

        nbr_a = self._lot_to_neighborhood.get(lot_a)
        nbr_b = self._lot_to_neighborhood.get(lot_b)
        if nbr_a and nbr_b and nbr_a == nbr_b:
            return 0.7

        dist_a = self._sim_districts.get(sim_a_id)
        dist_b = self._sim_districts.get(sim_b_id)
        if dist_a and dist_b and dist_a == dist_b:
            return 0.4

        return 0.1

    def travel_cost(self, origin_district: str, dest_district: str) -> int:
        """Return tick count needed to travel between two districts."""
        if origin_district == dest_district:
            return self.TRAVEL_COST_SAME_DISTRICT
        adj = self._district_adjacency.get(origin_district, {})
        cost = adj.get(dest_district)
        if cost is not None:
            return int(cost)
        return self.TRAVEL_COST_FAR_DISTRICT

    def enqueue_travel(
        self,
        sim_id: str,
        destination_lot: str,
        destination_district: str,
        purpose: str,
        current_tick: int,
    ) -> None:
        """Add a sim to the travel queue; it will arrive after travel_cost ticks."""
        origin_lot = self._sim_locations.get(sim_id, "")
        origin_district = self._sim_districts.get(sim_id, "")
        cost = self.travel_cost(origin_district, destination_district)
        record = TravelRecord(
            sim_id=sim_id,
            origin_lot=origin_lot,
            destination_lot=destination_lot,
            destination_district=destination_district,
            arrival_tick=current_tick + cost,
            purpose=purpose,
        )
        self._travel_queue.append(record)
        logger.debug(
            "spatial: %s enqueued for %s travel → lot %s (arrives tick %d)",
            sim_id, purpose, destination_lot, record.arrival_tick,
        )

    def get_sim_location(self, sim_id: str) -> dict:
        """Return {'lot_id': ..., 'district_id': ...} for a sim."""
        return {
            "lot_id": self._sim_locations.get(sim_id, ""),
            "district_id": self._sim_districts.get(sim_id, ""),
        }

    def district_population(self, district_id: str) -> int:
        """Count how many sims are currently in a given district."""
        return sum(
            1 for d in self._sim_districts.values() if d == district_id
        )

    def summary(self) -> dict:
        """Lightweight summary for get_state() or /timings endpoints."""
        return {
            "sim_locations_count": len(self._sim_locations),
            "travel_queue_length": len(self._travel_queue),
            "recent_migrations": list(self._migration_log[-10:]),
            "district_populations": {
                d_id: self.district_population(d_id)
                for d_id in self._district_adjacency
            },
        }

    # ── Tick ─────────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        """
        Main per-tick update:
          1. Lazy initialization on first tick.
          2. Drain arrived sims from the travel queue.
          3. Periodic migration checks.
          4. Every 5 ticks: route employed sims toward work venues.
        """
        if not self._initialized:
            self.initialize_from_engine(engine)

        tick = engine._tick_count
        self._drain_travel_queue(engine, tick)

        if tick % self.MIGRATION_CHECK_INTERVAL == 0 and tick > 0:
            self._check_migration(engine)

        if tick % 5 == 0:
            self._route_to_work(engine)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _drain_travel_queue(self, engine: "SimEngine", current_tick: int) -> None:
        """Move sims that have finished travelling to their destinations."""
        still_in_transit: list[TravelRecord] = []
        for record in self._travel_queue:
            if current_tick >= record.arrival_tick:
                # Land the sim
                self._sim_locations[record.sim_id] = record.destination_lot
                self._sim_districts[record.sim_id] = record.destination_district
                # Keep sim.current_lot_id in sync
                sim = next(
                    (s for s in engine.sims if s.sim_id == record.sim_id), None
                )
                if sim is not None:
                    sim.current_lot_id = record.destination_lot
                logger.debug(
                    "spatial: %s arrived at lot %s (%s) after %s travel",
                    record.sim_id, record.destination_lot,
                    record.destination_district, record.purpose,
                )
            else:
                still_in_transit.append(record)
        self._travel_queue = still_in_transit

    def _check_migration(self, engine: "SimEngine") -> None:
        """
        Compute district desirability and let wealthy sims in bad districts
        migrate to the most desirable alternative.

        Desirability = wealth*0.4 + culture*0.3 - crime*0.5 + tourism*0.1
        """
        # Compute desirability per district
        desirability: dict[str, float] = {}
        for d_id, identity in self._district_identity.items():
            score = (
                identity.get("wealth", 0.5) * 0.4
                + identity.get("culture", 0.5) * 0.3
                - identity.get("crime", 0.2) * 0.5
                + identity.get("tourism", 0.3) * 0.1
            )
            desirability[d_id] = round(score, 4)

        # Find best destination (highest desirability)
        if not desirability:
            return
        best_district = max(desirability, key=lambda d: desirability[d])

        for sim in engine.sims:
            current_district = self._sim_districts.get(sim.sim_id, "")
            if not current_district:
                continue
            d_score = desirability.get(current_district, 1.0)
            if d_score >= self.MIGRATION_DESIRABILITY_THRESHOLD:
                continue
            if sim.simoleons < self.MIGRATION_WEALTH_MIN:
                continue
            if random.random() >= self.MIGRATION_CHANCE:
                continue

            # Pick a destination lot in the best district
            dest_lots = self._district_lots.get(best_district, [])
            if not dest_lots:
                continue
            dest_lot = random.choice(dest_lots)

            event: dict = {
                "tick": engine._tick_count,
                "sim_id": sim.sim_id,
                "from_district": current_district,
                "to_district": best_district,
                "from_desirability": d_score,
                "to_desirability": desirability.get(best_district, 1.0),
                "simoleons": round(sim.simoleons, 2),
            }
            self._migration_log.append(event)
            # Trim log
            if len(self._migration_log) > self._MIGRATION_LOG_MAX:
                self._migration_log = self._migration_log[-self._MIGRATION_LOG_MAX:]

            # Enqueue the move
            self.enqueue_travel(
                sim_id=sim.sim_id,
                destination_lot=dest_lot,
                destination_district=best_district,
                purpose="migration",
                current_tick=engine._tick_count,
            )

            # Emit event on the engine bus
            bus = getattr(engine, "_bus", None)
            if bus is not None:
                bus.emit("sim_migrated", **event)

            logger.info(
                "spatial: %s migrating from %s (desirability=%.2f) to %s (%.2f)",
                sim.sim_id, current_district, d_score,
                best_district, desirability.get(best_district, 1.0),
            )

    def _route_to_work(self, engine: "SimEngine") -> None:
        """
        Every 5 ticks, move employed sims toward an appropriate work lot.
        Sims whose job is non-empty are routed to a business/community lot
        in their current district if one exists; otherwise the nearest district
        with work lots is chosen.
        """
        if not self._work_lots:
            return

        # Group work lots by district for fast lookup
        work_by_district: dict[str, list[str]] = {}
        for lot_id in self._work_lots:
            d_id = self._lot_to_district.get(lot_id, "")
            if d_id:
                work_by_district.setdefault(d_id, []).append(lot_id)

        for sim in engine.sims:
            job = sim.profile.get("job", "") or ""
            if not job or job.lower() in {"unemployed", "none", ""}:
                continue

            current_district = self._sim_districts.get(sim.sim_id, "")
            # Prefer work lots in the sim's own district
            local_lots = work_by_district.get(current_district, [])
            if local_lots:
                dest_lot = random.choice(local_lots)
                dest_district = current_district
            else:
                # Fall back to any work lot
                dest_lot = random.choice(self._work_lots)
                dest_district = self._lot_to_district.get(dest_lot, current_district)

            # Only travel if not already there
            if self._sim_locations.get(sim.sim_id) != dest_lot:
                self.enqueue_travel(
                    sim_id=sim.sim_id,
                    destination_lot=dest_lot,
                    destination_district=dest_district,
                    purpose="work",
                    current_tick=engine._tick_count,
                )
