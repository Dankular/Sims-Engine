# Sims Engine

An AI-powered life simulation where each Sim has a fully realised identity — OCEAN personality, MBTI type, zodiac sign, cultural background, fears, career, and relationships. Social interactions are adjudicated by a local LLM, producing deep emergent behaviour across 50+ independent systems.

---

## What makes this different

- Every interaction is **LLM-adjudicated**: the model sees full personality profiles (OCEAN, MBTI, zodiac, traits, skills, fears, emotional state, relationship history, venue context, weather) and returns structured JSON with 14 fields
- **Emergent feedback loops**: emotional contagion spreads moods through social networks, reputation gates who approaches whom, shared memory history biases partner selection
- **Distributed multiplayer**: NATS messaging lets multiple clients each host their own sims in shared rooms (global, friends-only, personal) — sims on different machines interact via request-reply LLM adjudication
- **Lightweight LLM**: default adjudicator is `qwen2.5:3b` (1.9 GB) — runs on any machine with 4 GB RAM

---

## Feature Overview

### Identity & Personality
| Feature | Detail |
|---------|--------|
| **OCEAN scoring** | `KevSun/Personality_LM` scores real OkCupid essays; children use `Arash-Alborz/personality-trait-predictor` |
| **MBTI type** | Deterministic OCEAN→MBTI + optional `theta/MBTI-ckiplab-bert` text inference |
| **Zodiac + birthday** | DOB generated per sim; sign derived; OCEAN nudged ±0.05 per sign |
| **Cultural background** | Sampled from CultureBank; inherited by children; cross-cultural friction in adjudicator |
| **27-emotion model** | GoEmotions labels, neuroticism-modulated decay, augmented post-adjudication by `cirimus/modernbert-base-go-emotions` |
| **Moodlets** | 24 named stackable overlays (Well Rested, Flirty, Inspired, Heartbroken…); dominant moodlet drives displayed state |
| **8 needs** | Hunger, energy, social, fun, hygiene, environment, bladder, comfort |
| **Likes & dislikes** | Specific preferences beyond interests; feed into attraction scoring and compatibility |
| **Wants & fears** | Aspiration-driven wants; fear acquisition from trauma; resolution through positive interactions |

### Simulation Engine
| Feature | Detail |
|---------|--------|
| **LLM adjudicator** | Full profile block sent per interaction: OCEAN, MBTI, zodiac, culture, social orientation, reputation, EI, creative reputation, traits, fears, skills, weather, memory, sentiments |
| **Two-tier LLM LOD** | ACTIVE: `qwen2.5:3b`; BACKGROUND: `qwen2.5:1.5b`; DORMANT: minimal decay |
| **Async adjudication** | Thread pool — tick loop never blocks on LLM |
| **Prompt enrichment** | 15 social norms + emotion calibration + ethics + empathetic context + ATOMIC causal inference + social IQA + situational example — all injected per call |
| **Timing traces** | `GET /timings` — boot phases, tick latency, LLM call latency with real token counts from Ollama |

### Emergent Mechanics (3 core feedback loops)
| Mechanic | How it works |
|----------|-------------|
| **Emotional contagion** | After each interaction, sims at friendship ≥ 35 absorb a fraction of each other's dominant emotion (strength scales 0–30% with friendship level) |
| **Reputation gating** | `reputation_score` adjusts pair-selection probability (−0.5 to +0.25) and biases interaction type weights in the scheduler |
| **Memory → partner selection** | Average valence of last 6 shared memories adjusts pair-selection score (±0.25) — good history pulls sims together, bad history pushes them apart |

### Sentiments (15 named types)
Distinct from generic memory — structured emotional tags on relationships that persist for N ticks and actively gate/unlock interactions:

| Positive | Negative |
|----------|----------|
| `first_kiss` → unlocks kiss/embrace | `betrayal` → blocks share secret/confide |
| `first_love` → unlocks propose marriage | `heartbreak` → blocks all romance |
| `saved_me` → unlocks deep emotional talk | `held_grudge` → blocks flirt/compliment |
| `shared_triumph`, `inspired_me`, `reconciled` | `embarrassed_me`, `jealousy_drama`, `rivalry_formed` |
| `childhood_bond` (permanent) | `cheated_on_me` (permanent), `lied_to_me` |

### Attraction & Compatibility
`attraction_score(sim_a, sim_b)` → −1.0..+1.0 chemistry score from:
- OCEAN complementarity (extraversion: slight opposites attract; openness: similarity bonds)
- Shared interests (Jaccard similarity)
- Likes/dislikes overlap (bonding on shared preferences, clash on opposing ones)
- Dealbreaker penalties (hard negative)
- MBTI compatibility bonus

Feeds into pair-selection (romantic bonus) and unlocks romantic interactions earlier.

