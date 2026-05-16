# Sims Engine

An AI-powered life simulation where each Sim has a fully realised identity — OCEAN personality, MBTI type, zodiac sign, cultural background, fears, career, and relationships. Social interactions are adjudicated by a local LLM, producing deep emergent behaviour across dozens of independent systems: moral dilemmas, toxic relationship cycles, community reputation, skill-gated comedy and debate, creative expression, financial stress, cultural friction, and spoken story narration.

---

## Feature Overview

### Identity & Personality
| Feature | Detail |
|---------|--------|
| **OCEAN scoring** | `KevSun/Personality_LM` scores real OkCupid essays; children use `Arash-Alborz/personality-trait-predictor` on their self-summary |
| **MBTI type** | Deterministic OCEAN→MBTI + optional `theta/MBTI-ckiplab-bert` text inference; descriptor injected into every adjudication |
| **Zodiac sign + birthday** | DOB generated per sim; sign derived; OCEAN nudged ±0.05 per sign |
| **Cultural background** | Sampled from CultureBank cultural groups; inherited by children; cross-cultural friction in adjudicator |
| **27-emotion model** | GoEmotions labels, neuroticism-modulated decay, augmented post-adjudication by `cirimus/modernbert-base-go-emotions` |
| **8 needs** | Hunger, energy, social, fun, hygiene, environment, bladder, comfort; moodlets at critical thresholds |
| **Wants & fears** | Aspiration-driven wants; fear acquisition from trauma; resolution through positive interactions |

### Simulation Engine
| Feature | Detail |
|---------|--------|
| **LLM adjudicator** | Full profile block (OCEAN, MBTI, zodiac, cultural background, social orientation, reputation, EI + creative reputation, traits, fears, skills) sent per interaction |
| **Two-tier LLM LOD** | ACTIVE: primary 9B model; BACKGROUND: optional 3B model (`Ministral-3B`); DORMANT: minimal decay |
| **Async adjudication** | Thread pool; tick loop never blocks |
| **Skills system** | Charisma, comedy, cooking, fitness, logic, creativity — each grounded in dataset content |
| **Career & economy** | Simoleons, pay periods, mood-modulated career drift, LLM-generated career events |
| **Relationship graph** | 8 state labels: best friends → close friends → friends → acquaintances → strangers → dislike → **rivals** → enemies |
| **Romance graph** | Partners → dating → crush → none → repulsed |
| **Life events** | 9 types: milestone, conflict, celebration, loss, opportunity, financial_crisis, rivalry_escalation, creative_breakthrough, cultural_clash |
| **Gossip graph** | Sims learn and spread facts; AITA community verdicts attach to gossip |
| **Family system** | Full UUID sim IDs; children inherit OCEAN/traits/zodiac/cultural background; `parent_ids` on every profile |
| **Households** | 2–3 sims grouped; shared home venue |

### Behaviour Classes (7 Emergent Systems)

| Class | Dataset(s) | Emergent Behaviour |
|-------|-----------|-------------------|
| **Reputation & Community Judgment** | OsamaBsher/AITA · yosrissa/AITA · agentlans/reddit-ethics | YTA/NTA/ESH/NAH verdicts attach to gossip; `reputation_score` accumulates; POOR reputation triggers avoidance note |
| **Dynamic Social Orientation** | tee-oh-double-dee/social-orientation | 8 circumplex states per tick (Warm-Agreeable → Arrogant-Calculating); anger/low energy/joy each shift orientation |
| **Comedy Skill-Gated Jokes** | Fraser/short-jokes · shuttie/reddit-dadjokes | Skill 1–3: mediocre puns; skill 7+: sharp one-liners and dad jokes; failed jokes generate annoyance |
| **Memory Texture** | allenai/hippocorpus | Positive memory → vivid linear narration (recalled); traumatic → fragmented retold style |
| **Persuasion & Influence** | Anthropic/persuasion | `[CONVINCE]` interaction; charisma × target agreeableness modifier on friendship delta |
| **Deep Confessions** | SocialGrep/one-million-reddit-confessions | Fear/emotion-matched secrets; rejected → trauma; accepted → highest-valence memories |
| **Emotional Intelligence** | llm-council/emotional_application | EI scenarios as life events; `ei_reputation` score; high-agree sims respond well |

### Coded Gap Fills (6 Systems)

