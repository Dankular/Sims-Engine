# Sims Engine — Claude Code Guide

## How to run

```bash
python __main__.py                          # 3 sims, 10 ticks, Ollama (qwen2.5:3b)
python __main__.py --sims 5 --ticks 30
python __main__.py --story --tts            # LLM narration + Supertonic TTS
python __main__.py --backend llama-cpp      # in-process GGUF
python __main__.py --backend mock           # deterministic mock (no LLM, fast)
python __main__.py --nats nats://localhost:4222 --room global  # distributed

python server.py --sims 3 --port 8080       # FastAPI + WebSocket server
python server.py --backend llama-server --port 8081  # benchmark mode

# MTP realtime run (Qwen3.5-0.8B MTP via llama-server):
powershell -ExecutionPolicy Bypass -File "./run_realtime_tts_llamaserver.ps1"

python show_timings.py [http://localhost:8081]   # live /timings report
python audit_prompt.py                            # capture + print a real adjudicator prompt
python show_prompt.py                             # full prompt breakdown
python gap_check_actions.py                       # check action coverage gaps
python validate_galaxea_actions.py                # validate open-world action data
```

## Architecture

Flat root layout (no package prefix). Key module boundaries:

```
core/         Pure sim state — Sim, Needs, EmotionState, Skills, Relationships,
              Memory, Sentiments, Compatibility, Moodlets, Illness,
              LifetimeWish, AspirationRewards, LifeStage (aging/death),
              ActionIntelligence, Consequences, ConversationArcPolicy,
              Dynasty (sim-level), KnowledgeAspiration
engine/       Simulation loop — SimEngine, LOD tiers (lod.py), async adjudication
              (async_adj.py), SimClock (clock.py), RealtimeSimEngine (realtime.py),
              NativeRegistry (natives.py), NeuralInteractionPolicy (neural_policy.py),
              InteractionObserver (observer.py), EventBus, NATS network layer,
              world_registry.py
identity/     Profile generation — OCEAN scoring, MBTI, zodiac, faker identity,
              EmotionClassifier (ModernBERT GoEmotions)
datasets/     DatasetRegistry (50+ fields), all loaders, .sim_cache/ (pickle)
              Includes: open_world_actions, openpets_catalog.json
llm/          Backends (Ollama/LlamaServer/LlamaCpp/Mock), adjudicator, context builder,
              timing (TimedBackend + TimingStore), small_models.py
narrative/    career, life_events, gossip, story_writer, story_runner,
              marriage (proposal/wedding/divorce), drama (cascade/witnesses),
              pregnancy (3-stage gestation arc), exporter.py
tts/          Supertonic wrapper, voice assignment, voice_catalog.py, winsound playback
world/        venues, households, clubs, social_events, weather, calendar,
              crafting, phone, gigs, property, shopping, pets, dynasty (world-level),
              burglar, grim_reaper, lot_layout, action_packs, context_sensors,
              neighborhoods, objects, career_manager, cleanliness, programming,
              cooking, wellness, skill_classes, life_states, dreams
analytics/    SimTracker (EventBus subscriber, per-tick snapshots), report.py
persistence/  SQLite 5-table PersistenceLayer
api/          Minimal FastAPI wrapper (create_app(engine)) — alternative to server.py
sim_types/    Enums + lightweight types (Moodlet, Want, Fear, LODTier)
```

## Engine loop (run_tick order)

1. Drain resolved LLM futures (`drain_pending` → `_apply_resolved`)
2. Tick all sims (LOD-gated: DORMANT = minimal decay only)
3. Arc systems (grief, loneliness, burnout) per sim
4. Autonomous self-care (sleep, eat, bathroom)
5. Shop visits for critical needs
6. LOD reassignment
7. Background LOD heuristic interaction (or LLM if bg_llm available)
8. **New systems tick**: weather, crafting, phone, gigs, properties, calendar, illness, pregnancy
9. Active LOD: pick pair → choose interaction → submit (local or NATS remote)
10. Venue rotation (every 10 ticks)
11. Relationship decay (every 10 ticks)
12. Career events (every CAREER_EVENT_INTERVAL)
13. Life events (every LIFE_EVENT_INTERVAL)
14. Gossip spread (every 5 ticks)
15. **Emergent systems**: clubs.tick(), social_events.tick(), divorce check, sentiment decay
16. Lifetime wish + aspiration milestone checks (every 10 ticks)
17. Celebrity score update (every 5 ticks)
18. NATS state broadcast (if networked)
19. Autosave (every 5 ticks)
20. `tick_complete` event → SimTracker snapshot

