"""world/politics.py — Elections, governance, and policy effects."""
from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

__all__ = ["PoliticalOffice", "Election", "PoliticalSystem"]

POLICY_EFFECTS: dict[str, dict[str, float]] = {
    "lower_taxes":     {"shop_cost_modifier": -0.10, "reputation_bonus": 0.15},
    "crime_crackdown": {"crime_modifier": -0.15,     "culture_modifier":  0.00},
    "arts_funding":    {"culture_modifier":  0.15,   "shop_cost_modifier": 0.05},
    "jobs_program":    {"employment_modifier": 0.10, "wage_modifier":      0.05},
    "housing_subsidy": {"shop_cost_modifier": -0.05, "wealth_modifier":    0.05},
}

_FACTION_GOAL_POLICY: dict[str, str] = {
    "form_union":       "jobs_program",
    "control_economy":  "lower_taxes",
    "oust_leader":      "crime_crackdown",
    "gain_influence":   "arts_funding",
}


@dataclass
class PoliticalOffice:
    office_id: str
    title: str
    holder_sim_id: str
    term_length: int
    term_start: int
    power: float = 0.7
    policies: list[str] = field(default_factory=list)


@dataclass
class Election:
    election_id: str
    office: str
    candidates: list[str]
    votes: dict[str, int]
    start_tick: int
    end_tick: int
    resolved: bool = False