| Gap | Dataset(s) | Emergent Behaviour |
|-----|-----------|-------------------|
| **Logic Skill Debates** | ibm-research/argument_quality_ranking_30k · webis/args_me | Skill gates argument quality tier; weak arg + low-agree target → annoyance/disgust |
| **Cooking Skill Social** | cstrathe435/Task2Dial | Recipe teaching dialogues; diet compatibility check at dinner parties; vegan tension |
| **Creativity Output** | euclaise/writingprompts | Quality-tiered creative works; `creative_reputation` field; openness-calibrated reactions |
| **Toxic Relationship Dynamics** | audreyeleven/MentalManip · Maxwe11y/gaslighting | Love-bombing → devaluation → repair cycle; `in_toxic_cycle` on relationships; fear acquisition for targets |
| **Cultural Identity Layer** | SALT-NLP/CultureBank · NormBank · WORKBank | `cultural_background` on all sims; direct↔high-context friction 0.3; workplace norms in office venue |
| **Financial Stress** | bilalRahib/fiqa-personal-finance-dataset | Money-anxious seeds when broke; `financial_crisis` life event type; FiQA-grounded LLM narrative |

### Additional Dataset Behaviour
- **Moral dilemmas** — `demelin/moral_stories` + `ninoscherrer/moralchoice`; OCEAN decides path, guilt/pride follow
- **Mental health layer** — `ShenLab/MentalChat16K`; `[DEEP SUPPORT]` unlocks at friendship > 65 + active fears
- **Emotional cascades** — `uwnlp/event2Mind`; secondary xReact/oReact/xWant after life events
- **Venue-aware dialogue** — `agentlans/li2017dailydialog`; office → work, gym → health, nightclub → attitude
- **Ethics calibration** — `hendrycks/ethics`; commonsense + virtue examples in every adjudication
- **Social bias detection** — `allenai/social_bias_frames`; offensive interactions escalate to conflict
- **Persona consistency** — `nazlicanto/persona-based-chat`; OCEAN-matched few-shot examples in adjudicator context

### Narrative & Output
- **Story narration** — LLM writes narrator + dialogue script after each resolved interaction; hippocorpus narrative style scaffolding (recalled/retold/imagined) based on valence
- **Supertonic TTS** — on-device ONNX, per-sim voice assignment (F1=narrator; sims cycle M1–M5, F2–F5); WAV saved to `audio/`
- **Pygame frontend** — 1280×720; sim cards, world graph with relationship lines, event log, story panel; click to select sims
- **FastAPI server** — REST + WebSocket: `/state`, `/tick`, `/interact`, `/stream`
- **SQLite persistence** — 5 tables; auto-saved every 5 ticks

---

## Requirements

```bash
pip install requests faker sentence-transformers chromadb fastapi uvicorn pygame-ce supertonic sounddevice
```

| LLM Backend | Setup |
|-------------|-------|
| **Ollama** (default) | `winget install Ollama.Ollama` then `ollama pull qwen3.5:9b` |
| **llama-server** | Run with OpenAI-compatible endpoint |
| **llama-cpp** | `pip install llama-cpp-python` — auto-downloads GGUF |

Optional background LLM (Ministral-3B for BACKGROUND tier):
```bash
# Auto-downloads on first use with llama-cpp backend
# Disable: set SIM_V2_BG_LLM=0
```

---

## Usage

```bash
start.bat                        # 3 sims, 10 ticks, Ollama
play.bat                         # Pygame window
start.bat --sims 5 --ticks 30
start.bat --story                # LLM narration (text in terminal)
start.bat --story --tts          # + Supertonic audio
start.bat --backend llama-cpp    # in-process GGUF
start.bat --profile              # print one profile JSON (MBTI, zodiac, culture, etc.)
start.bat --no-datasets          # skip dataset loading
start.bat --update               # clear cache + re-download
```

**Pygame controls:** `SPACE` pause · `N` force tick · `+/-` speed · click select sim · `ESC` quit

**API:** `python server.py --sims 5 --port 8080`
→ `GET /state` · `POST /tick` · `GET /sim/{id}` · `POST /interact` · `DELETE /reset` · `WS /stream`

---

## Project Structure