### Clubs (Social Groups)
Interest-based groups of 3–6 sims that meet every 15 ticks at a shared venue:
- Auto-formed from shared interests at engine init
- Each club has a **rule** that biases interaction weights (`share_jokes_often`, `deep_conversations`, `skill_sharing`, etc.)
- Club members get +0.20 pair-selection bonus toward each other
- Meetings trigger group LLM interactions + social/fun need boosts

### Relationship Milestones
| System | Detail |
|--------|--------|
| **Marriage** | Propose interaction (romance ≥ 85 + `first_love` sentiment); household merge; wedding social event |
| **Divorce** | Romance < 15 for 3+ ticks → household split; heartbreak sentiment; grief arc |
| **Breakup** | Existing event type in life events pool |
| **Lifetime wishes** | One per aspiration (Fortune=§50k, Family=3 adult children, Popularity=10 close friends, Knowledge=all skills 8, Romance=3 partners, Creative=rep 80+) |
| **Aspiration rewards** | 3-milestone perk tree per aspiration; unlocks need boosts, skill XP, new interactions, trait upgrades |
| **Fame/celebrity** | `celebrity_score` (0–100) → tier (none/known/star/celebrity/icon); rises with positive reputation, large network, career success; unlocks fan interactions |

### Social Events & Drama
| System | Detail |
|--------|--------|
| **Social events** | Birthday parties (auto on aging), dinner parties (high-agreeableness hosts), weddings; group interactions + success score |
| **Drama cascade** | Witnesses observe negative interactions → credible gossip spread → sides form → enemy-of-my-friend friendship decay |
| **Phone interactions** | Async texts/calls between sims not at same venue; heuristic deltas; 8+ action types (memes, catch-up calls, flirty texts) |

### Economy
| System | Detail |
|--------|--------|
| **Gig economy** | 10 skill-matched gig types (freelance coding, catering, portrait commission, comedy open mic…); 3-tick completion; pay scales with skill |
| **Crafting outputs** | Cooking → food items (hunger restore + sell); creativity → artwork (sell + creative rep); writing → manuscripts (royalty per tick); logic → inventions |
| **Property / real estate** | 7 property types; buy with simoleons or mortgage; passive rental income per tick; value appreciates; rep boost from ownership |
| **Bills & household expenses** | Per-tick living cost + periodic bill cycle |

### World Systems
| System | Detail |
|--------|--------|
| **Weather** | 7 states (sunny/cloudy/rainy/stormy/snowy/foggy/heatwave); seasonal probability tables; need effects; sunshine mood boost; danger exposure for snow/heat; injected into adjudicator prompt |
| **Calendar & holidays** | 365-tick year; 8 annual holidays (New Year, Love Day, Spring Festival, Summer Solstice, Harvest Moon, Spooky Season, Winterfest, Winter Solstice); mass social/fun boost + special interactions on holiday tick |
| **Illness & contagion** | Spreads at shared venue via hygiene; 3 severity tiers; need decay multipliers while sick; recovery requires rest |
| **Pregnancy gestation** | 3-stage arc: discovery (morning sickness) → growing (nesting wants) → birth; partner gets support goal; miscarriage risk |
| **Seasons** | Monthly mood modulation affecting energy/social/fun |

### Behavioural Classes (7 Emergent Systems)
| Class | Dataset(s) | Behaviour |
|-------|-----------|-----------|
| **Reputation** | OsamaBsher/AITA · yosrissa/AITA · agentlans/reddit-ethics | YTA/NTA verdicts; `reputation_score` gates interactions |
| **Social Orientation** | tee-oh-double-dee/social-orientation | 8 circumplex states per tick |
| **Comedy Skill-Gated Jokes** | Fraser/short-jokes · shuttie/reddit-dadjokes | Skill 1–3: puns; skill 7+: sharp one-liners |
| **Memory Texture** | allenai/hippocorpus | Positive → vivid narration; traumatic → fragmented retold style |
| **Persuasion** | Anthropic/persuasion | `[CONVINCE]` interaction; charisma × agreeableness modifier |
| **Confessions** | SocialGrep/one-million-reddit-confessions | Fear-matched secrets; trauma on rejection |
| **Emotional Intelligence** | llm-council/emotional_application | EI scenarios; `ei_reputation` score |

---

## Distributed Multiplayer (NATS)

Three room types (Habbo Hotel model):

```
Global Room      — all clients; sims interact across machines
Friends Room     — private room for a set of trusted clients
Personal Room    — local only; no cross-client interactions
```

NATS subject layout:
```
room.<room_id>.state              — sim state broadcast (every tick)
room.<room_id>.interact.<sim_id>  — cross-client LLM adjudication (request-reply)
room.<room_id>.relationship       — friendship/romance delta sync
room.<room_id>.gossip             — gossip spread sync
```