## _apply_resolved post-processing chain

After every adjudicated interaction:
- Apply friendship/romance deltas (with sentiment analysis modulation)
- Emotional contagion (spreads dominant emotion proportional to friendship ≥ 35)
- Drama cascade (witnesses → gossip, sides, enemy-of-friend)
- Moodlet generation from valence
- Sentiment detection (betrayal, first_kiss, heartbreak, etc.)
- Consequence recording (`core/consequences.py` → social_strain update)
- Marriage proposal check (romance ≥ 85 + first_love sentiment)
- Celebrity score bump
- Social orientation drift
- EI/AITA reputation
- Toxic cycle detection
- GoEmotions augmentation (ModernBERT)
- Memory write (ChromaDB)
- DB event log

## Key files to know

| File | What it does |
|------|-------------|
| `engine/engine.py` | Everything wired together; `run_tick()`, `_apply_resolved()`, `get_state()` |
| `engine/scheduler.py` | `pick_interaction_pair()` + `choose_interaction()` — reputation/memory/attraction/club/sentiment gating |
| `engine/async_adj.py` | `PendingInteraction` dataclass + `drain_pending()` |
| `engine/clock.py` | `SimClock` — maps wall time to sim time at configurable speed (default 3600×) |
| `engine/realtime.py` | `RealtimeSimEngine` — non-blocking `update()` safe to call at 60fps |
| `engine/lod.py` | `assign_lod_tiers()`, `heuristic_background_interaction()` |
| `engine/natives.py` | `NativeRegistry` — GTA5-style native function bindings (bootstraps from remote DB) |
| `engine/neural_policy.py` | `NeuralInteractionPolicy` — 3-phase contextual bandit (interaction + value + planner) |
| `engine/observer.py` | `InteractionObserver` — JSONL logger; attach to bus for pattern mining |
| `llm/context.py` | Builds adjudicator system prompt + user message (datasets injected here) |
| `llm/adjudicator.py` | `call_adjudicator()` — the LLM call |
| `llm/timing.py` | `TimedBackend` wraps any LLM backend; `store` singleton exposes `/timings` |
| `llm/mock_backend.py` | Deterministic mock — Beta-distributed responses per category, no LLM needed |
| `core/sentiments.py` | 15 named sentiment types, trigger detection, decay, interaction gating |
| `core/compatibility.py` | `attraction_score(sim_a, sim_b)` → −1.0..+1.0 |
| `core/moodlets.py` | `MoodletStack` — 24 named stackable overlays |
| `core/life_stage.py` | Life stage transitions (child→teen→young_adult→adult→elder), death at age 72–85 |
| `core/action_intelligence.py` | `score_action_feasibility()`, `compute_social_risk()` |
| `core/consequences.py` | `record_consequence()` → social_strain tracking |
| `narrative/marriage.py` | `marry()`, `divorce()`, `check_divorces()` |
| `narrative/drama.py` | `DramaCascade.on_resolved()` — witnesses, sides, propagation |
| `world/clubs.py` | `ClubManager` — auto-form, meeting tick, pair bonuses |
| `world/weather.py` | `WeatherSystem` — seasonal states, need effects, adjudicator injection |
| `world/calendar.py` | `GameCalendar` — holidays, date_dict() |
| `world/crafting.py` | `CraftingEngine` — skill → item → inventory → royalties |
| `world/gigs.py` | `GigManager` — 10 gig types, 3-tick completion |
| `world/property.py` | `PropertyManager` — buy, mortgage, passive income |
| `world/pets.py` | `PetManager` — PetRecord, openpets_catalog.json, needs/bond/mood |
| `world/burglar.py` | `BurglarSystem` — break-in events, police response |
| `world/grim_reaper.py` | `GrimReaperNPC` — death events, plead/chess/pet-save mechanics |
| `world/dynasty.py` | `DynastyManager` — multi-generation legacies, heirs, alliances, rivalries |
| `world/lot_layout.py` | `LotLayout` — room-by-room object placement, passive need effects |
| `world/shopping.py` | `ShoppingCenter` — curated marketplace lots |
| `world/context_sensors.py` | `sense_context()` → noise/crowd/intimacy/cleanliness dict |
| `world/neighborhoods.py` | `NeighborhoodSystem` — districts, neighborhoods, lots |
| `analytics/tracker.py` | `SimTracker` — per-tick snapshots (emotions, simoleons, arcs, relationships) |
| `analytics/report.py` | Render analytics data to charts/JSON |
| `engine/network.py` | `NATSNetwork` — NATS distributed multiplayer |
| `config.py` | All constants — model IDs, dataset IDs, thresholds, pools |
| `server.py` | FastAPI REST + WebSocket; `GET /timings` for live perf data |