```
core/           Sim, Needs, EmotionState, SkillsSystem, WantsEngine, RelationshipGraph, MemoryStore
engine/         SimEngine, LOD (two-tier), scheduler, async adjudicator, event bus
identity/       profile_factory, faker_identity, ocean_scorer, emotion_classifier, mbti, zodiac
datasets/       30-field DatasetRegistry, all loaders, .sim_cache/
llm/            OllamaBackend, LlamaServerBackend, LlamaCppBackend, BackgroundLLMBackend
narrative/      career events, life events, gossip, story_writer, story_runner
tts/            TTSEngine (Supertonic, voice assignment, WAV output)
world/          venues, audio sensor, households, schedule, economy
sim_types/      Moodlet, Want, Fear, LODTier enums
persistence/    SQLite 5-table PersistenceLayer
pygame_app/     Pygame frontend (game loop, renderer, colours)
api/            FastAPI thin wrapper

sim_engine.py   Legacy monolithic reference
start.bat       python __main__.py %*
play.bat        python pygame_app/main.py %*
server.bat      python server.py %*
```

---

## HuggingFace Models (10)

| Model | Purpose |
|-------|---------|
| `KevSun/Personality_LM` | RoBERTa — OCEAN from OkCupid essays |
| `Arash-Alborz/personality-trait-predictor` | DistilBERT — OCEAN for children |
| `cirimus/modernbert-base-go-emotions` | ModernBERT — 27-label GoEmotions post-adjudication |
| `AnasAlokla/multilingual_go_emotions` | Multilingual BERT — emotion classifier fallback |
| `theta/MBTI-ckiplab-bert` | BERT — MBTI inference from text |
| `sentence-transformers/static-retrieval-mrl-en-v1` | Static MRL — fast memory embeddings |
| `sentence-transformers/all-mpnet-base-v2` | MPNet — higher quality memory fallback |
| `unsloth/Qwen3.5-9B-GGUF` | Primary LLM — ACTIVE tier adjudication |
| `unsloth/Ministral-3B-Instruct-2410-GGUF` | Background LLM — BACKGROUND tier |
| `Supertone/supertonic-3` | ONNX TTS — on-device speech, 10 voices |

## HuggingFace Datasets (30+ sources)

| Dataset | System |
|---------|--------|
| `SpiceeChat/OkCupid-59k-Anonymized-Profiles` | OCEAN scoring |
| `allenai/prosocial-dialog` | Social norms in adjudicator |
| `Estwld/atomic2020-origin` | Commonsense if/then reasoning |
| `allenai/social_i_qa` | Social reasoning QA |
| `facebook/empathetic_dialogues` | Emotion-matched seeds |
| `convai-challenge/conv_ai_2` | Quality-filtered dialogue |
| `agentlans/multi-character-dialogue` | Multi-character seeds |
| `dair-ai/emotion` | Emotion calibration |
| `demelin/moral_stories` | Moral dilemma events |
| `ninoscherrer/moralchoice` | Ambiguous moral choices |
| `uwnlp/event2Mind` | Emotional cascade after life events |
| `agentlans/li2017dailydialog` | Venue-matched topic seeds |
| `ShenLab/MentalChat16K` | Deep support interactions |
| `Amod/mental_health_counseling_conversations` | Counseling seed fallback |
| `nazlicanto/persona-based-chat` | Persona consistency few-shot |
| `allenai/social_bias_frames` | Norm violation detection |
| `hendrycks/ethics` | Ethics calibration |
| `OsamaBsher/AITA-Reddit-Dataset` | Community reputation verdicts |
| `yosrissa/AITA-posts-topics-dataset` | Topic-classified AITA |
| `agentlans/reddit-ethics` | Philosophical dilemmas |
| `tee-oh-double-dee/social-orientation` | Circumplex social orientation |
| `Fraser/short-jokes` + `shuttie/reddit-dadjokes` | Comedy skill-gated jokes |
| `allenai/hippocorpus` | Memory texture + narrative style |
| `Anthropic/persuasion` | Charisma-based persuasion |
| `SocialGrep/one-million-reddit-confessions` | Confession seeds |
| `llm-council/emotional_application` | EI scenario life events |
| `ibm-research/argument_quality_ranking_30k` + `webis/args_me` | Logic skill debates |
| `cstrathe435/Task2Dial` | Cooking skill teaching dialogues |
| `euclaise/writingprompts` | Creativity skill output |
| `audreyeleven/MentalManip` + `Maxwe11y/gaslighting` | Toxic relationship dynamics |
| `SALT-NLP/CultureBank` + `NormBank` + `WORKBank` | Cultural identity layer |
| `bilalRahib/fiqa-personal-finance-dataset` | Financial stress behaviour |
