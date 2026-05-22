# Sims Engine — Claude Code Guide

## How to run

```bash
# Demo — world integration test (no LLM server needed)
python run_demo.py                              # 6 sims, 20 beats, 30 sim-min/beat
python run_demo.py --beats 40 --sims 8 --dt 3600   # 8 sims, 40 beats, 1 sim-hr/beat

# Headless sim (legacy tick mode, still supported)
python __main__.py --sims 5                     # llama-server backend
python __main__.py --nats nats://localhost:4222 --room global  # distributed

# API server (real-time heartbeat loop starts automatically on /startup)
python server.py --sims 3 --port 8080
python server.py --backend llama-server --port 8081

# MTP realtime run (Qwen3.5-0.8B MTP via llama-server):
powershell -ExecutionPolicy Bypass -File "./run_realtime_tts_llamaserver.ps1"

python show_timings.py [http://localhost:8081]   # live /timings report
python audit_prompt.py                           # capture + print a real adjudicator prompt
```

## Architecture

Flat root layout (no package prefix). Key module boundaries:

```
core/         Pure sim state — Sim, Needs, EmotionState, Skills, Relationships,
              Memory, Sentiments, Compatibility, Moodlets, Illness,
              LifetimeWish, AspirationRewards, LifeStage (aging/death),
              ActionIntelligence, Consequences, ConversationArcPolicy,
              Intention (IntentionStack, Goal, SubGoal),
              Beliefs (BeliefGraph, CausalBelief),
              Rumor (RumorNetwork, HiddenRelationship),
              HardConsequenceEngine, CollateralEngine,
              Negotiation (NegotiationEngine, NegotiationSession),
              IdentityDrift (TraitDriftEngine),
              Appearance (SimAppearance, validate_appearance)

engine/       Simulation core — SimEngine, HeartbeatLoop (real-time),
              LOD tiers (lod.py), async adjudication (async_adj.py),
              BudgetedScheduler (budget.py), ShardManager (shard.py),
              AOIManager (aoi.py), PairFeatureCache (pair_cache.py),
              ChainBridge (chain_bridge.py), PressureIndex (pressure.py),
              NeuralInteractionPolicy + ActionProgram (neural_policy.py),
              InteractionObserver (observer.py), EventBus, NATS network,
              world_registry.py

blockchain/   SimChain (PoA, chain_id=13371) — block.py, chain.py,
              transaction.py, wallet.py (secp256k1 via eth_account),
              node.py (ChainNode, NATS broadcaster),
              eip712.py (EIP-712 typed data, MetaMask add-chain params),
              siwe.py (Sign-In With Ethereum challenge/verify),
              rpc.py (Ethereum JSON-RPC 2.0 for MetaMask connectivity)
              contracts/: SimCoin ($SIM ERC-20), ShopRegistry,
                          AgreementEngine, StockMarket (8 sectors)

identity/     Profile generation — OCEAN scoring, MBTI, zodiac, faker identity,
              EmotionClassifier (ModernBERT GoEmotions)

datasets/     DatasetRegistry (50+ fields), all loaders, .sim_cache/ (pickle)

llm/          Backends (LlamaServer/LlamaCpp/Mock), adjudicator (timeout +
              deterministic fallback), context builder, timing, mock_backend

narrative/    career, life_events, gossip, story_writer, story_runner,
              marriage (proposal/wedding/divorce), drama (cascade/witnesses),
              pregnancy (3-stage gestation arc), exporter.py

tts/          Supertonic wrapper, voice assignment, voice_catalog.py

world/        venues, households, clubs, social_events, weather, calendar,
              crafting, phone, gigs, property, shopping, pets, dynasty,
              burglar, grim_reaper, lot_layout, action_packs, context_sensors,
              neighborhoods, objects, career_manager, cleanliness, programming,
              cooking, wellness, skill_classes, life_states, dreams,
              bank (CityBank — term deposits, checking, ACID),
              web3_bridge (Web3Bridge — SimChain ↔ SimEngine)

analytics/    SimTracker (EventBus subscriber), EmergenceDashboard (report.py)

persistence/  SQLite layers:
                PersistenceLayer (sim_state.db — sims, relationships, events)
                FinancialLedger (sim_ledger.db — ACID, 57 TX types, anomaly flagging)
                EventLog (sim_state_events.db — snapshots + append-only deltas)
                AuthStore (sim_auth.db — users, sessions, PBKDF2 passwords)
                CityBank (sim_bank.db — accounts, term deposits)

api/          Minimal FastAPI wrapper (create_app(engine)) — alternative to server.py
sim_types/    Enums + lightweight types (Moodlet, Want, Fear, LODTier)
```

