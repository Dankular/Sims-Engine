# Sims Engine

An AI-powered life simulation where each Sim has a real personality profile derived from OkCupid essays scored through a language model. Social interactions are adjudicated by a local LLM, producing emergent relationships, emotions, fears, careers, and gossip.

## Features

- **OCEAN personality system** — profiles scored via `KevSun/Personality_LM` against real OkCupid essays
- **8 needs** — hunger, energy, social, fun, hygiene, environment, bladder, comfort — decay per tick and trigger moodlets
- **27-emotion model** — GoEmotions labels with neuroticism-modulated decay
- **LLM adjudicator** — every social interaction sent to a local LLM; outcome updates friendship, romance, skills, fears, and memories
- **Relationship graph** — friendship + romance scores with state labels (strangers → best friends, crush → partners)
- **Wants & fears** — aspiration-driven wants, fear acquisition from bad interactions, fear resolution through positive ones
- **Skills system** — charisma, comedy, cooking, fitness, logic, creativity; unlock special interactions at thresholds
- **Career & economy** — simoleons, pay periods, career performance drift, LLM-generated career events
- **Life events** — milestone-triggered (romance, friendship) and random narrative events via LLM
- **Gossip graph** — sims learn and spread social facts about each other
- **LOD tiers** — ACTIVE (full LLM), BACKGROUND (heuristic), DORMANT (minimal decay)
- **Async adjudication** — LLM calls run in a thread pool, sim ticks continue without blocking
- **SQLite persistence** — full state saved every 5 ticks (sims, relationships, events, households, gossip)
- **Dataset-seeded interactions** — EmpathDialogues, ConvAI2, ATOMIC commonsense, Social IQA, social norms from prosocial-dialog
- **FastAPI server** — `/state` and `/tick` endpoints for external clients (Unity, web, etc.)

## Requirements

```
pip install requests faker sentence-transformers chromadb fastapi uvicorn
```

For the LLM adjudicator, one of:

| Backend | Setup |
|---------|-------|
| **Ollama** (default) | `winget install Ollama.Ollama` → `ollama pull qwen3.5:9b` |
| **llama-server** | Run `llama-server` with an OpenAI-compatible endpoint |
| **llama-cpp** | `pip install llama-cpp-python` — auto-downloads GGUF from HuggingFace |

## Usage

```bash
# Default — 3 sims, 10 ticks, Ollama backend
start.bat

# More sims / ticks
start.bat --sims 5 --ticks 30

# Different backend
start.bat --backend llama-server --llama-url http://127.0.0.1:8080/v1/chat/completions

# Print one profile as JSON and exit
start.bat --profile

# Skip dataset loading (faster startup, less varied interactions)
start.bat --no-datasets

# Clear cached datasets and re-download
start.bat --update
```

Or directly:

```bash
python -m sim_v2 --sims 5 --ticks 20 --backend ollama
```

## Project Structure

```
sim_v2/
├── core/           # Sim, Needs, EmotionState, Skills, Wants, Relationships, Memory
├── engine/         # SimEngine, LOD, scheduler, async adjudicator, event bus
├── identity/       # Profile factory, Faker identity, OCEAN scorer
├── datasets/       # HuggingFace dataset loaders + local cache
├── llm/            # LLM backends (Ollama, llama-server, llama-cpp), adjudicator, schemas
├── narrative/      # Career events, life events, gossip graph
├── world/          # Venues, audio sensor, households, schedule, economy
├── persistence/    # SQLite persistence layer
└── api/            # FastAPI server

sim_engine.py       # Legacy monolithic implementation (reference)
start.bat           # Windows launcher
```

## HuggingFace Datasets Used

| Dataset | Purpose |
|---------|---------|
| `SpiceeChat/OkCupid-59k-Anonymized-Profiles` | Real essays for OCEAN personality scoring |
| `allenai/prosocial-dialog` | Social norms injected into adjudicator system prompt |
| `Estwld/atomic2020-origin` | Commonsense if/then reasoning for interaction context |
| `allenai/social_i_qa` | Social reasoning QA for adjudicator calibration |
| `facebook/empathetic_dialogues` | Emotion-matched interaction seeds |
| `convai-challenge/conv_ai_2` | Quality-filtered dialogue seeds |
| `agentlans/multi-character-dialogue` | Multi-character dialogue seeds |
| `dair-ai/emotion` | Emotion calibration examples |
| `KevSun/Personality_LM` | RoBERTa model for OCEAN scoring |