```bash
# Start NATS
docker run -p 4222:4222 nats:latest
pip install nats-py

# Client A (hosts Sim A) — global room
python __main__.py --nats nats://localhost:4222 --room global --sims 2

# Client B (hosts Sim B) — same global room, different machine
python __main__.py --nats nats://192.168.x.x:4222 --room global --sims 2
```

---

## Requirements

```bash
pip install requests faker sentence-transformers chromadb fastapi uvicorn pygame-ce supertonic sounddevice nats-py
```

| LLM Backend | Setup |
|-------------|-------|
| **Ollama** (default) | `winget install Ollama.Ollama` then `ollama pull qwen2.5:3b` |
| **llama-server** | Run with OpenAI-compatible endpoint |
| **llama-cpp** | `pip install llama-cpp-python` — auto-downloads GGUF |

Background LLM (optional — BACKGROUND tier):
```bash
ollama pull qwen2.5:1.5b
# Or disable: set SIM_V2_BG_LLM=0
```

---

## Usage

```bash
start.bat                              # 3 sims, 10 ticks, Ollama
play.bat                               # Pygame window (1600×900)
start.bat --sims 5 --ticks 30
start.bat --story                      # LLM narration (text)
start.bat --story --tts                # + Supertonic audio
start.bat --backend llama-cpp          # in-process GGUF
start.bat --no-datasets                # skip dataset loading (faster boot)
start.bat --nats nats://localhost:4222 # join distributed world
```

**Pygame controls:** `SPACE` pause · `N` force tick · `+/-` speed · click select sim · `ESC` quit

**API server:**
```bash
python server.py --sims 5 --port 8080 --ollama-model qwen2.5:3b

GET  /state       — world state (sims, weather, calendar, clubs, events)
POST /tick        — advance one tick
GET  /sim/{id}    — single sim detail
POST /interact    — force an interaction
DELETE /reset     — restart simulation
WS   /stream      — real-time state push after every tick
GET  /timings     — boot phases, tick latency, LLM token counts
```

---

## Project Structure

```
core/             Sim, Needs, EmotionState, Skills, Wants, Relationships,
                  Memory, Sentiments, Compatibility, Moodlets, Illness,
                  LifetimeWish, AspirationRewards
engine/           SimEngine, LOD, scheduler, adjudicator, event bus,
                  NATS network (rooms), world registry
identity/         profile_factory, ocean_scorer, emotion_classifier, mbti, zodiac
datasets/         30+ DatasetRegistry fields, all loaders, .sim_cache/
llm/              OllamaBackend, LlamaServerBackend, LlamaCppBackend, TimedBackend
narrative/        career, life_events, gossip, story_writer, story_runner,
                  marriage, drama, pregnancy
tts/              TTSEngine (Supertonic, voice assignment, WAV output)
world/            venues, audio sensor, households, schedule, economy,
                  clubs, social_events, weather, calendar, crafting,
                  phone, gigs, property
sim_types/        Moodlet, Want, Fear, LODTier enums
persistence/      SQLite 5-table PersistenceLayer
pygame_app/       Pygame frontend (game loop, renderer, colours)
```

---

## Models

| Model | Purpose | Size |
|-------|---------|------|
| `Qwen/Qwen2.5-3B-Instruct-GGUF` | Primary LLM — ACTIVE tier adjudication | ~2 GB |
| `Qwen/Qwen2.5-1.5B-Instruct-GGUF` | Background LLM — BACKGROUND tier | ~1 GB |
| `KevSun/Personality_LM` | RoBERTa — OCEAN from essays | 500 MB |
| `Arash-Alborz/personality-trait-predictor` | DistilBERT — OCEAN for children | 250 MB |
| `cirimus/modernbert-base-go-emotions` | ModernBERT — 27-label emotion augmentation | 400 MB |
| `AnasAlokla/multilingual_go_emotions` | Multilingual fallback | 400 MB |
| `theta/MBTI-ckiplab-bert` | BERT — MBTI inference | 400 MB |
| `sentence-transformers/static-retrieval-mrl-en-v1` | Fast memory embeddings | 100 MB |
| `sentence-transformers/all-mpnet-base-v2` | Memory embeddings fallback | 400 MB |
| `Supertone/supertonic-3` | ONNX TTS — on-device speech, 10 voices | — |

---

## Datasets (30+ sources)

Personality · Social Norms · Commonsense · Moral Dilemmas · Emotions · Memory · Persuasion · Confessions · Reputation · Orientation · Comedy · Mental Health · Ethics · Culture · Finance · Gossip · Empathy · Venue Dialogue · EI Scenarios · Social Bias · Persona · Debates · Creativity · Manipulation · Interests

See `datasets/loader.py` for the full 30+ field `DatasetRegistry`.