class PoliticalSystem:
    ELECTION_TRIGGER_INTERVAL = 100
    ELECTION_DURATION = 15
    CAMPAIGN_REP_BOOST = 1.5
    TERM_LENGTH = 80
    POLICY_ADOPT_INTERVAL = 10
    MIN_CANDIDATES = 2

    def __init__(self) -> None:
        self.offices: list[PoliticalOffice] = []
        self.elections: list[Election] = []
        self.policy_effects: dict[str, float] = {}
        self._last_election_tick: int = 0
        self._sim_rep_cache: dict[str, float] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def get_officeholder(self, office_title: str) -> str | None:
        for office in self.offices:
            if office.title == office_title:
                return office.holder_sim_id
        return None

    def trigger_election(
        self,
        office_title: str,
        candidates: list[str],
        engine: "SimEngine",
        duration: int | None = None,
    ) -> None:
        if len(candidates) < self.MIN_CANDIDATES:
            return
        duration = duration or self.ELECTION_DURATION
        eid = str(uuid.uuid4())[:8]
        election = Election(
            election_id=eid,
            office=office_title,
            candidates=list(candidates),
            votes={c: 0 for c in candidates},
            start_tick=engine._tick_count,
            end_tick=engine._tick_count + duration,
        )
        self.elections.append(election)
        self._last_election_tick = engine._tick_count
        engine._bus.emit(
            "election_started",
            office=office_title,
            candidates=candidates,
            tick=engine._tick_count,
        )
        logger.info("[Politics] Election started for %s — %d candidates", office_title, len(candidates))

    def apply_policy_to_world(self, policy_key: str, engine: "SimEngine") -> None:
        effects = POLICY_EFFECTS.get(policy_key, {})
        for k, v in effects.items():
            self.policy_effects[k] = self.policy_effects.get(k, 0.0) + v
        # Propagate to neighborhoods
        if "crime_modifier" in effects:
            try:
                for dist in engine.neighborhoods.world.districts:
                    for nbhd in dist.neighborhoods:
                        nbhd.district_identity["crime"] = max(
                            0.0,
                            min(1.0, nbhd.district_identity.get("crime", 0.2) + effects["crime_modifier"] * 0.5),
                        )
            except Exception:
                pass
        if "culture_modifier" in effects:
            try:
                for dist in engine.neighborhoods.world.districts:
                    for nbhd in dist.neighborhoods:
                        nbhd.district_identity["culture"] = max(
                            0.0,
                            min(1.0, nbhd.district_identity.get("culture", 0.5) + effects["culture_modifier"] * 0.5),
                        )
            except Exception:
                pass
        engine._bus.emit("policy_enacted", policy=policy_key, effects=effects, tick=engine._tick_count)
        try:
            engine.world_history.record(
                tick=engine._tick_count,
                event_type="policy_enacted",
                description=f"Policy '{policy_key}' enacted by officeholder.",
                participants=[],
                location="global",
                impact=sum(effects.values()) * 0.2,
                tags=["political"],
            )
        except Exception:
            pass

    def summary(self) -> list[dict]:
        return [
            {
                "office": o.title,
                "holder": o.holder_sim_id,
                "policies": o.policies,
                "power": round(o.power, 2),
                "term_remaining": max(0, (o.term_start + o.term_length) - 0),
            }
            for o in self.offices
        ]

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        tick = engine._tick_count

        # Trigger periodic elections
        if (tick - self._last_election_tick >= self.ELECTION_TRIGGER_INTERVAL
                or self._faction_wants_power(engine)):
            self._maybe_trigger_election(engine)

        # Process active elections
        for election in self.elections:
            if not election.resolved:
                self._tally_votes(election, engine)
                if tick >= election.end_tick:
                    self._resolve_election(election, engine)

        # Remove resolved elections
        self.elections = [e for e in self.elections if not e.resolved]

        # Tick offices: expiry check
        for office in list(self.offices):
            if tick >= office.term_start + office.term_length:
                self._expire_office(office, engine)

        # Officeholders adopt policies every POLICY_ADOPT_INTERVAL
        if tick % self.POLICY_ADOPT_INTERVAL == 0:
            self._adopt_policies(engine)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _faction_wants_power(self, engine: "SimEngine") -> bool:
        try:
            for faction in engine.factions.factions:
                if "gain_political_power" in faction.goals or "oust_leader" in faction.goals:
                    return True
        except Exception:
            pass
        return False

    def _maybe_trigger_election(self, engine: "SimEngine") -> None:
        if any(not e.resolved for e in self.elections):
            return  # election already running
        # Gather candidates: faction leaders + high-reputation sims
        candidates: list[str] = []
        try:
            for faction in engine.factions.factions:
                if faction.leader_id:
                    candidates.append(faction.leader_id)
        except Exception:
            pass
        # Fill with highest-reputation sims if sparse
        if len(candidates) < self.MIN_CANDIDATES:
            sorted_sims = sorted(
                engine.sims,
                key=lambda s: getattr(s, "reputation_score", 0),
                reverse=True,
            )
            for s in sorted_sims:
                if s.sim_id not in candidates:
                    candidates.append(s.sim_id)
                if len(candidates) >= 4:
                    break
        self.trigger_election("Mayor", candidates[:4], engine)

    def _tally_votes(self, election: Election, engine: "SimEngine") -> None:
        for candidate_id in election.candidates:
            sim = engine._sim_lookup.get(candidate_id)
            if not sim:
                continue
            rep = getattr(sim, "reputation_score", 0.0)
            base_votes = max(0, int(rep / 10))
            # Faction support multiplier
            try:
                faction = engine.factions.get_sim_faction(candidate_id)
                if faction:
                    base_votes += len(faction.member_ids)
                    # Campaign boost to candidate rep
                    sim.reputation_score = min(100.0, sim.reputation_score + self.CAMPAIGN_REP_BOOST * 0.1)
            except Exception:
                pass
            election.votes[candidate_id] = election.votes.get(candidate_id, 0) + base_votes

    def _resolve_election(self, election: Election, engine: "SimEngine") -> None:
        if not election.votes:
            election.resolved = True
            return
        winner_id = max(election.votes, key=lambda k: election.votes[k])
        winner = engine._sim_lookup.get(winner_id)

        # Create or replace office
        self.offices = [o for o in self.offices if o.title != election.office]
        self.offices.append(PoliticalOffice(
            office_id=str(uuid.uuid4())[:8],
            title=election.office,
            holder_sim_id=winner_id,
            term_length=self.TERM_LENGTH,
            term_start=engine._tick_count,
        ))

        if winner:
            winner.reputation_score = min(100.0, winner.reputation_score + 15)
            if hasattr(winner, "moodlets"):
                winner.moodlets.add("proud", source="election_victory")

        # Losers get disappointed
        for c_id in election.candidates:
            if c_id != winner_id:
                loser = engine._sim_lookup.get(c_id)
                if loser and hasattr(loser, "moodlets"):
                    loser.moodlets.add("very_sad", source="election_loss")

        election.resolved = True
        engine._bus.emit(
            "election_won",
            winner=winner_id,
            office=election.office,
            votes=election.votes,
            tick=engine._tick_count,
        )
        try:
            engine.world_history.record(
                tick=engine._tick_count,
                event_type="election",
                description=f"{getattr(winner, 'name', winner_id)} won the {election.office} election.",
                participants=election.candidates,
                location="global",
                impact=0.4,
                tags=["political", "milestone"],
            )
        except Exception:
            pass
        logger.info("[Politics] %s won %s election", winner_id, election.office)

    def _expire_office(self, office: PoliticalOffice, engine: "SimEngine") -> None:
        self.offices.remove(office)
        engine._bus.emit("office_expired", office=office.title, tick=engine._tick_count)
        self._maybe_trigger_election(engine)

    def _adopt_policies(self, engine: "SimEngine") -> None:
        for office in self.offices:
            if len(office.policies) >= 3:
                continue
            holder = engine._sim_lookup.get(office.holder_sim_id)
            if not holder:
                continue
            # Pick policy aligned with faction goals or random
            policy = None
            try:
                faction = engine.factions.get_sim_faction(office.holder_sim_id)
                if faction:
                    for goal in faction.goals:
                        if goal in _FACTION_GOAL_POLICY:
                            policy = _FACTION_GOAL_POLICY[goal]
                            break
            except Exception:
                pass
            if policy is None:
                policy = random.choice(list(POLICY_EFFECTS.keys()))
            if policy not in office.policies:
                office.policies.append(policy)
                self.apply_policy_to_world(policy, engine)
                logger.info("[Politics] %s enacted policy: %s", office.holder_sim_id, policy)