---

## Time model — real-time, not ticks

The engine runs on **wall-clock time**. There are no tick counters driving
game logic. Everything is measured in real seconds.

### Heartbeat loop (`engine/heartbeat.py`)

The server starts `HeartbeatLoop.run()` as an asyncio task at startup.
Each beat fires every `HEARTBEAT_INTERVAL` seconds (default 10 s) and:

1. Computes `dt = now − last_beat` (real elapsed seconds)
2. Decays sim needs proportionally: `need -= RATE_PER_SEC * dt`
3. Applies autonomous self-care (sleep, eat, bathroom, shower) scaled to dt
4. Checks bank deposit maturities
5. Evaluates collateral for sims below the trigger balance
6. Fires cadenced real-time events (see table below)
7. Ticks SimChain node + Web3Bridge
8. Reassigns LOD tiers
9. Attempts one LLM interaction pair if the queue is clear
10. Emits `heartbeat` bus event

**Need decay rates (true 1:1 real-time — depletes fully in)**

| Need | Rate/sec | Depletes in |
|------|----------|-------------|
| hunger | 0.00347 | 8 hours |
| energy | 0.00174 | 16 hours |
| social | 0.00116 | 24 hours |
| fun | 0.00231 | 12 hours |
| hygiene | 0.00039 | 72 hours |
| bladder | 0.00694 | 4 hours |
| comfort | 0.00058 | 48 hours |

**Cadenced events (real seconds between fires)**

| System | Interval |
|--------|----------|
| Gossip spread | 5 min (300 s) |
| Career events | 30 min (1 800 s) |
| Relationship decay | 1 hr (3 600 s) |
| Life events | 1 hr (3 600 s) |
| Venue rotation | 10 min (600 s) |
| Autosave | 5 min (300 s) |
| World snapshot | 10 min (600 s) |

### Legacy `run_tick()`

`run_tick()` is kept for backward compatibility and CLI mode. It calls the
underlying systems directly without real-time dt math. New code should prefer
`heartbeat.beat_once()` (manual single beat) or let the server's asyncio loop
drive the heartbeat automatically.

---

## _apply_resolved post-processing chain

After every adjudicated interaction:
- Apply friendship/romance deltas (sentiment-analysis modulated)
- Emotional contagion (spreads dominant emotion at friendship ≥ 35)
- Drama cascade (witnesses → gossip, sides, enemy-of-friend)
- Moodlet generation from valence
- Sentiment detection (betrayal, first_kiss, heartbreak, etc.)
- Consequence recording → social_strain update
- Marriage proposal check (romance ≥ 85 + first_love sentiment)
- Celebrity score bump
- **Trait drift** — `TraitDriftEngine.record()` records behavioral event
- **Belief update** — observer records sim_b's reaction into sim_a's BeliefGraph
- **Causal belief update** — "action → outcome" with confidence weighting
- **Emergence dashboard** — records interaction type for policy diversity
- **Rumor seeding** — high-valence events (|valence| > 0.7) propagate as rumors
- GoEmotions augmentation (ModernBERT)
- Memory write (ChromaDB / in-process)
- ACID ledger event log

---

## Financial system

### `engine._tx(sim, amount, tx_type, ...)` — the ONLY correct way to change simoleons

All 57 transaction types route through `_tx()`:
- Records to `FinancialLedger` (ACID SQLite — WAL + synchronous=FULL)
- `sim.simoleons` is mutated INSIDE the ledger transaction; if the DB write
  fails the balance is unchanged
- Mirrors to SimChain ($SIM mint/burn) asynchronously
- Triggers collateral evaluation if balance crosses −50

**Transaction taxonomy (select TX_* from `persistence/ledger.py`)**

| Category | Example types |
|----------|--------------|
| Income | `salary`, `gig_payout`, `property_dividend`, `stock_sale`, `crafting_royalty`, `inheritance`, `bank_interest`, `betting_win`, `programming_freelance` |
| Expense | `living_cost`, `shop_purchase`, `property_purchase`, `income_tax`, `bank_deposit`, `stock_purchase`, `household_bill`, `noise_fine` |
| Transfer | `gift_sent`, `gift_received`, `transfer_in`, `transfer_out` |
| System | `correction`, `chain_sync`, `bankruptcy_seizure` |