## All systems instantiated in SimEngine.__init__

Primary (always on):
`relationships`, `memory_store`, `wants_engine`, `gossip`, `milestones`, `opportunities`, `ancestry`, `audio_sensor`, `natives`, `clubs`, `social_events`, `weather`, `crafting`, `phone`, `gigs`, `properties`, `calendar`, `illness`, `drama`, `pregnancy`, `dream_system`, `career_manager`, `cleanliness`, `programming`, `cooking`, `wellness`, `skill_classes`, `life_states`, `neighborhoods`, `objects`, `lot_layout`, `grim_reaper`, `shopping`, `dynasties`, `burglar`, `neural_policy`, `pets`

## LLM backends

| Backend | Flag | Env var | Notes |
|---------|------|---------|-------|
| llama-server | `--backend llama-server` | `SIM_V2_LLAMA_SERVER_URL`, `SIM_V2_LLAMA_SERVER_MODEL`, `SIM_V2_LLAMA_SERVER_TIMEOUT` | **Default.** OpenAI-compatible; MTP supported |
| llama-cpp | `--backend llama-cpp` | Uses `GGUF_REPO`/`GGUF_FILENAME` in config.py | In-process GGUF |
| mock | `--backend mock` | — | Deterministic Beta-distributed; no server needed |

Key env vars: `SIM_V2_ADJ_MAX_TOKENS` (default varies), `SIM_V2_BG_LLM=0` (disable background LLM).

## MTP benchmark setup (Qwen3.5-0.8B)

```powershell
# 1. Start llama-server with MTP model (GGUF auto-downloaded from HuggingFace)
$env:SIM_V2_LLAMA_SERVER_MODEL = "qwen3.5-0.8b-mtp"
$env:SIM_V2_LLAMA_SERVER_URL   = "http://127.0.0.1:8080/v1/chat/completions"
$env:SIM_V2_ADJ_MAX_TOKENS     = "260"
$env:SIM_V2_BG_LLM             = "0"

# llama-server args: -hf unsloth/Qwen3.5-0.8B-MTP-GGUF:UD-Q4_K_XL --spec-type draft-mtp --spec-draft-n-max 6

# 2. Start benchmark FastAPI server on separate port
python server.py --backend llama-server --port 8081 --sims 4

# 3. Collect timings
python show_timings.py http://localhost:8081
```

The PS1 script `run_realtime_tts_llamaserver.ps1` does all of the above plus TTS in one shot.

## LLM configuration

Default backend: **`llama-server`** (OpenAI-compatible HTTP API). Start with:

