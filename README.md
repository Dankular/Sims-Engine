# Sims Engine

An AI-powered life simulation where each Sim has a fully realised identity — OCEAN personality, MBTI type, zodiac sign, birthday, fears, career, and relationships. Social interactions are adjudicated by a local LLM, producing emergent behaviour: moral dilemmas, mental health arcs, gossip networks, family trees, and spoken story narration.

## Features

### Personality & Identity
- **OCEAN scoring** — `KevSun/Personality_LM` scores real OkCupid essays; children scored via `Arash-Alborz/personality-trait-predictor` on their self-summary
- **MBTI type** — deterministic OCEAN→MBTI mapping + optional `theta/MBTI-ckiplab-bert` inference; type descriptor injected into every adjudication
- **Zodiac sign** — birthday generated per sim, sign derived from DOB, soft OCEAN nudges (±0.05) applied per sign
- **27-emotion model** — GoEmotions labels, neuroticism-modulated decay, augmented post-adjudication by `cirimus/modernbert-base-go-emotions` multi-label classifier
- **8 needs** — hunger, energy, social, fun, hygiene, environment, bladder, comfort; decay per tick, trigger moodlets at critical thresholds
- **Wants & fears** — aspiration-driven wants, fear acquisition from trauma, resolution through positive interactions and deep support

### Simulation Engine
- **LLM adjudicator** — full profile block (OCEAN, MBTI, zodiac, traits, fears, skills) sent to local LLM per interaction; outcomes update friendship, romance, skills, memories, gossip
- **Two-tier LLM LOD** — ACTIVE sims use primary 9B model; BACKGROUND sims use optional 3B model (`Ministral-3B`) for real emergent outcomes at low cost; DORMANT sims get minimal decay only
- **Async adjudication** — LLM calls run in a thread pool; tick loop never blocks
- **Moral dilemma events** — `demelin/moral_stories` + `ninoscherrer/moralchoice` inject moral scenarios into interactions; OCEAN decides which path the sim takes
- **Mental health layer** — `ShenLab/MentalChat16K` unlocks `[DEEP SUPPORT]` interactions when friendship > 65 and target sim has active fears
- **Emotional cascades** — `uwnlp/event2Mind` queries secondary reactions after life events (xReact, oReact, xWant)
- **Venue-aware dialogue** — `agentlans/li2017dailydialog` seeds topic-matched utterances (office → work, gym → health, nightclub → attitude & emotion)
- **Relationship graph** — friendship + romance scores, state labels (strangers → best friends, crush → partners), natural decay
- **Skills system** — charisma, comedy, cooking, fitness, logic, creativity; unlock special interactions at thresholds
- **Career & economy** — simoleons, pay periods, mood-modulated career drift, LLM-generated career events
- **Life events** — milestone-triggered (romance, friendship) + random; emotional cascade applied via event2Mind
- **Gossip graph** — sims learn and spread social facts; gossip enriches adjudicator memory context
- **Family system** — full UUID sim IDs; sims can have children with inherited OCEAN/traits/zodiac; `parent_ids` tracked on every profile
- **Households** — 2–3 sims grouped per household, shared home venue

### Narrative & Output
- **Story narration** — after each resolved interaction the LLM writes a narrator + dialogue script; `Supertonic TTS` synthesises it with per-sim voices
- **Per-sim voice assignment** — narrator: F1; each sim cycles through M1–M5, F2–F5
- **WAV output** — audio saved to `audio/tick{N}_{speaker}.wav`
- **Pygame frontend** — 1280×720 window with sim cards, relationship graph, event log, story panel; click to select sims
- **FastAPI server** — REST + WebSocket (`/state`, `/tick`, `/interact`, `/stream`, `/narrate`)
- **SQLite persistence** — sims, relationships, events, households, gossip; auto-saved every 5 ticks

### Ethics & Calibration
- **Social norms** — `allenai/prosocial-dialog` rules injected into adjudicator system prompt
- **Ethics calibration** — `hendrycks/ethics` commonsense + virtue examples in every adjudication
- **Social bias detection** — `allenai/social_bias_frames` detects norm violations and injects conflict escalation context
- **Persona consistency** — `nazlicanto/persona-based-chat` provides OCEAN-matched few-shot examples to anchor sim voice across interactions

---

## Requirements

```bash
pip install requests faker sentence-transformers chromadb fastapi uvicorn pygame-ce supertonic sounddevice
```

For the LLM adjudicator, one of:

| Backend | Setup |
|---------|-------|
| **Ollama** (default) | `winget install Ollama.Ollama` then `ollama pull qwen3.5:9b` |
| **llama-server** | Run `llama-server` with an OpenAI-compatible endpoint |
| **llama-cpp** | `pip install llama-cpp-python` — auto-downloads GGUF from HuggingFace |

Optional for two-tier background LLM:
```bash
# llama-cpp backend automatically downloads Ministral-3B for BACKGROUND tier
# Disable with: set SIM_V2_BG_LLM=0
```

---

## Usage

