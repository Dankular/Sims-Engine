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
        self._bg_llm = bg_llm  # smaller model for BACKGROUND tier
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
        self._relationship_story_arcs: dict[tuple[str, str], dict[str, object]] = {}

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
                    item.sim_a_id,
                    item.sim_b_id,
                    exc,
                )

        # Tick all sims — DORMANT gets minimal decay only (no full tick)
        from config import NEEDS_DECAY
        from core.arcs import (
            grief_tick, loneliness_tick, burnout_tick,
            should_trigger_burnout, apply_burnout, maybe_generate_dream,
        )

        # Track which sims had a social interaction this tick
        _had_interaction: set[str] = {
            item.sim_a_id for item in self._pending
        } | {item.sim_b_id for item in self._pending}

        for sim in self.sims:
            if sim.lod_tier == LODTier.DORMANT:
                sim.needs.hunger = max(0, sim.needs.hunger - NEEDS_DECAY * 0.5)
                sim.needs.energy = max(0, sim.needs.energy - NEEDS_DECAY * 0.4)
                continue
            sim._current_tick = self._tick_count
            sim.tick(self.wants_engine, all_sim_ids)

            # Arc system ticks
            grief_tick(sim)
            loneliness_tick(sim, had_interaction=(sim.sim_id in _had_interaction))
            burnout_tick(sim)

            if should_trigger_burnout(sim):
                apply_burnout(sim)
                self._run_life_event(sim, None, event_type="burnout",
                                     context=f"{sim.name} is burned out after sustained overwork.")
                logger.info("[BURNOUT] %s", sim.name)

            # Seasonal / time-of-day mood modulation (Tier 3, #11)
            self._apply_seasonal_mood(sim, hour)

            # System 4: clear expired goals, set arc-state goals
            try:
                from core.goals import clear_expired_goal, set_goal_from_arc
                clear_expired_goal(sim, self._tick_count)
                # Grief bargaining → apologise goal; depression → confide
                if sim.grief_stage in (2, 3) and not getattr(sim, "_active_goal", None):
                    arc_key = "grief:bargaining" if sim.grief_stage == 2 else "grief:depression"
                    closest_id = self._find_closest_friend_id(sim)
                    if closest_id:
                        set_goal_from_arc(sim, arc_key, closest_id, self._tick_count)
                # Loneliness → seek comfort goal
                from core.arcs import is_lonely
                if is_lonely(sim) and not getattr(sim, "_active_goal", None):
                    closest_id = self._find_closest_friend_id(sim)
                    if closest_id:
                        set_goal_from_arc(sim, "loneliness", closest_id, self._tick_count)
            except Exception:
                pass

            # Dream system: fire on low-energy sleep state
            dream = maybe_generate_dream(sim)
            if dream:
                tag = f"dream:{dream[8:50]}"
                self.memory_store.write(sim.sim_id, sim.sim_id, tag, 0.0,
                                        tick=self._tick_count)
                logger.debug("[DREAM] %s: %s", sim.name, dream[:60])

            # System 5: sleep-phase memory consolidation
            try:
                from core.consolidation import consolidate_memories, CONSOLIDATION_ENERGY_THRESHOLD
                if sim.needs.energy <= CONSOLIDATION_ENERGY_THRESHOLD:
                    consolidated = consolidate_memories(sim, self.memory_store, self._tick_count)
                    if consolidated:
                        if self._db:
                            self._db.log_event(
                                self._tick_count, sim.sim_id,
                                "memory_consolidation",
                                {"summary": consolidated[:250]},
                            )
                        logger.info("[CONSOLIDATION] %s: %s", sim.name, consolidated[:60])
            except Exception:
                pass

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
            heuristic_background_interaction(
                bg_a, bg_b, self.relationships, self._bg_llm
            )

        # Active LOD: queue one LLM interaction when the queue is empty
        active = [s for s in self.sims if s.lod_tier == LODTier.ACTIVE]
        if len(active) >= 2 and not self._pending:
            pair = pick_interaction_pair(active, self.relationships)
            if pair:
                sim_a, sim_b = pair
                rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
                # Stamp venue name on sim so scheduler can use DailyDialog topic
                sim_a._current_venue_name = self._venue.get("name", "")
                interaction = choose_interaction(
                    sim_a, sim_b, rel, self._tick_count, self._datasets
                )
                self._submit_interaction(sim_a, sim_b, interaction, self._venue)

        # Venue rotation every 10 ticks with updated audio sensor
        if self._tick_count % 10 == 0:
            self._venue = {**random.choice(VENUES), **self._audio_sensor.sense()}
            logger.info(
                "[Tick %d] Venue → %s (ambient: %s)",
                self._tick_count,
                self._venue["name"],
                self._audio_sensor.current_class,
            )

        # Periodic relationship decay
        if self._tick_count % 10 == 0:
            self.relationships.decay_all()

        # Career events + mentor session check
        if self._tick_count % CAREER_EVENT_INTERVAL == 0:
            if random.random() < CAREER_EVENT_CHANCE:
                self._run_career_event(random.choice(self.sims))
            # Mentor session: 15% chance when skill gap >= 4 between any active pair
            if random.random() < 0.15 and len(active) >= 2:
                mentor = random.choice(active)
                student = random.choice([s for s in active if s is not mentor])
                self._run_mentor_session(mentor, student)

        # Health scare life events — triggered by chronic low energy
        from datasets.health import HEALTH_SCARE_TICK_COUNT

        for sim in self.sims:
            if getattr(sim, "_low_energy_ticks", 0) >= HEALTH_SCARE_TICK_COUNT:
                sim._low_energy_ticks = 0  # reset counter
                self._run_life_event(
                    sim,
                    None,
                    event_type="health_scare",
                    context=None,
                )
                break  # one health scare per tick

        # Group event — high-crowd venue with 3+ ACTIVE sims
        if self._tick_count % 3 == 0:
            self._maybe_run_group_event(active)

        # Gap 6: NPC ambient encounters (20% chance at crowded venues)
        if self._venue.get("crowd", 0) >= 0.5 and active and random.random() < 0.20:
            self._maybe_run_npc_encounter(random.choice(active))

        # Gap 7: Cross-household social events (check every 15 ticks)
        if self._tick_count % 15 == 0 and len(self.households) >= 2:
            self._maybe_run_cross_household_event()

        # Gap 8: Age-based inheritance (check every 50 ticks)
        if self._tick_count % 50 == 0:
            self._check_inheritance()

        # Life events — milestone-based first, random fallback
        if (
            self._tick_count % LIFE_EVENT_INTERVAL == 0
            and random.random() < LIFE_EVENT_CHANCE
        ):
            self._check_life_events()

        # Gossip spread
        if self._tick_count % 5 == 0 and len(self.sims) >= 3:
            spreader = random.choice(self.sims)
            rest = [s for s in self.sims if s is not spreader]
            receiver = random.choice(rest)
            subjects = [s for s in rest if s is not receiver]
            if subjects and random.random() < GOSSIP_SPREAD_CHANCE:
                self.gossip.spread(
                    spreader.sim_id, receiver.sim_id, random.choice(subjects).sim_id
                )

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
            sims_out.append(
                {
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
                        for n in [
                            "hunger",
                            "energy",
                            "social",
                            "fun",
                            "hygiene",
                            "environment",
                            "bladder",
                            "comfort",
                        ]
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
                }
            )
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
                        item.sim_a_id,
                        item.sim_b_id,
                        exc,
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
        # System 1: semantic recall — pass interaction as query for relevant retrieval
        memories = self.memory_store.recall(
            sim_a.sim_id, sim_b.sim_id, query=interaction
        )
        norms: list[str] = []
        if self._datasets and self._datasets.social_norms:
            norms = random.sample(
                self._datasets.social_norms,
                min(3, len(self._datasets.social_norms)),
            )
        system = build_adjudicator_system(
            norms, datasets=self._datasets, interaction=interaction
        )
        # Systems 1 + 2: inject semantic memories + dialogue buffer into context
        context_str = get_interaction_context(
            interaction, sim_a, sim_b,
            datasets=self._datasets,
            memory_store=self.memory_store,
            current_tick=self._tick_count,
        )
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
            self._tick_count,
            iid,
            sim_a.name,
            sim_b.name,
            interaction,
        )
        self._bus.emit(
            "interaction_queued",
            sim_a=sim_a,
            sim_b=sim_b,
            interaction=interaction,
            interaction_id=iid,
            tick=self._tick_count,
        )

    def _apply_resolved(self, item: PendingInteraction, result: dict) -> None:
        sim_a = self._sim_lookup.get(item.sim_a_id)
        sim_b = self._sim_lookup.get(item.sim_b_id)
        if not sim_a or not sim_b:
            return

        rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
        fd = float(result.get("friendship_delta", 0))
        rd = float(result.get("romance_delta", 0))

        # System 2: Sentiment-modulated deltas — graded outcomes from reaction text
        try:
            from llm.small_models import get_sentiment, get_ekman, sentiment_to_modifier
            reaction_text = result.get("sim_b_reaction", "")
            if reaction_text:
                sent_pipe = get_sentiment()
                if sent_pipe is not None:
                    sent_result = sent_pipe(reaction_text[:512])
                    mod = sentiment_to_modifier(sent_result)
                    fd = round(fd * mod, 2)
                    rd = round(rd * mod, 2)
                # Ekman "surprise" bonus: unexpected kindness hits harder
                ekman_pipe = get_ekman()
                if ekman_pipe is not None and fd > 0:
                    ekman_result = ekman_pipe(reaction_text[:512])
                    labels = ekman_result[0] if isinstance(ekman_result[0], list) else ekman_result
                    surprise_score = next(
                        (r["score"] for r in labels if r["label"].lower() == "surprise"), 0.0
                    )
                    if surprise_score > 0.4:
                        fd = round(fd * (1.0 + surprise_score * 0.3), 2)
        except Exception:
            pass

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

        # System 5: GoEmotions as PRIMARY emotional cascade
        memory_tag = result.get("memory_tag", item.interaction)
        reaction_text = result.get("sim_b_reaction", "")
        try:
            from identity.emotion_classifier import classify

            for sim, text, llm_emo in [
                (sim_a, memory_tag, emo_a),
                (sim_b, reaction_text, emo_b),
            ]:
                if not text.strip():
                    continue
                classified = classify(text, threshold=0.30, top_k=2)
                if classified:
                    # Primary emotion from classifier (higher confidence than LLM alone)
                    sim.emotion.add(classified[0], 0.75, duration=4, source="goemo_primary")
                    if len(classified) > 1:
                        sim.emotion.add(classified[1], 0.35, duration=2, source="goemo_secondary")
                elif llm_emo:
                    # Fallback: use LLM emotion if classifier has nothing
                    sim.emotion.add(llm_emo, 0.7, duration=4, source="life event")
        except Exception:
            # Hard fallback: original LLM emotions applied above are sufficient
            pass

        memory_tag = result.get("memory_tag", item.interaction)
        self.memory_store.write(
            sim_a.sim_id,
            sim_b.sim_id,
            memory_tag,
            valence,
            interaction_id=item.interaction_id,
            tick=self._tick_count,
        )
        rel.add_memory(memory_tag, valence, interaction_id=item.interaction_id)

        # System 2: update dialogue buffer for both sims
        try:
            turn = {
                "speaker_a": sim_a.name,
                "speaker_b": sim_b.name,
                "content_a": item.interaction[:100],
                "content_b": result.get("sim_b_reaction", memory_tag)[:100],
                "emotion_a": emo_a,
                "emotion_b": emo_b,
                "tick": self._tick_count,
            }
            _BUFFER_MAX = 6
            for sim, partner_id in ((sim_a, sim_b.sim_id), (sim_b, sim_a.sim_id)):
                if sim._dialogue_partner != partner_id:
                    sim._dialogue_buffer = []
                    sim._dialogue_partner = partner_id
                sim._dialogue_buffer.append(turn)
                sim._dialogue_buffer = sim._dialogue_buffer[-_BUFFER_MAX:]
                sim._dialogue_last_tick = self._tick_count
        except Exception:
            pass

        resolve_fears(sim_a, valence)
        resolve_fears(sim_b, valence)
        new_fear = self.wants_engine.check_fear_acquisition(
            sim_a, item.interaction, valence
        )
        if new_fear and new_fear not in sim_a.fears:
            sim_a.fears.append(new_fear)

        self.gossip.learn(sim_a.sim_id, sim_b.sim_id, memory_tag)
        self.gossip.learn(sim_b.sim_id, sim_a.sim_id, memory_tag)
        if rel.friendship >= 45 and random.random() < GOSSIP_SPREAD_CHANCE:
            others = [
                s for s in self.sims if s.sim_id not in (sim_a.sim_id, sim_b.sim_id)
            ]
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
                    rep_before = sim_a.reputation_score
                    sim_a.reputation_score = max(
                        -100, min(100, sim_a.reputation_score + delta)
                    )
                    logger.debug(
                        "[AITA] %s verdict=%s rep=%.0f",
                        sim_a.name, verdict, sim_a.reputation_score,
                    )
                    # Gap 3: Reputation gossip propagation — scandal spreads
                    rep_drop = rep_before - sim_a.reputation_score
                    if rep_drop > 10:
                        scandal = (
                            f"{sim_a.name} was judged '{verdict}' after "
                            f"'{item.interaction[:40]}' — community is talking"
                        )
                        bystanders = [
                            s for s in self.sims
                            if s.sim_id not in (sim_a.sim_id, sim_b.sim_id)
                        ][:3]
                        for bystander in bystanders:
                            self.gossip.learn(bystander.sim_id, sim_a.sim_id, scandal)
                        logger.info(
                            "[SCANDAL] %s rep drop %.0f → gossip spread to %d sims",
                            sim_a.name, rep_drop, len(bystanders),
                        )
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
                logger.debug(
                    "[PERSUADE] %s→%s mod=%.2f extra_fd=%+.1f",
                    sim_a.name,
                    sim_b.name,
                    mod,
                    extra_fd,
                )
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

        # Gap 3: Creative reputation update
        if "[CREATIVE WORK" in item.interaction:
            try:
                from datasets.creative_works import creative_reputation_delta

                delta = creative_reputation_delta(
                    valence,
                    sim_b.ocean.get("openness", 0.5),
                    sim_a.skills.levels.get("creativity", 0),
                )
                sim_a.creative_reputation = max(
                    0, min(100, sim_a.creative_reputation + delta)
                )
            except Exception:
                pass

        # Gap 4: Toxic cycle detection and phase tracking
        try:
            from datasets.manipulation import (
                is_toxic_initiator,
                is_toxic_target,
                next_toxic_phase,
                CYCLE_PHASES,
            )

            if is_toxic_initiator(sim_a) and is_toxic_target(sim_b):
                if 40 <= rel.friendship <= 70:
                    if not rel.in_toxic_cycle:
                        rel.in_toxic_cycle = True
                        rel.toxic_cycle_phase = "love_bombing"
                        rel.toxic_cycle_tick = self._tick_count
                    elif (self._tick_count - rel.toxic_cycle_tick) >= 3:
                        next_phase = next_toxic_phase(rel.toxic_cycle_phase)
                        phase_delta = CYCLE_PHASES.get(next_phase, 0)
                        rel.apply_deltas(phase_delta, 0)
                        rel.toxic_cycle_phase = next_phase
                        rel.toxic_cycle_tick = self._tick_count
                        logger.info(
                            "[TOXIC] %s→%s phase=%s fd=%+.0f",
                            sim_a.name,
                            sim_b.name,
                            next_phase,
                            phase_delta,
                        )
                        # Fear acquisition for target during devaluation
                        if (
                            next_phase == "devaluation"
                            and "[MANIPULATION" in item.interaction
                        ):
                            from datasets.manipulation import TECHNIQUE_FEAR
                            from sim_types.sim_types import Fear

                            tech = "gaslighting"
                            fear_label = TECHNIQUE_FEAR.get(
                                tech, "fear of losing grip on reality"
                            )
                            new_fear = Fear(
                                fear_label, sim_b.ocean.get("neuroticism", 0.5)
                            )
                            if new_fear.label not in [f.label for f in sim_b.fears]:
                                sim_b.fears.append(new_fear)
        except Exception:
            pass

        # Habit formation — record action repetition
        try:
            from core.arcs import register_action_history
            register_action_history(sim_a, item.interaction[:40])
        except Exception:
            pass

        # Trauma OCEAN drift — on high-magnitude negative events
        if abs(valence) > 0.8 and valence < 0:
            try:
                from datasets.trauma import apply_trauma_drift
                event_hint = (
                    "loss" if "loss" in item.interaction.lower()
                    else "conflict" if valence < -0.7
                    else "rejection"
                )
                apply_trauma_drift(sim_a, event_hint)
            except Exception:
                pass

        # Jealousy system — detect when a romantic interaction is observed by a partner
        if "flirt" in item.interaction.lower() or "romantic" in item.interaction.lower():
            try:
                self._check_jealousy(sim_a, sim_b, valence)
            except Exception:
                pass

        # Gift giving — handle "give gift" interaction outcome
        if "give gift" in item.interaction.lower() or "[GIFT]" in item.interaction:
            try:
                self._apply_gift_outcome(sim_a, sim_b, result)
            except Exception:
                pass

        # Gap 5: Jealousy reassurance — "reassure partner" reduces jealousy
        if "reassure" in item.interaction.lower() and valence > 0:
            try:
                rel.apply_reassurance()
                logger.debug("[JEALOUSY] %s reassured %s → score %.0f",
                             sim_a.name, sim_b.name, rel.jealousy_score)
            except Exception:
                pass

        # System 4 + Gap 4: mark goal achieved and clear it
        try:
            from core.goals import is_goal_valid, mark_goal_achieved
            goal = getattr(sim_a, "_active_goal", None)
            if goal and goal.target_sim == sim_b.sim_id:
                # Mark achieved if the interaction matches the goal type
                action_words = goal.action_type.replace("_", " ")
                if action_words in item.interaction.lower() or valence > 0.4:
                    mark_goal_achieved(sim_a)
                    sim_a._active_goal = None
                elif not is_goal_valid(goal, self._tick_count):
                    sim_a._active_goal = None
        except Exception:
            pass

        sim_a.register_action(item.interaction, self._tick_count)

        if self._db:
            try:
                self._db.log_event(
                    self._tick_count,
                    sim_a.sim_id,
                    "interaction",
                    {
                        "with": sim_b.sim_id,
                        "action": item.interaction,
                        "valence": valence,
                        "memory": memory_tag,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to log interaction event: %s", exc)

        logger.info(
            "[Tick %d] RESOLVED [%s]: %s → %s (%s) fd=%+.1f rd=%+.1f valence=%.2f",
            self._tick_count,
            item.interaction_id,
            sim_a.name,
            sim_b.name,
            item.interaction,
            fd,
            rd,
            valence,
        )
        self._bus.emit(
            "interaction_resolved",
            sim_a=sim_a,
            sim_b=sim_b,
            result=result,
            valence=valence,
            tick=self._tick_count,
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

        mbti_line = f"MBTI: {mbti} ({mbti_desc})\n" if mbti else ""
        zodiac_line = f"Zodiac: {zodiac} — {zodiac_desc}\n" if zodiac else ""
        orientation = getattr(sim, "social_orientation", "Warm-Agreeable")
        ori_desc = ORIENTATION_DESCRIPTORS.get(orientation, "")
        rep_score = getattr(sim, "reputation_score", 0.0)
        ei_rep = getattr(sim, "ei_reputation", 0.0)
        rep_note = ""
        if rep_score <= -30:
            rep_note = f"Community reputation: POOR ({rep_score:.0f}) — others may avoid them.\n"
        elif rep_score >= 40:
            rep_note = (
                f"Community reputation: STRONG ({rep_score:.0f}) — socially credible.\n"
            )
        creative_rep = getattr(sim, "creative_reputation", 0.0)
        creative_note = (
            f"Creative reputation: {creative_rep:.0f}/100\n"
            if creative_rep > 10
            else ""
        )
        cultural_bg = p.get("cultural_background", "")
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
            f"Cultural background: {cultural_bg}\n"
            if cultural_bg
            else ""
            f"Social orientation: {orientation} — {ori_desc}\n"
            f"EI reputation: {ei_rep:.0f}  |  "
            f"Humor: {p['humor_type']} | Comm style: {p['comm_style']}\n"
            f"Attachment: {p['attachment']}\n"
            f"{rep_note}"
            f"{creative_note}"
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
        ambient = (
            f"\nAmbient sound: {venue['ambient_sound']}"
            if venue.get("ambient_sound")
            else ""
        )

        # Gap 5: Cultural context for cross-cultural pairs
        cultural_note = ""
        try:
            bg_a = sim_a.profile.get("cultural_background", "")
            bg_b = sim_b.profile.get("cultural_background", "")
            if bg_a and bg_b and bg_a != bg_b:
                from datasets.culture import get_cultural_context

                cultural_note = get_cultural_context(bg_a, bg_b, rel.state_label())
        except Exception:
            pass

        # Add workplace norms for office venue
        if "office" in venue.get("name", "").lower():
            try:
                from datasets.culture import get_workplace_norm

                wn = get_workplace_norm()
                if wn:
                    cultural_note = (cultural_note + f"\nWorkplace norm: {wn}").strip()
            except Exception:
                pass

        full_context = "\n".join(filter(None, [context_str, cultural_note]))
        extra_ctx = (
            f"\n=== CONTEXTUAL KNOWLEDGE ===\n{full_context}" if full_context else ""
        )
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
            f'{sim_a.name} initiated: "{interaction}"\n\n'
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
                0,
                min(
                    100,
                    sim.career_performance + float(result.get("performance_delta", 0)),
                ),
            )
            sim.simoleons = max(
                0, sim.simoleons + float(result.get("simoleon_delta", 0))
            )
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
            self._bus.emit(
                "career_event", sim=sim, result=result, tick=self._tick_count
            )
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
                    sim_a,
                    sim_b,
                    event_type="relationship_milestone",
                    context=f"{sim_a.name} and {sim_b.name} have been dating.",
                )
                fired = True
                break

            # Friendship milestone: close → best friends
            if (
                REL_CLOSE <= rec.friendship < REL_BEST
                and random.random() < 0.4
                and not fired
            ):
                self._run_life_event(
                    sim_a,
                    sim_b,
                    event_type="friendship_milestone",
                    context=f"{sim_a.name} and {sim_b.name} are very close friends.",
                )
                fired = True
                break

        if not fired:
            # 20% chance: EI scenario life event instead of generic random
            if (
                self._datasets
                and hasattr(self._datasets, "ei_scenarios")
                and self._datasets.ei_scenarios
                and random.random() < 0.20
            ):
                from datasets.emotional_intelligence import (
                    sample_ei_scenario,
                    format_ei_interaction,
                )

                ei = sample_ei_scenario()
                if ei:
                    sim_a = random.choice(self.sims)
                    others = [s for s in self.sims if s is not sim_a]
                    sim_b = random.choice(others) if others else None
                    self._run_life_event(
                        sim_a,
                        sim_b,
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
        profile = generate_child_profile(
            parent_a.profile, parent_b.profile, okcupid_essays
        )
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
            child.name,
            parent_a.name,
            parent_b.name,
            child.sim_id,
        )
        self._bus.emit(
            "child_born",
            child=child,
            parent_a=parent_a,
            parent_b=parent_b,
            tick=self._tick_count,
        )
        return child

    def _run_life_event(
        self,
        sim_a: Sim,
        sim_b: Sim | None,
        event_type: str | None = None,
        context: str | None = None,
    ) -> None:
        if event_type is None:
            # Expanded life event pool
            pool = [
                "milestone",
                "conflict",
                "celebration",
                "loss",
                "opportunity",
                "financial_crisis",
                "rivalry_escalation",
                "creative_breakthrough",
                "cultural_clash",
            ]
            # Weight financial_crisis when sim is stressed
            if sim_a.simoleons < 300:
                pool.extend(["financial_crisis"] * 2)
            # Weight rivalry_escalation when relationship is in rivals tier
            if sim_b:
                rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
                if rel.state_label() == "rivals":
                    pool.extend(["rivalry_escalation"] * 2)
                if rel.romance >= 80 and rel.romance_label() == "partners":
                    pool.extend(["breakup", "reconciliation_arc"])
                    if self._datasets and getattr(
                        self._datasets, "literotica_snippets", []
                    ):
                        pool.append("intimate_encounter")
            event_type = random.choice(pool)

        # Health scare: use symptom context
        if event_type == "health_scare" and context is None:
            try:
                from datasets.health import health_scare_context

                context = health_scare_context(sim_a)
            except Exception:
                pass

        # Financial crisis: use FiQA context
        if event_type == "financial_crisis" and context is None:
            try:
                from datasets.finance import financial_crisis_context

                context = financial_crisis_context(
                    sim_a.simoleons, sim_a.profile["job"]
                )
            except Exception:
                pass
        if context is None:
            context = f"tick={self._tick_count}, emotion={sim_a.emotion.dominant}"

        # BORU arc scaffolding for high-drama pair events
        if (
            sim_b
            and self._datasets
            and getattr(self._datasets, "boru_arcs", [])
            and event_type in {"rivalry_escalation", "breakup", "reconciliation_arc"}
        ):
            key = (min(sim_a.sim_id, sim_b.sim_id), max(sim_a.sim_id, sim_b.sim_id))
            arc_state = self._relationship_story_arcs.get(key)
            if not arc_state:
                from datasets.boru import sample_arc

                arc = sample_arc()
                if arc:
                    arc_state = {"arc": arc, "stage": 0}
                    self._relationship_story_arcs[key] = arc_state
            if arc_state:
                arc = arc_state["arc"]
                stage = int(arc_state.get("stage", 0))
                beat = "inciting"
                if stage == 1:
                    beat = "escalation"
                elif stage >= 2:
                    beat = "resolution"
                context += (
                    f"\nStory arc scaffold ({beat}): {arc.get(beat, '')}\n"
                    "Use this only as structure: inciting event -> escalation -> resolution."
                )
                arc_state["stage"] = min(stage + 1, 2)

        # Adult-gated intimate encounter context
        if (
            event_type == "intimate_encounter"
            and sim_b
            and self._datasets
            and getattr(self._datasets, "literotica_snippets", [])
        ):
            rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
            if rel.romance < 75 or rel.romance_label() != "partners":
                return
            from datasets.adult import sample_literotica_snippet

            snippet = sample_literotica_snippet()
            if snippet:
                context += (
                    "\nIntimate encounter seed (adult mode): "
                    f"{snippet[:350]}\n"
                    "Narrate scene tastefully; keep adjudication outcomes strictly JSON deltas."
                )

        try:
            result = run_life_event_llm(
                self._llm,
                "You are narrating a life event in an AI life simulation.",
                sim_a,
                sim_b,
                event_type,
                context,
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
                            sim_a.emotion.add(
                                reaction.lower(), 0.5, 4, source="cascade"
                            )
            except Exception:
                pass

            logger.info("[Life Event] %s: %s", event_type, narrative[:80])
            self._bus.emit(
                "life_event",
                sim_a=sim_a,
                sim_b=sim_b,
                result=result,
                tick=self._tick_count,
            )

            # Grief arc — trigger on loss events
            if event_type in ("loss", "health_scare") and sim_a.grief_stage < 0:
                from core.arcs import start_grief
                target_label = (sim_b.name if sim_b else context) or event_type
                start_grief(sim_a, target_label)
                logger.info("[Grief] %s enters grief arc for '%s'", sim_a.name, target_label)

                # Gap 8: Multi-generational grief — children of sim_b also grieve
                if sim_b:
                    for child in self.sims:
                        if sim_b.sim_id in child.parent_ids and child.grief_stage < 0:
                            start_grief(child, sim_b.name)
                            logger.info(
                                "[GEN-GRIEF] %s (child of %s) enters grief arc",
                                child.name, sim_b.name,
                            )

            # System 4: NLI-inferred goal toward closest friend after notable life events
            try:
                from core.goals import set_goal_from_life_event
                closest_id = self._find_closest_friend_id(sim_a)
                if closest_id:
                    set_goal_from_life_event(
                        sim_a, event_type, closest_id, self._tick_count,
                        narrative=narrative,  # pass narrative for NLI inference
                    )
            except Exception:
                pass

        except Exception as exc:
            logger.warning("Life event failed: %s", exc)

    def _maybe_run_group_event(self, active_sims: list[Sim]) -> None:
        """Fire a group event if venue is crowded and 3+ active sims present."""
        if len(active_sims) < 3:
            return
        crowd = self._venue.get("crowd", 0)
        if crowd < 0.7:
            return
        if random.random() > 0.25:  # 25% chance per eligible tick
            return
        if not (
            self._datasets
            and hasattr(self._datasets, "group_scenes")
            and self._datasets.group_scenes
        ):
            return
        try:
            from datasets.group_scenes import (
                sample_group_scene,
                sample_trigger,
                format_group_interaction,
            )

            scene = sample_group_scene()
            trigger = sample_trigger()
            if not scene:
                return
            participants = random.sample(active_sims, min(3, len(active_sims)))
            sim_names = [s.name for s in participants]

            # System 9: Peer pressure — reward-model conformity, then heuristic fallback
            try:
                from datasets.social_conformity import (
                    compute_conformity_pressure_model,
                    compute_conformity_pressure,
                    sample_herding_seed,
                )
                venue_name = self._venue.get("name", "social gathering")
                pressures = compute_conformity_pressure_model(participants, venue_name)
                if not pressures:
                    pressures = compute_conformity_pressure(participants, "agreeableness")
                if pressures:
                    max_pressure_id = max(pressures, key=pressures.get)
                    if pressures[max_pressure_id] > 0.6:
                        herding = sample_herding_seed()
                        if herding:
                            trigger = f"{trigger} [peer pressure: {herding[:100]}]"
            except Exception:
                pass

            interaction = format_group_interaction(scene, sim_names, trigger)
            initiator = participants[0]
            target = participants[1]
            self._submit_interaction(initiator, target, interaction, self._venue)
            logger.info(
                "[GROUP] %s + %d witnesses at %s",
                initiator.name,
                len(participants) - 1,
                self._venue["name"],
            )
        except Exception as exc:
            logger.debug("Group event failed: %s", exc)

    # ── Arc system helpers ────────────────────────────────────────────────────

    def _find_closest_friend_id(self, sim: Sim) -> str | None:
        """Return the sim_id of the sim with the highest friendship score, or None."""
        best_id: str | None = None
        best_score = -999.0
        for other in self.sims:
            if other.sim_id == sim.sim_id:
                continue
            rec = self.relationships.get(sim.sim_id, other.sim_id)
            if rec.friendship > best_score:
                best_score = rec.friendship
                best_id = other.sim_id
        return best_id

    def _maybe_run_npc_encounter(self, sim: Sim) -> None:
        """Gap 6 + System 8: Ambient NPC encounter with bg-LLM-generated dialogue."""
        try:
            from core.npc import NPCManager
            if not hasattr(self, "_npc_manager"):
                self._npc_manager = NPCManager()
            npc = self._npc_manager.spawn()
            # System 8: generate dialogue via Ministral-3B background LLM
            dialogue = self._npc_manager.generate_dialogue(npc, sim, self._bg_llm)
            outcome = self._npc_manager.heuristic_interact(sim, npc, self.relationships)
            if dialogue:
                outcome["memory_tag"] = f"met {npc.name}: '{dialogue[:60]}'"
            self.memory_store.write(
                sim.sim_id, npc.npc_id,
                outcome["memory_tag"], outcome["valence"], tick=self._tick_count,
            )
            logger.info(
                "[NPC] %s met %s → '%s' (valence=%.2f)",
                sim.name, npc.name, dialogue[:40] if dialogue else "", outcome["valence"]
            )
        except Exception as exc:
            logger.debug("NPC encounter failed: %s", exc)

    def _maybe_run_cross_household_event(self) -> None:
        """Gap 7: Trigger a cross-household social event when 2+ households have high social need."""
        try:
            qualifying = []
            for hh in self.households:
                members = [s for s in self.sims if s.sim_id in hh.member_ids]
                if not members:
                    continue
                avg_social_need = sum(s.needs.social for s in members) / len(members)
                if avg_social_need < 70:
                    continue
                qualifying.append((hh, members))

            if len(qualifying) < 2:
                return

            hh_a, members_a = qualifying[0]
            hh_b, members_b = qualifying[1]
            rep_a = random.choice(members_a)
            rep_b = random.choice(members_b)

            # Use a cross-household party interaction seeded from group_scenes if available
            interaction = f"[CROSS-HOUSEHOLD EVENT] {hh_a.name} hosts {hh_b.name} for a social gathering"
            self._submit_interaction(rep_a, rep_b, interaction, {
                "name": "house party",
                "noise": 0.8, "intimacy": 0.4, "crowd": 0.9,
            })
            logger.info(
                "[CROSS-HH] %s ↔ %s social event triggered",
                hh_a.name, hh_b.name,
            )
        except Exception as exc:
            logger.debug("Cross-household event failed: %s", exc)

    def _check_inheritance(self) -> None:
        """Gap 8: Distribute simoleons to children when a parent reaches age threshold."""
        AGE_DEATH_THRESHOLD = 75
        try:
            for sim in list(self.sims):
                if sim.profile.get("age", 0) < AGE_DEATH_THRESHOLD:
                    continue
                if getattr(sim, "_inheritance_done", False):
                    continue
                children = [
                    s for s in self.sims if sim.sim_id in s.parent_ids
                ]
                if not children:
                    continue
                share = sim.simoleons / len(children)
                for child in children:
                    child.simoleons += share
                    child.emotion.add("relief", 0.5, duration=5, source="inheritance")
                    self.memory_store.write(
                        child.sim_id, sim.sim_id,
                        f"inherited simoleons from {sim.name}",
                        0.6, tick=self._tick_count,
                    )
                sim.simoleons = 0
                sim._inheritance_done = True
                logger.info(
                    "[INHERITANCE] %s distributed §%.0f to %d children",
                    sim.name, share * len(children), len(children),
                )
        except Exception as exc:
            logger.debug("Inheritance check failed: %s", exc)

    def _apply_seasonal_mood(self, sim: Sim, hour: int) -> None:
        """Apply time-of-day and seasonal mood modulation (Tier 3, #11)."""
        month = 1 + (self._tick_count // 200) % 12  # 1 month per 200 ticks

        # Seasonal effects
        if month in (12, 1, 2):   # winter
            sim.needs.energy = max(0, sim.needs.energy - 0.15)
            sim.needs.social = max(0, sim.needs.social - 0.10)
        elif month in (6, 7, 8):  # summer
            sim.needs.fun = min(100, sim.needs.fun + 0.15)

        # Time-of-day effects
        if 6 <= hour < 9:         # morning
            sim.needs.energy = max(0, sim.needs.energy - 0.20)
        elif 18 <= hour < 21:     # evening social/fun bonus
            sim.needs.social = min(100, sim.needs.social + 0.10)
            sim.needs.fun    = min(100, sim.needs.fun    + 0.10)

    def _check_jealousy(self, flirter: Sim, target: Sim, valence: float) -> None:
        """Detect jealousy in the target's existing partner when flirting fires."""
        for sim in self.sims:
            if sim.sim_id in (flirter.sim_id, target.sim_id):
                continue
            rel_with_target = self.relationships.get(sim.sim_id, target.sim_id)
            if rel_with_target.romance < 80:
                continue
            # This sim is target's partner — they may feel jealous
            neuro  = sim.ocean.get("neuroticism", 0.5)
            increase = 15 * neuro * max(0, valence)
            rel_with_target.jealousy_score = min(100,
                rel_with_target.jealousy_score + increase)
            if rel_with_target.jealousy_score > 50:
                sim.emotion.add("annoyance", 0.7, duration=5, source="jealousy")
                # Damage flirter-partner relationship
                rel_with_flirter = self.relationships.get(sim.sim_id, flirter.sim_id)
                rel_with_flirter.apply_deltas(-5, 0)
                logger.info("[JEALOUSY] %s jealous of %s flirting with %s (score=%.0f)",
                            sim.name, flirter.name, target.name,
                            rel_with_target.jealousy_score)
            if rel_with_target.jealousy_score > 70:
                # Romance damage on the primary relationship
                rel_with_target.apply_deltas(-3, -5)
                rel_with_target.jealousy_score = 50  # reset after consequence

    def _apply_gift_outcome(self, giver: Sim, receiver: Sim, result: dict) -> None:
        """Apply friendship bonus based on gift interest-match."""
        giver_interests = set(giver.profile.get("interests", []))
        receiver_interests = set(receiver.profile.get("interests", []))
        match = bool(giver_interests & receiver_interests)
        bonus = 8.0 if match else 2.0

        # Broke sim giving expensive gift → admiration bonus
        if giver.simoleons < 300 and random.random() < 0.5:
            bonus += 3.0
            receiver.emotion.add("admiration", 0.6, duration=5, source="gift_sacrifice")

        rel = self.relationships.get(giver.sim_id, receiver.sim_id)
        rel.apply_deltas(bonus, bonus * 0.3)
        logger.info("[GIFT] %s→%s match=%s bonus=+%.1f", giver.name, receiver.name, match, bonus)

    def _run_mentor_session(self, mentor: Sim, mentee: Sim) -> None:
        """Fire a mentoring interaction when skill gap >= 4."""
        rel = self.relationships.get(mentor.sim_id, mentee.sim_id)
        if rel.friendship < 45:
            return

        # Find the largest skill gap
        gap_skill = None
        max_gap = 0
        for skill, level in mentor.skills.levels.items():
            mentee_level = mentee.skills.levels.get(skill, 0)
            gap = level - mentee_level
            if gap > max_gap:
                max_gap = gap
                gap_skill = skill

        if not gap_skill or max_gap < 4:
            return

        rel.mentor_of = mentee.sim_id
        mentee.skills.gain_xp(gap_skill, 0.5)
        mentor.skills.gain_xp("charisma", 0.3)
        rel.apply_deltas(3, 0)
        logger.info("[MENTOR] %s teaches %s +0.5 %s", mentor.name, mentee.name, gap_skill)
        self._bus.emit("mentor_session", mentor=mentor, mentee=mentee,
                       skill=gap_skill, tick=self._tick_count)

    def _on_tick_complete(self, **_: Any) -> None:
        pass
