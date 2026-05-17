# Sims Engine — Claude Code Guide

## How to run

```bash
python __main__.py                          # 3 sims, 10 ticks, Ollama (qwen2.5:3b)
python __main__.py --sims 5 --ticks 30
python __main__.py --story --tts            # LLM narration + Supertonic TTS
python __main__.py --backend llama-cpp      # in-process GGUF
python __main__.py --nats nats://localhost:4222 --room global  # distributed

python server.py --sims 3 --port 8080       # FastAPI + WebSocket server
python server.py --ollama-model qwen2.5:3b  # explicit model

python pygame_app/main.py                   # Pygame frontend (1600×900)

python show_timings.py                      # live /timings report
python audit_prompt.py                      # capture + print a real adjudicator prompt
python show_prompt.py                       # full prompt breakdown
```

## Architecture

Flat root layout (no package prefix). Key module boundaries:

```
core/         Pure sim state — Sim, Needs, EmotionState, Skills, Relationships,
              Memory, Sentiments, Compatibility, Moodlets, Illness,
              LifetimeWish, AspirationRewards
engine/       Simulation loop — SimEngine, LOD tiers, scheduler, adjudicator,
              EventBus, NATS network layer (network.py, rooms.py, world_registry.py)
identity/     Profile generation — OCEAN scoring, MBTI, zodiac, faker identity
datasets/     DatasetRegistry (30+ fields), all loaders, .sim_cache/ (pickle)
llm/          Backends (Ollama/LlamaServer/LlamaCpp), adjudicator, context builder,
              timing (TimedBackend + TimingStore)
narrative/    career, life_events, gossip, story_writer, story_runner,
              marriage (proposal/wedding/divorce), drama (cascade/witnesses),
              pregnancy (3-stage gestation arc)
tts/          Supertonic wrapper, voice assignment, winsound playback
world/        venues, households, clubs, social_events, weather, calendar,
              crafting, phone, gigs, property
sim_types/    Enums + lightweight types (Moodlet, Want, Fear, LODTier)
persistence/  SQLite 5-table PersistenceLayer
pygame_app/   Game loop, renderer, colours
```

## Engine loop (run_tick order)

1. Drain resolved LLM futures (`drain_pending` → `_apply_resolved`)
2. Tick all sims (LOD-gated: DORMANT = minimal decay only)
3. Arc systems (grief, loneliness, burnout) per sim
4. Autonomous self-care (sleep, eat, bathroom)
5. Shop visits for critical needs
6. LOD reassignment
7. Background LOD heuristic interaction
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
20. `tick_complete` event

## _apply_resolved post-processing chain

After every adjudicated interaction:
- Apply friendship/romance deltas (with sentiment analysis modulation)
- Emotional contagion (spreads dominant emotion proportional to friendship ≥ 35)
- Drama cascade (witnesses → gossip, sides, enemy-of-friend)
- Moodlet generation from valence
- Sentiment detection (betrayal, first_kiss, heartbreak, etc.)
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
| `engine/scheduler.py` | `pick_interaction_pair()` + `choose_interaction()` — includes reputation/memory/attraction/club/sentiment gating |
| `llm/context.py` | Builds adjudicator system prompt + user message (datasets injected here) |
| `llm/adjudicator.py` | `call_adjudicator()` — the LLM call |
| `llm/timing.py` | `TimedBackend` wraps any LLM backend; `store` singleton exposes `/timings` |
| `core/sentiments.py` | 15 named sentiment types, trigger detection, decay, interaction gating |
| `core/compatibility.py` | `attraction_score(sim_a, sim_b)` → −1.0..+1.0 |
| `core/moodlets.py` | `MoodletStack` — 24 named stackable overlays |
| `narrative/marriage.py` | `marry()`, `divorce()`, `check_divorces()` |
| `narrative/drama.py` | `DramaCascade.on_resolved()` — witnesses, sides, propagation |
| `world/clubs.py` | `ClubManager` — auto-form, meeting tick, pair bonuses |
| `world/weather.py` | `WeatherSystem` — seasonal states, need effects, adjudicator injection |
| `world/calendar.py` | `GameCalendar` — holidays, date_dict() |
| `world/crafting.py` | `CraftingEngine` — skill → item → inventory → royalties |
| `world/gigs.py` | `GigManager` — 10 gig types, 3-tick completion |
| `world/property.py` | `PropertyManager` — buy, mortgage, passive income |
| `engine/network.py` | `NATSNetwork` — NATS distributed multiplayer |
| `config.py` | All constants — model IDs, dataset IDs, thresholds, pools |
| `server.py` | FastAPI REST + WebSocket; `GET /timings` for live perf data |

