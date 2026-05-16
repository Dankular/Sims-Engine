from __future__ import annotations

import concurrent.futures
import logging
import random
from typing import TYPE_CHECKING, Any

from config import (
    ADJ_WORKERS,
    CAREER_EVENT_CHANCE,
    CAREER_EVENT_INTERVAL,
    GAME_START_HOUR,
    GOSSIP_SPREAD_CHANCE,
    LIFE_EVENT_CHANCE,
    LIFE_EVENT_INTERVAL,
)
from core.memory import MemoryStore
from core.relationships import RelationshipGraph
from core.sim import resolve_fears
from core.wants import WantsEngine
from engine.async_adj import PendingInteraction, drain_pending
from engine.events import EventBus
from engine.lod import assign_lod_tiers, heuristic_background_interaction
from engine.scheduler import choose_interaction, pick_interaction_pair
from llm.adjudicator import call_adjudicator
from llm.context import build_adjudicator_system, get_interaction_context
from narrative.career import run_career_event
from narrative.gossip import GossipGraph
from narrative.life_events import run_life_event_llm
from sim_types.enums import LODTier
from world.venues import VENUES, AudioEnvironmentSensor

if TYPE_CHECKING:
    from core.sim import Sim
    from datasets.loader import DatasetRegistry
    from llm.backend import LLMBackend
    from persistence.protocol import PersistenceBackend

logger = logging.getLogger(__name__)