Anomaly thresholds per type (e.g. salary > £2 000 flagged); global flag ceiling £10 M.
Query via `GET /ledger/trace/{sim_id}`, `/ledger/anomalies`, `/ledger/top_earners`.

### City Bank (`world/bank.py` → `sim_bank.db`)

Each sim has a `BankAccount`. Deposits are locked in real time — the maturity
timestamp is a Unix timestamp, not a tick count. Early withdrawal raises an error.

| Term | Real duration | APR | §1 000 earns |
|------|--------------|-----|-------------|
| 1 Week | 7 days | 1.5% | §0.29 |
| 2 Weeks | 14 days | 2.5% | §0.96 |
| 1 Month | 30 days | 4.0% | §3.29 |
| 3 Months | 90 days | 6.0% | §14.79 |
| 1 Year | 365 days | 10.0% | §100.00 |

ACID: `open_deposit()` deducts from sim.simoleons AND inserts the deposit row
in a single `BEGIN IMMEDIATE` transaction. Rollback and compensation if either leg fails.

Heartbeat calls `bank.check_maturities(engine)` every beat; emits
`bank_deposit_matured` on the bus when a deposit crosses its `matures_at` timestamp.

### Collateral (`core/collateral.py`)

Fires from `_tx()` whenever `sim.simoleons < COLLATERAL_TRIGGER_BALANCE` (−50).

Asset classes evaluated:
- Properties → `current_value()` from PropertyManager
- Businesses → valuation table
- Bank deposits → principal × 95% (risk-free)
- Stock portfolio → `market_price × shares`
- On-chain $SIM balance

Credit extended = `SUM(asset_values) × 0.70`. Below `COLLATERAL_MARGIN_CALL`
(−500) forces asset liquidation at 70 cents on the dollar, proceeds credited via ledger.

---

## SimChain (blockchain)

**Chain ID:** 13371 (`0x343b`)
**Consensus:** Proof of Authority — server node is sole validator
**Block time:** every `CHAIN_BLOCK_INTERVAL` heartbeats (default 5)
**Wallet:** secp256k1 via `eth_account` — deterministic from `sim_id`

**Two wallet types per sim:**

| Wallet | Key | Used for |
|--------|-----|---------|
| Game wallet | `SimWallet.from_sim_id(sim_id)` — server holds key | All automatic transactions (gigs, shops, taxes, contracts) |
| MetaMask address | Player owns key — server never has it | SIWE authentication, EIP-712 player-initiated actions |

**MetaMask setup (frontend, once):**
```js
const params = await fetch('/chain/connect').then(r => r.json());
await window.ethereum.request({ method: 'wallet_addEthereumChain', params: [params] });
```

**Player login (SIWE flow):**
```js
const { message, nonce } = await fetch('/chain/challenge', {
  method: 'POST', body: JSON.stringify({ address })
}).then(r => r.json());
const sig = await window.ethereum.request({
  method: 'personal_sign', params: [message, address]
});
await fetch('/chain/verify', {
  method: 'POST',
  body: JSON.stringify({ address, signature: sig, message, nonce, token })
});
```

**Deployed contracts:** `simcoin` (ERC-20 $SIM), `shop_registry` (tamper-evident tx log),
`sim_agreement` (loans/employment/partnerships with on-chain enforcement),
`stock_market` (8 sectors, prices driven by sim world events).

---

## Authentication (`persistence/auth.py` → `sim_auth.db`)

On signup a sim is created and a `BankAccount` opened automatically.
Password hashing: PBKDF2-HMAC-SHA256, 100 000 iterations, 16-byte random salt.
Sessions: `secrets.token_urlsafe(32)`, 7-day TTL, stored in SQLite.
`sim_id` is UUID4 verified against both the live engine lookup and the auth DB before commit.

Endpoints: `POST /auth/signup|login|logout`, `GET /auth/me?token=…`,
`PUT /auth/sim/appearance|personality`, `GET /auth/options`.

---

## Closed-loop cognition systems

All instantiated in `SimEngine.__init__`, run each heartbeat beat:

