# Sims Engine

An AI-powered life simulation where each Sim has a fully realised identity — OCEAN personality, MBTI type, zodiac sign, birthday, fears, career, and relationships. Social interactions are adjudicated by a local LLM, producing deep emergent behaviour: moral dilemmas, mental health arcs, community reputation verdicts, social orientation shifts, skill-gated comedy, authentic confession moments, emotional intelligence scenarios, and spoken story narration.

## Features

### Personality & Identity
- **OCEAN scoring** — `KevSun/Personality_LM` scores real OkCupid essays; children scored via `Arash-Alborz/personality-trait-predictor` on their self-summary
- **MBTI type** — deterministic OCEAN→MBTI mapping + optional `theta/MBTI-ckiplab-bert` text inference; descriptor injected into every adjudication
- **Zodiac sign + birthday** — DOB generated per sim, sign derived, OCEAN nudged (±0.05) per sign based on established correlations
- **27-emotion model** — GoEmotions labels, neuroticism-modulated decay, cross-checked post-adjudication by `cirimus/modernbert-base-go-emotions` multi-label classifier
- **8 needs** — hunger, energy, social, fun, hygiene, environment, bladder, comfort; trigger moodlets at critical thresholds
- **Wants & fears** — aspiration-driven wants, fear acquisition from trauma, resolution through positive interactions

### Simulation Engine
- **LLM adjudicator** — full profile block (OCEAN, MBTI, zodiac, social orientation, reputation, EI reputation, traits, fears, skills) sent to local LLM per interaction
- **Two-tier LLM LOD** — ACTIVE sims use primary 9B model; BACKGROUND sims use optional 3B model (`Ministral-3B`) for real emergent outcomes at low cost; DORMANT sims minimal decay
- **Async adjudication** — LLM calls run in a thread pool; tick loop never blocks
- **Relationship graph** — friendship + romance scores, state labels (strangers → best friends, crush → partners), natural decay
- **Skills system** — charisma, comedy, cooking, fitness, logic, creativity; unlock special interactions at thresholds
- **Career & economy** — simoleons, pay periods, mood-modulated career drift, LLM-generated career events
- **Life events** — milestone-triggered (romance, friendship) + EI scenarios (20%) + random; emotional cascade applied via event2Mind
- **Gossip graph** — sims learn and spread social facts; community verdicts attach to gossip via AITA
- **Family system** — full UUID sim IDs; sims can have children with inherited OCEAN/traits/zodiac; `parent_ids` tracked on every profile
- **Households** — 2–3 sims grouped per household, shared home venue

### Behaviour Classes (7 Emergent Systems)

| Class | Dataset(s) | Emergent Behaviour |
|-------|-----------|-------------------|
| **Reputation & Community Judgment** | OsamaBsher/AITA · yosrissa/AITA · agentlans/reddit-ethics | Negative interactions tagged with YTA/NTA/ESH/NAH verdict; `reputation_score` accumulates; poor reputation sims face avoidance |
| **Dynamic Social Orientation** | tee-oh-double-dee/social-orientation | 8 circumplex states (Warm-Agreeable → Arrogant-Calculating); updates per tick from needs + emotion; drifts after each interaction |
| **Comedy Skill-Gated Jokes** | Fraser/short-jokes · shuttie/reddit-dadjokes | Skill level 1–3 pulls mediocre puns; level 7 drops sharp one-liners / dad jokes; failed jokes generate annoyance |
| **Memory Texture** | allenai/hippocorpus | Positive memory → vivid linear narration (recalled); traumatic → fragmented retold style; openness score matched |
| **Persuasion & Influence** | Anthropic/persuasion | `[CONVINCE]` interaction type; Charisma × target agreeableness modifier applied to friendship delta |
| **Deep Confessions** | SocialGrep/one-million-reddit-confessions | Fear/emotion-matched secrets; rejected confessions → trauma memories; accepted → highest-valence relationship moments |
| **Emotional Intelligence** | llm-council/emotional_application | 200 EI scenarios fire as life events; `ei_reputation` score; high-agree sims respond well, high-neuro sims overreact |