class SimEngine:
    def __init__(
        self,
        sims: list[Sim],
        llm: LLMBackend | None = None,
        bg_llm: LLMBackend | None = None,
        datasets: DatasetRegistry | None = None,
        db: PersistenceBackend | None = None,
        bus: EventBus | None = None,
    ) -> None:
        if llm is None:
            from llm.backend import LlamaCppBackend
            llm = LlamaCppBackend()

        self.sims = sims
        self._llm = llm
        self._bg_llm = bg_llm      # smaller model for BACKGROUND tier
        self._datasets = datasets
        self._db = db
        self._bus = bus or EventBus()

        self._tick_count = 0
        self._sim_lookup: dict[str, Sim] = {s.sim_id: s for s in sims}

        self.relationships = RelationshipGraph()
        self.memory_store = MemoryStore()
        self.wants_engine = WantsEngine()
        self.gossip = GossipGraph()

        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=ADJ_WORKERS)
        self._pending: list[PendingInteraction] = []

        self._audio_sensor = AudioEnvironmentSensor()
        self._venue: dict = {**random.choice(VENUES), **self._audio_sensor.sense()}
        self.households: list = []

        self._bus.on("tick_complete", self._on_tick_complete)
        assign_lod_tiers(self.sims)

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def run_tick(self) -> None:
        self._tick_count += 1
        hour = (GAME_START_HOUR + self._tick_count) % 24
        all_sim_ids = [s.sim_id for s in self.sims]

        # Drain resolved LLM futures
        done, self._pending = drain_pending(self._pending)
        for item in done:
            try:
                self._apply_resolved(item, item.future.result())
            except Exception as exc:
                logger.warning(
                    "Adjudicator failed for %s→%s: %s",
                    item.sim_a_id, item.sim_b_id, exc,
                )

        # Tick all sims — DORMANT gets minimal decay only (no full tick)
        from config import NEEDS_DECAY
        for sim in self.sims:
            if sim.lod_tier == LODTier.DORMANT:
                sim.needs.hunger = max(0, sim.needs.hunger - NEEDS_DECAY * 0.5)
                sim.needs.energy = max(0, sim.needs.energy - NEEDS_DECAY * 0.4)
                continue
            sim._current_tick = self._tick_count
            sim.tick(self.wants_engine, all_sim_ids)

        # Shop visits for critically low needs
        from world.economy import visit_shop
        from config import SHOP_DEFS, LOW_NEED_SHOP_THRESHOLD
        for sim in self.sims:
            if sim.lod_tier == LODTier.DORMANT:
                continue
            if sim.simoleons > LOW_NEED_SHOP_THRESHOLD:
                pressures = sim.needs.pressure_vector()
                for shop in SHOP_DEFS:
                    if pressures.get(shop["need"], 0) > 0.75:
                        visit_shop(sim, shop)
                        break

        # LOD reassignment every tick
        assign_lod_tiers(self.sims)

        # Background LOD: lightweight heuristic interactions
        background = [s for s in self.sims if s.lod_tier == LODTier.BACKGROUND]
        if len(background) >= 2:
            bg_a = random.choice(background)
            bg_b = random.choice([s for s in background if s is not bg_a])
            heuristic_background_interaction(bg_a, bg_b, self.relationships, self._bg_llm)

        # Active LOD: queue one LLM interaction when the queue is empty
        active = [s for s in self.sims if s.lod_tier == LODTier.ACTIVE]
        if len(active) >= 2 and not self._pending:
            pair = pick_interaction_pair(active, self.relationships)
            if pair:
                sim_a, sim_b = pair
                rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
                # Stamp venue name on sim so scheduler can use DailyDialog topic
                sim_a._current_venue_name = self._venue.get("name", "")
                interaction = choose_interaction(sim_a, sim_b, rel, self._tick_count, self._datasets)
                self._submit_interaction(sim_a, sim_b, interaction, self._venue)

        # Venue rotation every 10 ticks with updated audio sensor
        if self._tick_count % 10 == 0:
            self._venue = {**random.choice(VENUES), **self._audio_sensor.sense()}
            logger.info("[Tick %d] Venue → %s (ambient: %s)", self._tick_count, self._venue["name"], self._audio_sensor.current_class)

        # Periodic relationship decay
        if self._tick_count % 10 == 0:
            self.relationships.decay_all()

        # Career events
        if self._tick_count % CAREER_EVENT_INTERVAL == 0 and random.random() < CAREER_EVENT_CHANCE:
            self._run_career_event(random.choice(self.sims))

        # Life events — milestone-based first, random fallback
        if self._tick_count % LIFE_EVENT_INTERVAL == 0 and random.random() < LIFE_EVENT_CHANCE:
            self._check_life_events()

        # Gossip spread
        if self._tick_count % 5 == 0 and len(self.sims) >= 3:
            spreader = random.choice(self.sims)
            rest = [s for s in self.sims if s is not spreader]
            receiver = random.choice(rest)
            subjects = [s for s in rest if s is not receiver]
            if subjects and random.random() < GOSSIP_SPREAD_CHANCE:
                self.gossip.spread(spreader.sim_id, receiver.sim_id, random.choice(subjects).sim_id)

        # Autosave
        if self._db and self._tick_count % 5 == 0:
            try:
                self._db.save_state(self)
            except Exception as exc:
                logger.warning("Autosave failed: %s", exc)

        self._bus.emit("tick_complete", engine=self, tick=self._tick_count, hour=hour)

    def get_state(self) -> dict:
        sims_out = []
        for sim in self.sims:
            rels: dict[str, Any] = {}
            for other in self.sims:
                if other.sim_id == sim.sim_id:
                    continue
                rec = self.relationships.get(sim.sim_id, other.sim_id)
                rels[other.name] = {
                    "friendship": round(rec.friendship, 1),
                    "romance": round(rec.romance, 1),
                    "state": rec.state_label(),
                }
            sims_out.append({
                "id": sim.sim_id,
                "name": sim.name,
                "job": sim.profile["job"],
                "simoleons": round(sim.simoleons, 2),
                "career_performance": round(sim.career_performance, 1),
                "lod_tier": sim.lod_tier.name,
                "dominant_emotion": sim.emotion.dominant,
                "dominant_valence": sim.emotion.dominant_valence,
                "needs": {
                    n: round(getattr(sim.needs, n), 1)
                    for n in ["hunger", "energy", "social", "fun", "hygiene", "environment", "bladder", "comfort"]
                },
                "emotion": sim.emotion.dominant,
                "valence": sim.emotion.dominant_valence,
                "active_wants": [w.description for w in sim.active_wants],
                "fears": [f.label for f in sim.fears],
                "skills": sim.skills.levels,
                "ocean": sim.profile["ocean"],
                "household_id": sim.household_id,
                "parent_ids": sim.parent_ids,
                "relationships": rels,
            })
        hour = (GAME_START_HOUR + self._tick_count) % 24
        from world.schedule import time_label
        return {
            "tick": self._tick_count,
            "hour": hour,
            "time_label": time_label(hour),
            "venue": self._venue,
            "pending_interactions": len(self._pending),
            "sims": sims_out,
            "relationships": [
                {
                    "sim_a": a,
                    "sim_b": b,
                    "friendship": round(rec.friendship, 1),
                    "romance": round(rec.romance, 1),
                    "state": rec.state_label(),
                    "romance_label": rec.romance_label(),
                }
                for (a, b), rec in self.relationships.all_pairs()
            ],
            "households": [
                {
                    "id": hh.id,
                    "name": hh.name,
                    "members": hh.member_ids,
                    "funds": round(hh.funds, 2),
                }
                for hh in self.households
            ],
        }

    def flush_pending(self) -> None:
        """Block until every outstanding LLM future is resolved and applied."""
        while self._pending:
            futures = [item.future for item in self._pending]
            concurrent.futures.wait(futures)
            done, self._pending = drain_pending(self._pending)
            for item in done:
                try:
                    self._apply_resolved(item, item.future.result())
                except Exception as exc:
                    logger.warning(
                        "Adjudicator failed for %s→%s: %s",
                        item.sim_a_id, item.sim_b_id, exc,
                    )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)
        if self._db:
            self._db.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _submit_interaction(
        self, sim_a: Sim, sim_b: Sim, interaction: str, venue: dict
    ) -> None:
        rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
        memories = self.memory_store.recall(sim_a.sim_id, sim_b.sim_id)
        norms: list[str] = []
        if self._datasets and self._datasets.social_norms:
            norms = random.sample(
                self._datasets.social_norms,
                min(3, len(self._datasets.social_norms)),
            )
        system = build_adjudicator_system(norms, datasets=self._datasets)
        context_str = get_interaction_context(interaction, sim_a, sim_b, datasets=self._datasets)
        user_msg = self._build_user_message(
            sim_a, sim_b, interaction, rel, memories, venue, context_str
        )
        future = self._pool.submit(call_adjudicator, self._llm, system, user_msg)
        rel_key = (min(sim_a.sim_id, sim_b.sim_id), max(sim_a.sim_id, sim_b.sim_id))
        self._pending.append(
            PendingInteraction(
                sim_a_id=sim_a.sim_id,
                sim_b_id=sim_b.sim_id,
                interaction=interaction,
                rel_key=rel_key,
                future=future,
                tick_submitted=self._tick_count,
                memory_ctx=memories,
                venue_snapshot=dict(venue),
            )
        )
        iid = self._pending[-1].interaction_id
        logger.info(
            "[Tick %d] Queued [%s]: %s → %s (%s)",
            self._tick_count, iid, sim_a.name, sim_b.name, interaction,
        )
        self._bus.emit(
            "interaction_queued",
            sim_a=sim_a, sim_b=sim_b, interaction=interaction,
            interaction_id=iid, tick=self._tick_count,
        )

    def _apply_resolved(self, item: PendingInteraction, result: dict) -> None:
        sim_a = self._sim_lookup.get(item.sim_a_id)
        sim_b = self._sim_lookup.get(item.sim_b_id)
        if not sim_a or not sim_b:
            return

        rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
        fd = float(result.get("friendship_delta", 0))
        rd = float(result.get("romance_delta", 0))
        rel.apply_deltas(fd, rd)

        valence = float(result.get("valence", 0.5))

        sim_a.needs.restore("social", float(result.get("social_need_restore_a", 0)))
        sim_a.needs.restore("fun", float(result.get("fun_restore_a", 0)))
        sim_b.needs.restore("social", float(result.get("social_need_restore_b", 0)))
        sim_b.needs.restore("fun", float(result.get("fun_restore_b", 0)))

        emo_a = result.get("emotion_a", "")
        emo_b = result.get("emotion_b", "")
        if emo_a:
            sim_a.emotion.add(emo_a, 0.7, duration=4, source=item.interaction)
        if emo_b:
            sim_b.emotion.add(emo_b, 0.7, duration=4, source=item.interaction)

        if result.get("charisma_xp_a"):
            sim_a.skills.gain_xp("charisma", float(result["charisma_xp_a"]))
        if result.get("comedy_xp_a"):
            sim_a.skills.gain_xp("comedy", float(result["comedy_xp_a"]))

        # Emotion classifier — augment LLM emotions with ModernBERT GoEmotions tags
        memory_tag = result.get("memory_tag", item.interaction)
        reaction_text = result.get("sim_b_reaction", "")
        try:
            from identity.emotion_classifier import augment_emotions
            for sim, text, base_emo in [
                (sim_a, memory_tag, emo_a),
                (sim_b, reaction_text, emo_b),
            ]:
                extra = augment_emotions(base_emo, text)
                for extra_emo in extra[:1]:   # add at most 1 extra emotion per sim
                    sim.emotion.add(extra_emo, 0.4, duration=3, source="classifier")
        except Exception:
            pass

        memory_tag = result.get("memory_tag", item.interaction)
        self.memory_store.write(
            sim_a.sim_id, sim_b.sim_id, memory_tag, valence,
            interaction_id=item.interaction_id,
        )
        rel.add_memory(memory_tag, valence, interaction_id=item.interaction_id)

        resolve_fears(sim_a, valence)
        resolve_fears(sim_b, valence)
        new_fear = self.wants_engine.check_fear_acquisition(sim_a, item.interaction, valence)
        if new_fear and new_fear not in sim_a.fears:
            sim_a.fears.append(new_fear)

        self.gossip.learn(sim_a.sim_id, sim_b.sim_id, memory_tag)
        self.gossip.learn(sim_b.sim_id, sim_a.sim_id, memory_tag)
        if rel.friendship >= 45 and random.random() < GOSSIP_SPREAD_CHANCE:
            others = [s for s in self.sims if s.sim_id not in (sim_a.sim_id, sim_b.sim_id)]
            if others:
                target = random.choice(others)
                self.gossip.spread(sim_a.sim_id, target.sim_id, sim_b.sim_id)

        # Class 1: AITA reputation — tag negative interactions with community verdict
        if valence < -0.3 and self._datasets and hasattr(self._datasets, "aita_index"):
            try:
                from datasets.aita import sample_aita_for_topic, get_verdict_delta
                sim_state = {
                    "emotion": sim_a.emotion.dominant,
                    "simoleons": sim_a.simoleons,
                    "career_performance": sim_a.career_performance,
                }
                entry = sample_aita_for_topic(sim_state)
                if entry:
                    verdict = entry.get("verdict", "UNKNOWN")
                    delta = get_verdict_delta(verdict)
                    sim_a.reputation_score = max(-100, min(100,
                        sim_a.reputation_score + delta))
                    logger.debug("[AITA] %s verdict=%s rep=%.0f",
                                 sim_a.name, verdict, sim_a.reputation_score)
            except Exception:
                pass

        # Class 2: Social orientation drift after interaction
        try:
            from datasets.social_orientation import update_orientation_after_interaction
            sim_a.social_orientation = update_orientation_after_interaction(
                sim_a.social_orientation, valence, emo_a, sim_a.ocean
            )
            sim_b.social_orientation = update_orientation_after_interaction(
                sim_b.social_orientation, valence, emo_b, sim_b.ocean
            )
        except Exception:
            pass

        # Class 5: Persuasion modifier for convince interactions
        if "[CONVINCE]" in item.interaction and self._datasets:
            try:
                from datasets.persuasion import compute_persuasion_modifier
                mod = compute_persuasion_modifier(
                    sim_a.skills.levels.get("charisma", 0),
                    sim_b.ocean["agreeableness"],
                    sim_b.ocean["neuroticism"],
                    argument_delta=5.0,
                )
                extra_fd = round(fd * (mod - 1.0), 1)
                rel.apply_deltas(extra_fd, 0)
                logger.debug("[PERSUADE] %s→%s mod=%.2f extra_fd=%+.1f",
                             sim_a.name, sim_b.name, mod, extra_fd)
            except Exception:
                pass

        # Class 7: EI reputation update for EI scenarios
        if "[EMOTIONAL INTELLIGENCE" in item.interaction:
            try:
                from datasets.emotional_intelligence import ei_reputation_delta
                delta = ei_reputation_delta(
                    sim_a.ocean["agreeableness"],
                    sim_a.ocean["neuroticism"],
                    valence,
                )
                sim_a.ei_reputation = max(-50, min(50, sim_a.ei_reputation + delta))
            except Exception:
                pass

        sim_a.register_action(item.interaction, self._tick_count)

        if self._db:
            try:
                self._db.log_event(
                    self._tick_count,
                    sim_a.sim_id,
                    "interaction",
                    {"with": sim_b.sim_id, "action": item.interaction, "valence": valence, "memory": memory_tag},
                )
            except Exception as exc:
                logger.warning("Failed to log interaction event: %s", exc)

        logger.info(
            "[Tick %d] RESOLVED [%s]: %s → %s (%s) fd=%+.1f rd=%+.1f valence=%.2f",
            self._tick_count, item.interaction_id,
            sim_a.name, sim_b.name, item.interaction, fd, rd, valence,
        )
        self._bus.emit(
            "interaction_resolved",
            sim_a=sim_a, sim_b=sim_b, result=result,
            valence=valence, tick=self._tick_count,
            interaction_id=item.interaction_id,
            interaction=item.interaction,
        )

    @staticmethod
    def _profile_block(sim: "Sim") -> str:
        p = sim.profile
        mbti = p.get("mbti", "")
        mbti_desc = p.get("mbti_descriptor", "")
        zodiac = p.get("zodiac", "")
        zodiac_desc = p.get("zodiac_descriptor", "")
        from datasets.social_orientation import ORIENTATION_DESCRIPTORS
        mbti_line   = f"MBTI: {mbti} ({mbti_desc})\n" if mbti else ""
        zodiac_line = f"Zodiac: {zodiac} — {zodiac_desc}\n" if zodiac else ""
        orientation = getattr(sim, "social_orientation", "Warm-Agreeable")
        ori_desc    = ORIENTATION_DESCRIPTORS.get(orientation, "")
        rep_score   = getattr(sim, "reputation_score", 0.0)
        ei_rep      = getattr(sim, "ei_reputation", 0.0)
        rep_note    = ""
        if rep_score <= -30:
            rep_note = f"Community reputation: POOR ({rep_score:.0f}) — others may avoid them.\n"
        elif rep_score >= 40:
            rep_note = f"Community reputation: STRONG ({rep_score:.0f}) — socially credible.\n"
        return (
            f"Name: {sim.name} | Age: {p['age']} | Gender: {p['gender']}\n"
            f"Job: {p['job']} | Diet: {p['diet']}\n"
            f"Traits: {', '.join(p['traits'])}\n"
            f"Interests: {', '.join(p['interests'])}\n"
            f"Dealbreakers: {', '.join(p['dealbreakers'])}\n"
            f"Aspiration: {p['aspiration']}\n"
            f"OCEAN: O={sim.ocean['openness']} C={sim.ocean['conscientiousness']} "
            f"E={sim.ocean['extraversion']} A={sim.ocean['agreeableness']} "
            f"N={sim.ocean['neuroticism']}\n"
            f"{mbti_line}"
            f"{zodiac_line}"
            f"Social orientation: {orientation} — {ori_desc}\n"
            f"EI reputation: {ei_rep:.0f}  |  "
            f"Humor: {p['humor_type']} | Comm style: {p['comm_style']}\n"
            f"Attachment: {p['attachment']}\n"
            f"{rep_note}"
            f"Charisma: {sim.skills.levels.get('charisma', 0):.1f}/10 | "
            f"Comedy: {sim.skills.levels.get('comedy', 0):.1f}/10\n"
            f"Current emotion: {sim.emotion.dominant} (valence={sim.emotion.dominant_valence})\n"
            f"Active fears: {', '.join(f.label for f in sim.fears) or 'none'}"
        )

    def _build_user_message(
        self,
        sim_a: Sim,
        sim_b: Sim,
        interaction: str,
        rel: Any,
        memories: str,
        venue: dict,
        context_str: str,
    ) -> str:
        hour = (GAME_START_HOUR + self._tick_count) % 24
        ambient = f"\nAmbient sound: {venue['ambient_sound']}" if venue.get("ambient_sound") else ""
        extra_ctx = f"\n=== CONTEXTUAL KNOWLEDGE ===\n{context_str}" if context_str else ""
        return (
            f"/no_think\n\n"
            f"=== SIM A ===\n{self._profile_block(sim_a)}\n\n"
            f"=== SIM B ===\n{self._profile_block(sim_b)}\n\n"
            f"=== RELATIONSHIP (A→B) ===\n"
            f"Friendship: {rel.friendship:.1f}/100  |  State: {rel.state_label()}\n"
            f"Romance:    {rel.romance:.1f}/100     |  Status: {rel.romance_label()}\n"
            f"Interactions so far: {rel.interactions}\n"
            f"Recent memories: {memories}\n\n"
            f"=== ENVIRONMENT ===\n"
            f"Venue: {venue['name']}\n"
            f"Noise level: {venue.get('noise', 0)} (0=silent, 1=loud)\n"
            f"Intimacy: {venue.get('intimacy', 0)} (0=public, 1=private)\n"
            f"Crowd density: {venue.get('crowd', 0)}\n"
            f"Time of day: {hour:02d}:00{ambient}\n"
            f"{extra_ctx}\n"
            f"=== INTERACTION ===\n"
            f"{sim_a.name} initiated: \"{interaction}\"\n\n"
            f"Adjudicate this interaction and return the JSON result."
        )

    def _run_career_event(self, sim: Sim) -> None:
        system_prompt = (
            f"You are narrating a career event for {sim.name}, a {sim.profile['job']}. "
            "Generate a realistic workplace event affecting performance and emotions."
        )
        try:
            result = run_career_event(self._llm, system_prompt, sim)
            if not result:
                return
            sim.career_performance = max(
                0, min(100, sim.career_performance + float(result.get("performance_delta", 0)))
            )
            sim.simoleons = max(0, sim.simoleons + float(result.get("simoleon_delta", 0)))
            sim.emotion.add(
                result.get("emotion", "surprise"),
                float(result.get("intensity", 0.6)),
                int(result.get("duration", 4)),
                source="career event",
            )
            logger.info(
                "[Career] %s: %s (perf%+.1f §%+.0f)",
                sim.name,
                result.get("narrative", "")[:80],
                float(result.get("performance_delta", 0)),
                float(result.get("simoleon_delta", 0)),
            )
            self._bus.emit("career_event", sim=sim, result=result, tick=self._tick_count)
        except Exception as exc:
            logger.warning("Career event failed for %s: %s", sim.name, exc)

    def _check_life_events(self) -> None:
        """Check for milestone-based life events; fall back to a random event."""
        from config import REL_CLOSE, REL_BEST
        fired = False
        for (id_a, id_b), rec in self.relationships.all_pairs():
            sim_a = self._sim_lookup.get(id_a)
            sim_b = self._sim_lookup.get(id_b)
            if sim_a is None or sim_b is None:
                continue

            # Child spawning: partners who want a family
            if rec.romance >= 80 and not fired:
                wants_family_a = (
                    sim_a.profile["aspiration"] == "Family"
                    or "family-oriented" in sim_a.profile["traits"]
                    or any("child" in w.description for w in sim_a.active_wants)
                )
                wants_family_b = (
                    sim_b.profile["aspiration"] == "Family"
                    or "family-oriented" in sim_b.profile["traits"]
                    or any("child" in w.description for w in sim_b.active_wants)
                )
                if (wants_family_a or wants_family_b) and random.random() < 0.20:
                    self._spawn_child(sim_a, sim_b)
                    fired = True
                    break

            # Romance milestone: dating → partners
            if 55 <= rec.romance < 65 and not fired:
                self._run_life_event(
                    sim_a, sim_b,
                    event_type="relationship_milestone",
                    context=f"{sim_a.name} and {sim_b.name} have been dating.",
                )
                fired = True
                break

            # Friendship milestone: close → best friends
            if REL_CLOSE <= rec.friendship < REL_BEST and random.random() < 0.4 and not fired:
                self._run_life_event(
                    sim_a, sim_b,
                    event_type="friendship_milestone",
                    context=f"{sim_a.name} and {sim_b.name} are very close friends.",
                )
                fired = True
                break

        if not fired:
            # 20% chance: EI scenario life event instead of generic random
            if (self._datasets and hasattr(self._datasets, "ei_scenarios")
                    and self._datasets.ei_scenarios and random.random() < 0.20):
                from datasets.emotional_intelligence import sample_ei_scenario, format_ei_interaction
                ei = sample_ei_scenario()
                if ei:
                    sim_a = random.choice(self.sims)
                    others = [s for s in self.sims if s is not sim_a]
                    sim_b = random.choice(others) if others else None
                    self._run_life_event(
                        sim_a, sim_b,
                        event_type="ei_scenario",
                        context=format_ei_interaction(ei),
                    )
                    fired = True

            if not fired:
                sim_a = random.choice(self.sims)
                others = [s for s in self.sims if s is not sim_a]
                sim_b = random.choice(others) if others else None
                self._run_life_event(sim_a, sim_b)

    def _spawn_child(self, parent_a: Sim, parent_b: Sim) -> Sim:
        """Create a child sim from two partner sims and add them to the world."""
        from identity.profile_factory import generate_child_profile
        from core.sim import Sim as SimClass

        okcupid_essays = self._datasets.okcupid_essays if self._datasets else None
        profile = generate_child_profile(parent_a.profile, parent_b.profile, okcupid_essays)
        child = SimClass(profile)
        child.simoleons = random.uniform(200, 800)
        child.lod_tier = LODTier.ACTIVE

        self.sims.append(child)
        self._sim_lookup[child.sim_id] = child

        # Inherit household from parent_a if one exists
        if parent_a.household_id:
            child.household_id = parent_a.household_id
            for hh in self.households:
                if hh.id == parent_a.household_id:
                    hh.member_ids.append(child.sim_id)
                    break

        logger.info(
            "[Child] %s born to %s & %s (id=%s)",
            child.name, parent_a.name, parent_b.name, child.sim_id,
        )
        self._bus.emit(
            "child_born",
            child=child,
            parent_a=parent_a,
            parent_b=parent_b,
            tick=self._tick_count,
        )
        return child

    def _run_life_event(self, sim_a: Sim, sim_b: Sim | None, event_type: str | None = None, context: str | None = None) -> None:
        if event_type is None:
            event_type = random.choice(
                ["milestone", "conflict", "celebration", "loss", "opportunity"]
            )
        if context is None:
            context = f"tick={self._tick_count}, emotion={sim_a.emotion.dominant}"
        try:
            result = run_life_event_llm(
                self._llm,
                "You are narrating a life event in an AI life simulation.",
                sim_a, sim_b, event_type, context,
            )
            if not result:
                return
            sim_a.emotion.add(
                result.get("emotion_a", "surprise"), 0.6, 5, source="life event"
            )
            sim_a.simoleons = max(
                0, sim_a.simoleons + float(result.get("simoleon_delta_a", 0))
            )
            if sim_b:
                sim_b.emotion.add(
                    result.get("emotion_b", "surprise"), 0.6, 5, source="life event"
                )
                sim_b.simoleons = max(
                    0, sim_b.simoleons + float(result.get("simoleon_delta_b", 0))
                )
                rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
                rel.apply_deltas(
                    float(result.get("friendship_delta", 0)),
                    float(result.get("romance_delta", 0)),
                )
            # event2Mind — secondary emotional cascade
            narrative = result.get("narrative", "")
            try:
                from llm.context import get_life_event_context
                cascade_text = get_life_event_context(event_type, narrative)
                if cascade_text:
                    from datasets.event2mind import emotional_cascade
                    cascade = emotional_cascade(f"{event_type} {narrative}")
                    # Apply secondary wants as fresh moodlets
                    for reaction in cascade.get("xReact", [])[:1]:
                        from config import EMOTIONS_27
                        if reaction.lower() in EMOTIONS_27:
                            sim_a.emotion.add(reaction.lower(), 0.5, 4, source="cascade")
            except Exception:
                pass

            logger.info(
                "[Life Event] %s: %s", event_type, narrative[:80]
            )
            self._bus.emit(
                "life_event", sim_a=sim_a, sim_b=sim_b, result=result, tick=self._tick_count
            )
        except Exception as exc:
            logger.warning("Life event failed: %s", exc)

    def _on_tick_complete(self, **_: Any) -> None:
        pass