| System | File | What it does |
|--------|------|-------------|
| `IntentionStack` | `core/intention.py` | Per-sim multi-tick goals (save money, repair marriage, start business). Scheduler reads `active_bias()` to steer interaction choice. |
| `BeliefGraph` | `core/beliefs.py` | Private facts + causal models per sim. Adjudicator uses A's beliefs about B, not ground truth. |
| `RumorNetwork` | `core/rumor.py` | Propagates rumors per hop with confidence decay; 10% mistaken-identity chance; hidden relationships leak by valence. |
| `HardConsequenceEngine` | `core/consequences_hard.py` | BLACKLISTED, BANKRUPT, EVICTED, CAREER_LOCKED_OUT, CUSTODY_LOST, PERMANENT_RIVALRY with expensive repair paths. |
| `InstitutionalSanctions` | `world/institutions.py` | HR (PIP → termination), Legal (debt collection), Union (hardship pay), NeighborhoodWatch (noise fines), TaxAuthority (income tax + audit). |
| `PressureIndex` | `engine/pressure.py` | Computes financial/romance/institutional/health tension per sim; synthesises novel events above threshold (no templates). |
| `NegotiationEngine` | `core/negotiation.py` | Offer/counter/accept protocol for jobs, loans, partnerships, custody, property. Accepted deals become on-chain SimAgreements. |
| `EmergenceDashboard` | `analytics/emergence.py` | Policy diversity (entropy), Gini inequality, social mobility (Spearman rank), conflict half-life, reconciliation rate, novelty score. |
| `TraitDriftEngine` | `core/identity_drift.py` | OCEAN traits drift from repeated behavior + trauma. Recovery arcs toward baseline. |
| `CollateralEngine` | `core/collateral.py` | Asset evaluation and credit extension when sim balance < −50. Margin call at −500. |

---

## Scalability architecture

| Layer | Implementation | Purpose |
|-------|---------------|---------|
| Shard topology | `engine/shard.py` ShardManager | One authoritative sim owner per zone; cross-shard events via EventBus |
| Budgeted ticks | `engine/budget.py` BudgetedScheduler | ACTIVE_SIMS_PER_TICK (8) processed fully per beat; rest get heuristic or minimal decay |
| AOI subscriptions | `engine/aoi.py` AOIManager | Per-client zone subscriptions; WebSocket pushes only nearby events |
| Pair cache | `engine/pair_cache.py` PairFeatureCache | TTL + version-stamp cache for attraction/risk scores; busted on rel delta |
| Event sourcing | `persistence/event_log.py` EventLog | Periodic snapshots + append-only deltas; `recover()` replays to current state |
| State diffs | `engine._build_state_diffs()` | NATS publishes only changed sims (hash-gated) |

---

## Key files

| File | What it does |
|------|-------------|
| `engine/engine.py` | SimEngine — `heartbeat`, `_tx()`, `_apply_resolved()`, `add_sim()`, `get_state()` |
| `engine/heartbeat.py` | `HeartbeatLoop` — real-time dt-based decay + cadenced events |
| `engine/scheduler.py` | `pick_interaction_pair()` + `choose_interaction()` — intention/reputation/memory/club gating |
| `engine/chain_bridge.py` | `ChainBridge` — on-chain $SIM mirror; never mutates simoleons directly |
| `blockchain/chain.py` | `SimChain` — PoA ledger, contract dispatch, nonce tracking |
| `blockchain/wallet.py` | `SimWallet` — secp256k1, deterministic from sim_id |
| `blockchain/eip712.py` | EIP-712 typed data, MetaMask `add_chain` params, `recover_signer()` |
| `blockchain/siwe.py` | SIWE challenge/verify (EIP-191 personal_sign, 2-min nonce TTL) |
| `blockchain/rpc.py` | Ethereum JSON-RPC handler (`eth_chainId`, `eth_getBalance`, `eth_call`) |
| `world/bank.py` | `CityBank` — 5 term deposits, checking account, ACID SQLite |
| `world/web3_bridge.py` | `Web3Bridge` — game wallet registry, MetaMask identity layer, $SIM auto-invest |
| `core/collateral.py` | `CollateralEngine` — asset evaluation, credit extension, margin call |
| `persistence/ledger.py` | `FinancialLedger` — ACID, 57 TX types, anomaly flagging, balance reconstruction |
| `persistence/auth.py` | `AuthStore` — PBKDF2 accounts, session tokens, sim_id uniqueness |
| `llm/adjudicator.py` | `call_adjudicator()` — hard timeout + deterministic fallback |
| `llm/mock_backend.py` | `MockLLMBackend` + `mock_adjudicate()` — Beta-distributed; no server needed |
| `core/appearance.py` | `SimAppearance` — 8 visual fields, validation, profile text for adjudicator |
| `config.py` | All constants — model IDs, rates, thresholds, bank terms, heartbeat intervals |
| `server.py` | FastAPI REST + WebSocket; heartbeat asyncio task started at /startup |
| `run_demo.py` | End-to-end world demo (no LLM server required) |