```bash
# Default — 3 sims, 10 ticks, Ollama
start.bat

# Pygame window
play.bat

# More sims / ticks
start.bat --sims 5 --ticks 30

# Story narration (text in terminal)
start.bat --story

# Story narration + TTS audio
start.bat --story --tts

# Different backend
start.bat --backend llama-server --llama-url http://127.0.0.1:8080/v1/chat/completions

# Print one profile as JSON (shows MBTI, zodiac, OCEAN, birthday)
start.bat --profile

# Skip dataset loading (faster startup)
start.bat --no-datasets

# Clear cached datasets and re-download
start.bat --update
```

### Pygame controls

| Key | Action |
|-----|--------|
| `SPACE` | Pause / resume |
| `N` | Force next tick |
| `+` / `-` | Speed up / slow down (0.25×–4×) |
| `click` | Select sim (card or graph node) |
| `ESC` | Quit |

### API server

```bash
python server.py --sims 5 --port 8080
```

Endpoints: `GET /state` · `POST /tick` · `GET /sim/{id}` · `POST /interact` · `DELETE /reset` · `WS /stream`

---

## Project Structure

```
core/           Sim, Needs, EmotionState, SkillsSystem, WantsEngine, RelationshipGraph, MemoryStore
engine/         SimEngine, LOD (two-tier LLM), scheduler, async adjudicator, event bus
identity/       profile_factory, faker_identity, ocean_scorer, emotion_classifier, mbti, zodiac
datasets/       16 HuggingFace dataset loaders + local JSON cache (.sim_cache/)
llm/            OllamaBackend, LlamaServerBackend, LlamaCppBackend, BackgroundLLMBackend
narrative/      career events, life events, gossip, story_writer, story_runner
tts/            TTSEngine (Supertonic, per-sim voice assignment, WAV save)
world/          venues, audio sensor, households, schedule, economy (shop visits)
sim_types/      Moodlet, Want, Fear, LODTier enums
persistence/    SQLite (5 tables), PersistenceBackend protocol
pygame_app/     Pygame frontend (game loop, renderer, colours)
api/            FastAPI thin wrapper

sim_engine.py   Legacy monolithic implementation (reference only)
start.bat       CLI launcher  →  python __main__.py %*
play.bat        Pygame launcher  →  python pygame_app/main.py %*
server.bat      API server launcher  →  python server.py %*
```

---

## HuggingFace Models

| Model | Purpose |
|-------|---------|
| `KevSun/Personality_LM` | RoBERTa — OCEAN scoring from OkCupid essays |
| `Arash-Alborz/personality-trait-predictor` | DistilBERT — OCEAN scoring for children (short text) |
| `cirimus/modernbert-base-go-emotions` | ModernBERT — 27-label GoEmotions multi-label classifier |
| `AnasAlokla/multilingual_go_emotions` | Multilingual BERT — emotion classifier fallback |
| `theta/MBTI-ckiplab-bert` | BERT — MBTI type inference from text |
| `sentence-transformers/static-retrieval-mrl-en-v1` | Static MRL — fast memory embeddings (100–400× speedup) |
| `sentence-transformers/all-mpnet-base-v2` | MPNet — higher quality memory embeddings (fallback) |
| `unsloth/Qwen3.5-9B-GGUF` | Primary LLM — ACTIVE tier adjudication |
| `unsloth/Ministral-3B-Instruct-2410-GGUF` | Background LLM — BACKGROUND tier adjudication |
| `Supertone/supertonic-3` | ONNX TTS — on-device speech synthesis, 10 voices |

## HuggingFace Datasets

| Dataset | Emergent Behaviour |
|---------|-------------------|
| `SpiceeChat/OkCupid-59k-Anonymized-Profiles` | Real essays for OCEAN scoring |
| `allenai/prosocial-dialog` | Social norms in adjudicator system prompt |
| `Estwld/atomic2020-origin` | Commonsense if/then reasoning |
| `allenai/social_i_qa` | Social reasoning QA calibration |
| `facebook/empathetic_dialogues` | Emotion-matched interaction seeds |
| `convai-challenge/conv_ai_2` | Quality-filtered dialogue seeds |
| `agentlans/multi-character-dialogue` | Multi-character dialogue seeds |
| `dair-ai/emotion` | Emotion calibration examples |
| `demelin/moral_stories` | Moral dilemma events — OCEAN decides action, guilt/pride follow |
| `ninoscherrer/moralchoice` | Ambiguous moral choices — personality divergence |
| `uwnlp/event2Mind` | Secondary emotional cascades after life events |
| `agentlans/li2017dailydialog` | Venue-matched topic seeds (10 topics) |
| `ShenLab/MentalChat16K` | Deep support interactions for vulnerable sims |
| `Amod/mental_health_counseling_conversations` | Counseling seed fallback |
| `nazlicanto/persona-based-chat` | OCEAN-matched few-shot examples for persona consistency |
| `allenai/social_bias_frames` | Norm violation detection → conflict escalation |
| `hendrycks/ethics` | Commonsense + virtue ethics calibration |