### Additional Dataset Behaviour
- **Moral dilemmas** — `demelin/moral_stories` + `ninoscherrer/moralchoice`; OCEAN decides action, guilt/pride follow
- **Mental health layer** — `ShenLab/MentalChat16K`; `[DEEP SUPPORT]` unlocks at friendship > 65 + active fears
- **Emotional cascades** — `uwnlp/event2Mind`; secondary xReact/oReact/xWant after life events
- **Venue-aware dialogue** — `agentlans/li2017dailydialog`; office → work, gym → health, nightclub → attitude
- **Ethics calibration** — `hendrycks/ethics` commonsense + virtue examples in every adjudication
- **Social bias detection** — `allenai/social_bias_frames`; offensive interactions escalate to conflict
- **Persona consistency** — `nazlicanto/persona-based-chat`; OCEAN-matched few-shot examples in adjudicator

### Narrative & Output
- **Story narration** — LLM writes narrator + dialogue script after each resolved interaction; `Supertonic TTS` synthesises with per-sim voices; hippocorpus style scaffolding shapes narrative texture
- **Per-sim voice assignment** — narrator: F1; sims cycle through M1–M5, F2–F5
- **WAV output** — audio saved to `audio/tick{N}_{speaker}.wav`
- **Pygame frontend** — 1280×720; sim cards, relationship graph, event log, story panel; click to select
- **FastAPI server** — REST + WebSocket: `/state`, `/tick`, `/interact`, `/stream`
- **SQLite persistence** — sims, relationships, events, households, gossip; auto-saved every 5 ticks

---

## Requirements

```bash
pip install requests faker sentence-transformers chromadb fastapi uvicorn pygame-ce supertonic sounddevice
```

For the LLM adjudicator:

| Backend | Setup |
|---------|-------|
| **Ollama** (default) | `winget install Ollama.Ollama` then `ollama pull qwen3.5:9b` |
| **llama-server** | Run `llama-server` with an OpenAI-compatible endpoint |
| **llama-cpp** | `pip install llama-cpp-python` — auto-downloads GGUF from HuggingFace |

Optional two-tier background LLM (Ministral-3B for BACKGROUND tier sims):
```bash
# llama-cpp backend auto-downloads on first use
# Disable: set SIM_V2_BG_LLM=0
```

---

## Usage

```bash
start.bat                        # 3 sims, 10 ticks, Ollama
play.bat                         # Pygame window
start.bat --sims 5 --ticks 30
start.bat --story                # LLM narration (text)
start.bat --story --tts          # LLM narration + Supertonic audio
start.bat --backend llama-cpp    # in-process GGUF model
start.bat --profile              # print one profile as JSON (shows MBTI, zodiac, etc.)
start.bat --no-datasets          # skip dataset loading
start.bat --update               # clear dataset cache + re-download
```

### Pygame controls

| Key | Action |
|-----|--------|
| `SPACE` | Pause / resume |
| `N` | Force next tick |
| `+` / `-` | Speed (0.25×–4×) |
| Click | Select sim |
| `ESC` | Quit |

### API server
```bash
python server.py --sims 5 --port 8080
```
`GET /state` · `POST /tick` · `GET /sim/{id}` · `POST /interact` · `DELETE /reset` · `WS /stream`

---

## Project Structure