```powershell
# MTP model (fast CPU inference):
powershell -ExecutionPolicy Bypass -File "./run_realtime_tts_llamaserver.ps1"

# Or manually:
llama-server -hf unsloth/Qwen3.5-0.8B-MTP-GGUF:UD-Q4_K_XL --alias qwen3.5-0.8b-mtp \
  --port 8080 --ctx-size 4096 --threads 8 --spec-type draft-mtp \
  --spec-draft-n-max 6 --reasoning off --no-webui -np 1 --kv-cache-dtype fp8
```

`GGUF_USE_NO_THINK = False` — only set True for Qwen3.x without server-side `--reasoning off`.

Context window: `GGUF_N_CTX = 4096`. Current usage ~884–1059 tokens input, ~190 tokens output (26% utilisation).

## Adjudicator prompt structure

```
[SYSTEM]
  Base role + 14 JSON return keys       ~458 chars
  Social norms (15 sampled)             ~800 chars
  Emotion calibration (dair-ai/emotion) ~515 chars
  Ethics calibration (hendrycks/ethics) ~480 chars

[USER]
  Sim A profile block                   ~420 chars
  Sim B profile block                   ~420 chars
  Relationship state                    ~154 chars
  Environment / venue                   ~172 chars
  Contextual knowledge:
    ATOMIC causal inference             ~90 chars
    Social IQA (context+Q+A)            conditional
    Empathetic context                  conditional (skipped for neutral emotion)
    Situational example (keyword-matched) ~130 chars
    Cultural context                    conditional
    Weather line                        ~60 chars
  Interaction line                      ~110 chars
```

Run `python audit_prompt.py` or `python show_prompt.py` to capture a live prompt.

## Dataset injection rules

Only 4 datasets reach the LLM per call: `social_norms`, `emotion_calib`, `empath_index` (when emotion ≠ neutral), `dialogue_actions` (keyword-matched).

The DatasetRegistry holds 50+ fields (see `datasets/loader.py`). Most are used only for scheduling, arc detection, or post-processing — never sent to the LLM directly.

Datasets that **never** touch the LLM: `okcupid_essays`, `convai2_seeds`, `daily_dialog_index`, `moral_stories`, `moral_choice`, `aita_index`, `orientation_examples`, `jokes_by_tier`, `hippocorpus`, `persuasion_args`, `confessions_index`, `ei_scenarios`, `mental_chat_index`, `dialogue_actions` (scheduler), `empath_index` (scheduler).

## Sim life stages

`core/life_stage.py` defines 5 stages (by age):
- `child` (0–12): learning focus, inherited traits
- `teen` (13–17): social experimentation, identity
- `young_adult` (18–25): peak energy, ambition, romance
- `adult` (26–59): career/family, stable relationships
- `elder` (60+): wisdom, slower energy, health vulnerability; death at age 72–85 (random per sim)

## Key subsystems quick reference

**NativeRegistry** (`engine/natives.py`): GTA5-style native function system. Bootstraps from remote DB on startup; namespace-maps GTA namespaces to sim concepts (PED→SIM, MONEY→SHOPS, etc.). Provides engine actions callable by interaction scripts.

**NeuralInteractionPolicy** (`engine/neural_policy.py`): 3-phase contextual bandit — (1) interaction/object selection bandit, (2) value head for outcome prediction, (3) planner for acquire→use→social chains. Updates online per resolved interaction (lr=0.04).

**InteractionObserver** (`engine/observer.py`): Attach to EventBus → writes JSONL. Each record is self-contained. Reports go to `reports/`. Use `obs = InteractionObserver("reports/run.jsonl"); obs.attach(engine)`.

**SimTracker** (`analytics/tracker.py`): Subscribes to EventBus, snapshots per tick: emotions, simoleons, career_perf, OCEAN history, arc states, friendship per pair. Call `.serialise()` at run end.

**MockBackend** (`llm/mock_backend.py`): Beta-distributed valence per interaction category (no network). Use `--backend mock` for pattern mining / CI / unit tests without a running LLM.

**Context sensors** (`world/context_sensors.py`): `sense_context(engine, sim_a, sim_b)` → `{ambient_noise, crowd_density, intimacy, cleanliness}`. Used by scheduler action scoring.