---

## All systems on SimEngine

```
# World / game systems
relationships, memory_store, wants_engine, gossip, milestones, opportunities,
ancestry, clubs, social_events, weather, crafting, phone, gigs, properties,
calendar, illness, drama, pregnancy, dream_system, career_manager, cleanliness,
programming, cooking, wellness, skill_classes, life_states, neighborhoods,
objects, lot_layout, grim_reaper, shopping, dynasties, burglar, neural_policy, pets

# Economy / finance
financial_ledger, bank, stocks, tokens, contracts_engine, ledger

# Blockchain
chain, chain_node, web3 (Web3Bridge)

# Cognition
rumor_network, hard_consequences, institutions, pressure_engine, negotiation,
emergence, trait_drift, collateral

# Infrastructure
heartbeat (HeartbeatLoop), _budget (BudgetedScheduler), _shard_manager,
aoi (AOIManager), _pair_cache, _event_log, _bridge (ChainBridge),
event_engine, aspiration_system, adaptive_policy, arc_policy
```

---

## LLM backends

| Backend | Flag | Notes |
|---------|------|-------|
| llama-server | `--backend llama-server` | **Default.** OpenAI-compatible; MTP supported |
| llama-cpp | `--backend llama-cpp` | In-process GGUF |
| mock | `MockLLMBackend()` directly | Deterministic Beta-distributed; use for dev/tests |

Hard timeout: `SIM_V2_ADJ_TIMEOUT` (default 8 s). On timeout → `mock_adjudicate(interaction)` deterministic fallback; game state preserved.

Key env vars: `SIM_V2_ADJ_MAX_TOKENS`, `SIM_V2_BG_LLM=0` (disable background LLM),
`SIM_V2_LLAMA_SERVER_URL`, `SIM_V2_LLAMA_SERVER_MODEL`.

---

## Conventions

- `sim_types/` not `types/` — stdlib clash avoidance
- All simoleon changes go through `engine._tx(sim, amount, tx_type, ...)` — never `sim.simoleons +=` directly
- New world systems: `world/mysystem.py` → instantiate in `SimEngine.__init__` → tick in `HeartbeatLoop._beat()` (not run_tick)
- Cadenced operations use `RT_*_INTERVAL` seconds constants, not tick counts
- Bank/collateral/auth all use Unix timestamps (`time.time()`) — never tick counts
- New `_apply_resolved` hooks: add after the trait drift block (search "Rumor seeding")
- `_current_engine` module-level ref in `engine/engine.py` — lets scheduler helpers reach clubs/celebrity without circular imports
- ChainBridge methods (`pay`, `charge`, `transfer`) are mirror-only — they update on-chain $SIM but do NOT mutate `sim.simoleons`
- MetaMask address ≠ game wallet — MetaMask is identity/consent layer; game wallet (deterministic secp256k1) handles all automatic transactions

---

## Common tasks

**Add a new interaction type:**
Edit `INTERACTION_TYPES` in `config.py` and add weight logic in `engine/scheduler.py::choose_interaction()`.

**Add a new transaction type:**
1. Add `TX_MY_TYPE = "my_type"` constant to `persistence/ledger.py`
2. Add to `_INCOME_TYPES` or `_EXPENSE_TYPES` frozenset in the same file
3. Add optional anomaly ceiling to `ANOMALY_CEILINGS` dict
4. Call `engine._tx(sim, amount, TX_MY_TYPE, description="...")` at the site

**Add a new world system (real-time):**
1. Create `world/mysystem.py` with a class
2. Instantiate in `SimEngine.__init__`
3. Call `.tick(engine)` (or inline logic) in `HeartbeatLoop._beat()` under the cadenced events section