```
core/           Sim, Needs, EmotionState, SkillsSystem, WantsEngine, RelationshipGraph, MemoryStore
engine/         SimEngine, LOD (two-tier), scheduler, async adjudicator, event bus
identity/       profile_factory, faker_identity, ocean_scorer, emotion_classifier, mbti, zodiac
datasets/       24-field DatasetRegistry, all loaders, .sim_cache/
llm/            OllamaBackend, LlamaServerBackend, LlamaCppBackend, BackgroundLLMBackend
narrative/      career events, life events, gossip, story_writer, story_runner
tts/            TTSEngine (Supertonic, voice assignment, WAV output)
world/          venues, audio sensor, households, schedule, economy
sim_types/      Moodlet, Want, Fear, LODTier enums
persistence/    SQLite 5-table PersistenceLayer
pygame_app/     Pygame frontend (game loop, renderer, colours)
api/            FastAPI thin wrapper

sim_engine.py   Legacy monolithic implementation (reference only)
start.bat       CLI → python __main__.py %*
play.bat        Pygame → python pygame_app/main.py %*
server.bat      API → python server.py %*
```

---

## HuggingFace Models (10)

| Model | Purpose |
|-------|---------|
| `KevSun/Personality_LM` | RoBERTa — OCEAN from OkCupid essays |
| `Arash-Alborz/personality-trait-predictor` | DistilBERT — OCEAN for children (short text) |
| `cirimus/modernbert-base-go-emotions` | ModernBERT — 27-label GoEmotions multi-label classifier |
| `AnasAlokla/multilingual_go_emotions` | Multilingual BERT — emotion classifier fallback |
| `theta/MBTI-ckiplab-bert` | BERT — MBTI type inference from text |
| `sentence-transformers/static-retrieval-mrl-en-v1` | Static MRL — fast memory embeddings |
| `sentence-transformers/all-mpnet-base-v2` | MPNet — higher quality memory embeddings |
| `unsloth/Qwen3.5-9B-GGUF` | Primary LLM — ACTIVE tier adjudication |
| `unsloth/Ministral-3B-Instruct-2410-GGUF` | Background LLM — BACKGROUND tier |
| `Supertone/supertonic-3` | ONNX TTS — on-device speech, 10 voices |

## HuggingFace Datasets (24 sources across 24 registry fields)

| Dataset | System |
|---------|--------|
| `SpiceeChat/OkCupid-59k-Anonymized-Profiles` | OCEAN scoring |
| `allenai/prosocial-dialog` | Social norms in adjudicator prompt |
| `Estwld/atomic2020-origin` | Commonsense if/then reasoning |
| `allenai/social_i_qa` | Social reasoning QA calibration |
| `facebook/empathetic_dialogues` | Emotion-matched interaction seeds |
| `convai-challenge/conv_ai_2` | Quality-filtered dialogue seeds |
| `agentlans/multi-character-dialogue` | Multi-character dialogue seeds |
| `dair-ai/emotion` | Emotion calibration examples |
| `demelin/moral_stories` | Moral dilemma events |
| `ninoscherrer/moralchoice` | Ambiguous moral choices |
| `uwnlp/event2Mind` | Secondary emotional cascades after life events |
| `agentlans/li2017dailydialog` | Venue-matched topic seeds |
| `ShenLab/MentalChat16K` | Deep support interactions |
| `Amod/mental_health_counseling_conversations` | Counseling seed fallback |
| `nazlicanto/persona-based-chat` | OCEAN-matched few-shot persona examples |
| `allenai/social_bias_frames` | Norm violation detection |
| `hendrycks/ethics` | Commonsense + virtue ethics calibration |
| `OsamaBsher/AITA-Reddit-Dataset` | Community reputation verdicts |
| `yosrissa/AITA-posts-topics-dataset` | Topic-classified AITA scenarios |
| `agentlans/reddit-ethics` | Philosophical ethical dilemmas |
| `tee-oh-double-dee/social-orientation` | Circumplex social orientation labels |
| `Fraser/short-jokes` + `shuttie/reddit-dadjokes` | Comedy skill-gated joke content |
| `allenai/hippocorpus` | Memory texture + narrative style scaffolding |
| `Anthropic/persuasion` | Charisma-based persuasion modifier |
| `SocialGrep/one-million-reddit-confessions` | Confession + secret-sharing seeds |
| `llm-council/emotional_application` | EI scenario life events |