## LLM configuration

Default model: **`qwen2.5:3b`** via Ollama (1.9 GB, sufficient for adjudication)

```bash
ollama pull qwen2.5:3b      # active tier
ollama pull qwen2.5:1.5b    # background tier
```

To switch model: change `GGUF_REPO` / `GGUF_FILENAME` in `config.py`, or pass `--ollama-model` to server.py.

`GGUF_USE_NO_THINK = False` — only set True for Qwen3.x models (suppresses chain-of-thought).

Context window: `GGUF_N_CTX = 4096`. Current usage ~884–1059 tokens input, ~190 tokens output (26% utilisation). ~2,200 tokens headroom for more dataset injections.

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

15 datasets **never** touch the LLM — they're used for scheduling or post-processing only:
`okcupid_essays`, `convai2_seeds`, `daily_dialog_index`, `moral_stories`, `moral_choice`, `aita_index`, `orientation_examples`, `jokes_by_tier`, `hippocorpus`, `persuasion_args`, `confessions_index`, `ei_scenarios`, `mental_chat_index`, `dialogue_actions` (scheduler), `empath_index` (scheduler).

## NATS distributed multiplayer

Room model (Habbo Hotel style):
- `global` — all clients, default
- `personal.<client_id>` — local only
- `friends.<hash>` — private group

```bash
docker run -p 4222:4222 nats:latest
pip install nats-py
python __main__.py --nats nats://localhost:4222 --room global
```

Each client owns its sims, runs LLM locally. Cross-client interactions use NATS request-reply — the target sim's owner adjudicates and replies with deltas.

## Conventions

- `sim_types/` not `types/` — stdlib clash avoidance
- All new world systems go in `world/`, narrative systems in `narrative/`, pure state in `core/`
- Engine systems are instantiated in `SimEngine.__init__` and ticked in `run_tick()`
- New systems that need `_apply_resolved` hooks: add to the "post-processing chain" section after the existing sentiment/moodlet blocks
- `_current_engine` module-level reference in `engine/engine.py` — lets scheduler helpers reach clubs/celebrity without circular imports
- Timer middleware: wrap any LLM backend with `TimedBackend(backend, name="...")` to get `/timings` data

## Common tasks

**Add a new interaction type:**
Edit `INTERACTION_TYPES` in `config.py` and add weight logic in `engine/scheduler.py::choose_interaction()`.

**Add a new dataset to the adjudicator:**
Add injection in `llm/context.py::get_interaction_context()` or `build_adjudicator_system()`.

**Add a new sim attribute:**
1. `core/sim.py::Sim.__init__`
2. `engine/engine.py::_sim_to_network_state()` (for NATS)
3. `engine/engine.py::get_state()` (for API/pygame)
4. `engine/world_registry.py::RemoteSimStub.__init__` (if scheduler needs it)

**Add a new world system:**
1. Create `world/mysystem.py` with a class that has `.tick(engine)`
2. Instantiate in `SimEngine.__init__`
3. Call `.tick(self)` in `run_tick()` in the new-systems block
4. Expose data in `get_state()` if needed

**Check prompt token usage:**
```bash
curl http://localhost:8080/timings
python show_timings.py
```