## NATS distributed multiplayer

Room model (Habbo Hotel style): `global`, `personal.<client_id>`, `friends.<hash>`

```bash
docker run -p 4222:4222 nats:latest
pip install nats-py
python __main__.py --nats nats://localhost:4222 --room global
```

Each client owns its sims, runs LLM locally. Cross-client interactions use NATS request-reply.

## Conventions

- `sim_types/` not `types/` — stdlib clash avoidance
- All new world systems go in `world/`, narrative systems in `narrative/`, pure state in `core/`
- Engine systems are instantiated in `SimEngine.__init__` and ticked in `run_tick()`
- New systems that need `_apply_resolved` hooks: add after existing sentiment/moodlet blocks
- `_current_engine` module-level reference in `engine/engine.py` — lets scheduler helpers reach clubs/celebrity without circular imports
- Timer middleware: wrap any LLM backend with `TimedBackend(backend, name="...")` to get `/timings` data

## Common tasks

**Add a new interaction type:**
Edit `INTERACTION_TYPES` in `config.py` and add weight logic in `engine/scheduler.py::choose_interaction()`.

**Add a new dataset to the adjudicator:**
Add field to `DatasetRegistry` in `datasets/loader.py`, add loader, inject in `llm/context.py::get_interaction_context()` or `build_adjudicator_system()`.

**Add a new sim attribute:**
1. `core/sim.py::Sim.__init__`
2. `engine/engine.py::_sim_to_network_state()` (for NATS)
3. `engine/engine.py::get_state()` (for API/pygame)
4. `engine/world_registry.py::RemoteSimStub.__init__` (if scheduler needs it)

**Add a new world system:**
1. Create `world/mysystem.py` with a class that has `.tick(engine)`
2. Instantiate in `SimEngine.__init__` (after the "New systems" block)
3. Call `.tick(self)` in `run_tick()` in the new-systems block
4. Expose data in `get_state()` if needed

**Run a headless observation benchmark:**
```bash
python __main__.py --backend mock --sims 10 --ticks 500
# attach InteractionObserver to engine for JSONL output to reports/
```

**Check prompt token usage:**
```bash
curl http://localhost:8081/timings
python show_timings.py http://localhost:8081
```

**Pet system:** Pets live in `world/pets.py` (PetManager + PetRecord). Catalog sourced from `datasets/openpets_catalog.json`. Pets have hunger/fun/energy/cleanliness/mood/bond/neglect_ticks. Adopt via server endpoint; tick-decay mirrors sim needs.

**Dynasty system:** `world/dynasty.py` (DynastyManager) manages multi-gen legacies. Heirs, outcasts, alliances, rivalries all tracked. Distinct from `core/dynasty.py` (per-sim dynasty membership state).

**Grim Reaper:** `world/grim_reaper.py` (GrimReaperNPC). Triggered by `core/life_stage.py` death events. Sims can plead, play chess (logic skill gated), or send pets to harass. Endpoints: `GET /grim/status`, `POST /grim/plead`, etc.

**Burglar:** `world/burglar.py` (BurglarSystem). Random break-in events by lot. Endpoints: `GET /burglar/status`, `POST /burglar/trigger`.

## Server API surface (server.py)

Key endpoints beyond CRUD:
- `GET /timings` — LLM latency + tick perf stats (for `show_timings.py`)
- `GET /profile/{id}` — full social profile (identity, personality, relationships, activity)
- `POST /interact` — force a specific interaction
- `GET /lot/{lot_id}/layout` — room-by-room object placement with passive effects
- `GET /lot/{lot_id}/ambiance` — aggregated need bonuses per tick
- `GET /investments/{sim_id}` — investment dashboard
- `POST /investments/buy|collect|upgrade|rename|employee|sell`
- `GET /grim/status|tombstones`, `POST /grim/plead|chess|pet_save`
- `GET /burglar/status|log`, `POST /burglar/trigger`
- `POST /dynasty/create|heir|outcast|perk|alliance|rivalry`
- `WS /stream` — pushes state JSON after every tick