**Add a new cadenced real-time event:**
1. Add `RT_MY_EVENT = N` seconds to `config.py`
2. Add `"my_event": now + RT_MY_EVENT` to `HeartbeatLoop.__init__` `self._next` dict
3. Add the fire condition to `HeartbeatLoop._fire_cadenced()`

**Add a new sim attribute:**
1. `core/sim.py::Sim.__init__`
2. `engine/engine.py::_sim_to_network_state()` (for NATS)
3. `engine/engine.py::get_state()` (for API)
4. `engine/world_registry.py::RemoteSimStub.__init__` (if scheduler needs it)

**Open a bank deposit from code:**
```python
dep = eng.bank.open_deposit(sim, "1_month", 500.0, engine)
# sim.simoleons already deducted; dep.matures_at is a Unix timestamp
```

**Check MetaMask wallet status:**
```python
info = eng.web3.wallet_info(sim_id)
# {"game_wallet": "0x...", "game_balance_sim": 871.5, "metamask_address": "0x...", "metamask_linked": True}
```

**Trace a sim's full financial history:**
```bash
curl http://localhost:8080/ledger/trace/{sim_id}
# Returns: income_by_source, expense_by_type, flagged_transactions, wealth_history
```

**Run the world demo:**
```bash
python run_demo.py --beats 40 --sims 8 --dt 3600
# Shows: wealth standings, ledger trace, bank state, stock market,
#        blockchain, needs, relationships, beliefs, pressure events,
#        collateral, institutions, emergence analytics, identity drift,
#        rumor network
```

---

## Server API surface (server.py)

**Time / scheduling**
- `GET /heartbeat/status` — loop status, next event times, decay rates per hour

**Auth**
- `POST /auth/signup|login|logout`, `GET /auth/me?token=…`
- `PUT /auth/sim/appearance|personality`, `GET /auth/options`
- `GET /auth/sim/{id}/id-check`, `GET /auth/stats`

**Wallet / MetaMask**
- `POST /wallet/nonce` — SIWE challenge (auth required)
- `POST /wallet/link` — verify personal_sign, link MetaMask to sim (auth required)
- `GET /wallet/status/{sim_id}`

**Blockchain (SimChain)**
- `POST /chain/rpc` — Ethereum JSON-RPC 2.0 (MetaMask endpoint)
- `GET /chain/connect` — `wallet_addEthereumChain` params for frontend
- `POST /chain/challenge|verify` — SIWE flow
- `GET /chain/status`

**Bank**
- `GET /bank/rates` — APR schedule and term options
- `GET /bank/account/{sim_id}` — checking + all deposits
- `POST /bank/deposit` — open term deposit (locked until maturity)
- `POST /bank/withdraw` — claim matured deposit
- `GET /bank/matured/{sim_id}` — deposits ready to collect
- `POST /bank/checking/deposit|withdraw` — liquid checking account
- `GET /bank/stats`

**Collateral**
- `GET /collateral/{sim_id}` — active record + liquidation history

**ACID Ledger**
- `GET /ledger/summary` — total txs, flagged, volume
- `GET /ledger/sim/{id}` — full transaction history
- `GET /ledger/sim/{id}/income` — income by source, expense by type
- `GET /ledger/sim/{id}/balance_at/{tick}` — reconstruct historical balance
- `GET /ledger/top_earners` — ranked by total income
- `GET /ledger/anomalies` — all flagged transactions
- `GET /ledger/trace/{id}` — complete wealth audit

**Economy**
- `GET /stocks`, `POST /stocks/buy|sell`
- `GET /ledger`, `GET /contracts`, `POST /contracts/loan|employment|partnership`
- `GET /economy/overview`, `GET /sim/{id}/portfolio`

**World**
- `GET /profile/{id}`, `POST /interact`, `GET /state`
- `GET /lot/{id}/layout|ambiance`
- `GET /investments/{id}`, `POST /investments/buy|collect|upgrade|rename|employee|sell`
- `GET /grim/status|tombstones`, `POST /grim/plead|chess|pet_save`
- `GET /burglar/status|log`, `POST /burglar/trigger`
- `POST /dynasty/create|heir|outcast|perk|alliance|rivalry`
- `GET /analytics/emergence`

**Multiplayer**
- `WS /stream` — pushes state JSON each heartbeat
- `POST /online/session|connect|disconnect`, `GET /online/sessions`
- `POST /online/room/join`
