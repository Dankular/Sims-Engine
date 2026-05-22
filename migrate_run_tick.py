"""
migrate_run_tick.py — Remove run_tick(), install three focused replacements.

Run:  python migrate_run_tick.py
"""
import sys

with open("engine/engine.py", encoding="utf-8") as f:
    src = f.read()

# ── Locate run_tick ────────────────────────────────────────────────────────────
START_MARKER = "    def run_tick(self) -> None:\n"
END_MARKER   = "\n    def get_state(self) -> dict:"

if "def run_tick" not in src:
    print("run_tick already removed.")
    sys.exit(0)

run_tick_start = src.index(START_MARKER)
get_state_start = src.index(END_MARKER)
run_tick_block = src[run_tick_start:get_state_start]
print(f"Found run_tick: {run_tick_block.count(chr(10))} lines -> deleting")

# ── Replacement ────────────────────────────────────────────────────────────────
REPLACEMENT = '''
    # =========================================================================
    # run_tick() REMOVED — use process_pending(), process_sims(dt),
    # tick_world_systems(now), tick_emergent_systems(now) directly,
    # or let HeartbeatLoop drive them automatically.
    # =========================================================================

    def process_pending(self) -> None:
        """Drain the async LLM adjudicator queue and apply resolved interactions."""
        done, self._pending = drain_pending(self._pending)
        for item in done:
            try:
                self._apply_resolved(item, item.future.result())
            except Exception as exc:
                logger.warning(
                    "Adjudicator failed for %s->%s: %s",
                    item.sim_a_id, item.sim_b_id, exc,
                )

    def process_sims(self, dt: float = 0.0) -> None:
        """
        Run the budgeted per-sim processing loop for one heartbeat beat.

        dt: real seconds elapsed since last beat.
        Only ACTIVE_BUDGET sims get full arc/goal/dream processing per beat.
        """
        import time as _time
        from core.arcs import (
            grief_tick, loneliness_tick, burnout_tick,
            should_trigger_burnout, apply_burnout, maybe_generate_dream,
        )
        now_wall    = _time.time()
        all_sim_ids = [s.sim_id for s in self.sims]
        self._tick_count += 1

        _had_interaction: set[str] = {item.sim_a_id for item in self._pending} | {
            item.sim_b_id for item in self._pending
        }

        self._budget.rebuild(self.sims)
        _active_batch = self._budget.next_active_batch()

        for sim in _active_batch:
            sim._current_tick = self._tick_count
            sim.tick(self.wants_engine, all_sim_ids)

            grief_tick(sim)
            loneliness_tick(sim, had_interaction=(sim.sim_id in _had_interaction))
            burnout_tick(sim)

            if should_trigger_burnout(sim):
                apply_burnout(sim)
                self._run_life_event(
                    sim, None, event_type="burnout",
                    context=f"{sim.name} is burned out after sustained overwork.",
                )
                logger.info("[BURNOUT] %s", sim.name)

            self._apply_seasonal_mood(sim, int(now_wall) % 24)

            if sim.profile.get("age", 0) >= 60:
                try:
                    from core.life_stage import elder_tick_effects
                    elder_tick_effects(sim)
                except Exception:
                    pass

            try:
                from core.goals import clear_expired_goal, set_goal_from_arc
                clear_expired_goal(sim, self._tick_count)
                if sim.grief_stage in (2, 3) and not getattr(sim, "_active_goal", None):
                    arc_key = ("grief:bargaining" if sim.grief_stage == 2
                               else "grief:depression")
                    closest_id = self._find_closest_friend_id(sim)
                    if closest_id:
                        set_goal_from_arc(sim, arc_key, closest_id, self._tick_count)
                elif sim.grief_stage == -1 and getattr(sim, "grief_target", ""):
                    if sim.needs.social >= 30:
                        sim.grief_target = ""
                    elif not getattr(sim, "_active_goal", None):
                        closest_id = self._find_closest_friend_id(sim)
                        if closest_id:
                            set_goal_from_arc(sim, "grief:recovery", closest_id,
                                              self._tick_count)
                            if hasattr(sim, "moodlets"):
                                sim.moodlets.add("lonely", source="post_grief_isolation")
                from core.arcs import is_lonely
                if is_lonely(sim) and not getattr(sim, "_active_goal", None):
                    closest_id = self._find_closest_friend_id(sim)
                    if closest_id:
                        set_goal_from_arc(sim, "loneliness", closest_id, self._tick_count)
            except Exception:
                pass

            dream = maybe_generate_dream(sim)
            if dream:
                self.memory_store.write(
                    sim.sim_id, sim.sim_id, f"dream:{dream[8:50]}", 0.0,
                    tick=self._tick_count,
                )

            try:
                from core.consolidation import (
                    consolidate_memories, CONSOLIDATION_ENERGY_THRESHOLD,
                )
                if sim.needs.energy <= CONSOLIDATION_ENERGY_THRESHOLD:
                    consolidated = consolidate_memories(
                        sim, self.memory_store, self._tick_count
                    )
                    if consolidated and self._db:
                        self._db.log_event(
                            self._tick_count, sim.sim_id, "memory_consolidation",
                            {"summary": consolidated[:250]},
                        )
            except Exception:
                pass

    def tick_world_systems(self, now: float | None = None) -> None:
        """
        Tick every world / economy system for one heartbeat beat.

        Self-care and need decay live in HeartbeatLoop (dt-based).
        LLM interaction selection lives in HeartbeatLoop._maybe_interact().
        This method handles everything else: shop visits, neural planning,
        LOD/shard, world context, and all .tick() system calls.
        """
        import time as _t
        if now is None:
            now = _t.time()

        # Shop visits for critically low needs
        from world.economy import visit_shop
        from config import SHOP_DEFS, LOW_NEED_SHOP_THRESHOLD
        for sim in self.sims:
            if (sim.lod_tier != LODTier.DORMANT
                    and sim.simoleons > LOW_NEED_SHOP_THRESHOLD):
                pressures = sim.needs.pressure_vector()
                for shop in SHOP_DEFS:
                    if pressures.get(shop["need"], 0) > 0.75:
                        visit_shop(sim, shop, engine=self)
                        break

        self._run_neural_planning(active_only=True)
        self._process_neural_consequences()

        # LOD + shard
        assign_lod_tiers(self.sims)
        for sim in self.sims:
            new_shard = (
                str(getattr(sim, "current_lot_id", "") or "")
                or str(getattr(sim, "household_id", "") or "")
                or "global"
            )
            if self._sim_shard_cache.get(sim.sim_id) != new_shard:
                self._shard_manager.assign(sim.sim_id, new_shard)
                self._sim_shard_cache[sim.sim_id] = new_shard
                self._pair_cache.bump_sim(sim.sim_id)

        # World context for interaction scheduler
        curw   = getattr(self.weather, "current", None)
        w_cond = str(getattr(curw, "condition", "clear"))
        w_temp = float(getattr(curw, "temperature", 20.0))
        spec   = (
            ",".join(sorted(set(self.neighborhoods.specialization.values()))[:3])
            or "mixed"
        )
        for sim in self.sims:
            sim._world_context_line = (
                f"weather={w_cond} temp_c={w_temp:.1f} "
                f"venue={self._venue.get('name', '')} district_modes={spec}"
            )

        self.weather.tick(self)
        self.calendar.tick(self)

        # Core lifecycle / economy
        self._process_gestation()
        self._try_adoption_event()
        self._process_illness_and_transmission()
        self._process_temperature_risk(int(now) % 24)
        self.opportunities.tick(self)
        self._process_family_planning()
        self._run_custody_schedule()
        self._run_gig_economy()
        self._run_odd_jobs()
        self._process_bills_and_household_expenses()
        self._run_property_system()
        self._run_business_system()
        self._run_education_system()
        self.objects.tick_market(self._tick_count)
        self.shopping.tick(self)
        self.dynasties.tick(self)
        self.pets.tick(self)
        self._run_university_system()
        self._run_career_progression_system()
        self._run_occult_system()
        self._run_perk_progression()
        self._process_phone_actions()
        self._run_calendar_events()
        self._process_survival_hazards()
        self._run_travel_system()
        self._run_pet_system()

        # Data-driven system ticks
        self.event_engine.tick(self)
        self.crafting.tick(self)
        self.phone.tick(self)
        self.gigs.tick(self)
        self.properties.tick(self)
        self._feed_stock_from_properties()
        self.illness.tick(self)
        self.pregnancy.tick(self)
        try:
            self.bookie.tick(self)
        except Exception:
            pass
        self.career_manager.tick(self)
        self.cleanliness.tick(self)
        self.programming.tick(self)
        self.cooking.tick(self)
        self.wellness.tick(self)
        self.skill_classes.tick(self)
        self.life_states.tick(self)
        self.neighborhoods.tick(self)
        self.stocks.tick(self)

        contract_events = self.contracts_engine.tick(self)
        for evt in contract_events:
            evt_type = str(evt.get("type", "contract_event"))
            self._bus.emit(evt_type, **evt)
            if evt_type in {"contract_settlement", "contract_settled"}:
                self._emit_economy_event("economy.contract_settlement", **evt)
            if "breach" in evt_type:
                self._emit_economy_event("economy.contract_breach", **evt)
                self._adjudicate_contract_dispute(evt)

        block = self.ledger.tick(self._tick_count)
        if block:
            self._bus.emit(
                "ledger_block",
                index=block.index, hash=block.block_hash,
                tx_count=len(block.txs), tick=self._tick_count,
            )

        for sim in self.sims:
            if getattr(sim, "household_id", None):
                self.lot_layout.tick_passive_effects(sim, sim.household_id)

        self.grim_reaper.tick(self)
        self.burglar.tick(self, int(now) % 24)
        self._risk_counterplay_tick()

        for sim in self.sims:
            if hasattr(sim, "moodlets"):
                sim.moodlets.tick()
                deadly = sim.moodlets.deadly_emotion()
                if deadly and random.random() < 0.05:
                    sim.moodlets._moodlets = [
                        m for m in sim.moodlets._moodlets
                        if m.label not in ("enraged", "mortified", "grief_stricken")
                    ]
                    sim.emotion.add("relief", 0.5, duration=3,
                                    source="survived_emotional_crisis")

    def tick_emergent_systems(self, now: float | None = None) -> None:
        """
        Tick emergent social / narrative systems for one heartbeat beat.

        Replaces all the tick-count-gated blocks at the bottom of run_tick:
        clubs, social events, divorces, sentiments, lifetime wish, aspirations,
        celebrity, deaths, ambient events, aging, life events, NATS, cognition.
        """
        import time as _t
        if now is None:
            now = _t.time()

        if not self._clubs_formed:
            self.clubs.form_clubs(self.sims, self._tick_count)
            self._clubs_formed = True

        self.clubs.tick(self)
        self.social_events.tick(self)

        from narrative.marriage import check_divorces
        check_divorces(self)

        from core.sentiments import decay_sentiments
        for _, rel in self.relationships.all_pairs():
            decay_sentiments(rel, self._tick_count)

        from core.lifetime_wish import check_wish
        from core.aspiration_rewards import tick_aspiration
        for sim in self.sims:
            try:
                if check_wish(sim, self, self._tick_count):
                    logger.info("[Wish] %s fulfilled: %s", sim.name,
                                getattr(sim.lifetime_wish, "description", "?"))
                    self._bus.emit("wish_fulfilled", sim=sim, tick=self._tick_count)
                if hasattr(sim, "lifetime_wish"):
                    self.aspiration_system.update_progress_from_wish(
                        sim,
                        float(getattr(sim.lifetime_wish, "_progress_cache", 0.0)),
                    )
                self.aspiration_system.tick(sim, self, self._tick_count)
                for ms_label in tick_aspiration(sim, self, self._tick_count):
                    logger.info("[Milestone] %s: %s", sim.name, ms_label)
                    self._bus.emit("milestone_achieved", sim=sim,
                                   milestone=ms_label, tick=self._tick_count)
            except Exception:
                pass

        self._update_celebrity_scores()
        self._process_deaths()

        from datasets.health import HEALTH_SCARE_TICK_COUNT
        for sim in self.sims:
            if getattr(sim, "_low_energy_ticks", 0) >= HEALTH_SCARE_TICK_COUNT:
                sim._low_energy_ticks = 0
                self._run_life_event(sim, None, event_type="health_scare",
                                     context=None)
                break

        active = [s for s in self.sims
                  if s.lod_tier == LODTier.ACTIVE
                  and not getattr(s, "_sleeping", False)]
        if random.random() < 0.33:
            self._maybe_run_group_event(active)
        if self._venue.get("crowd", 0) >= 0.5 and active and random.random() < 0.20:
            self._maybe_run_npc_encounter(random.choice(active))
        if len(self.households) >= 2 and random.random() < 0.10:
            self._maybe_run_cross_household_event()

        # Aging — once per real day
        if not hasattr(self, "_last_age_advance"):
            self._last_age_advance = now
        if now - self._last_age_advance >= 86400:
            self._advance_all_ages()
            self._check_inheritance()
            self._last_age_advance = now

        from config import LIFE_EVENT_CHANCE
        if random.random() < LIFE_EVENT_CHANCE * 0.05:
            self._check_life_events()

        if self._network:
            self._network.publish_states(
                self._current_room, self._build_state_diffs()
            )

        try:
            self._tick_intentions()
            self._tick_beliefs()
            self.institutions.tick(self)
            self.pressure_engine.tick(self)
            self.negotiation.tick(self)
            self.trait_drift.tick(self)
            self.emergence.snapshot(self)
            rumor_events = self.rumor_network.tick(self.sims, self._tick_count)
            for rev in rumor_events:
                self._bus.emit("rumor_event", **rev, tick=self._tick_count)
        except Exception as _cle:
            logger.debug("[Cognition] tick error: %s", _cle)

        try:
            self.chain_node.tick()
            self.web3.tick(self)
            self.web3.sync_balances(self)
        except Exception as _ce:
            logger.debug("[Chain] tick error: %s", _ce)

        self._bus.emit("tick_complete", engine=self, tick=self._tick_count)

'''

# ── Perform the replacement ────────────────────────────────────────────────────
new_src = src.replace(run_tick_block, REPLACEMENT, 1)

assert "def run_tick" not in new_src, "run_tick still present!"
assert "def process_pending" in new_src
assert "def process_sims" in new_src
assert "def tick_world_systems" in new_src
assert "def tick_emergent_systems" in new_src

with open("engine/engine.py", "w", encoding="utf-8") as f:
    f.write(new_src)

print(f"Done. engine.py now {new_src.count(chr(10))} lines.")
