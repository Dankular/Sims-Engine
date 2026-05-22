from __future__ import annotations

import concurrent.futures
import logging
import os
import random
import secrets
from typing import TYPE_CHECKING, Any

from config import (
    ADJ_WORKERS,
    CAREER_EVENT_CHANCE,
    CAREER_EVENT_INTERVAL,
    GAME_START_HOUR,
    GOSSIP_SPREAD_CHANCE,
    LIFE_EVENT_CHANCE,
    LIFE_EVENT_INTERVAL,
    SOCIAL_NORMS_COUNT,
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
from engine.natives import NativeRegistry

if TYPE_CHECKING:
    from core.sim import Sim
    from datasets.loader import DatasetRegistry
    from llm.backend import LLMBackend
    from persistence.protocol import PersistenceBackend

logger = logging.getLogger(__name__)


def _to_float(val, default: float = 0.0) -> float:
    """Coerce an LLM result field to float. Handles 'positive'/'negative' strings."""
    try:
        return float(val)
    except (TypeError, ValueError):
        s = str(val).lower()
        if "positive" in s:
            return abs(default) or 3.0
        if "negative" in s:
            return -(abs(default) or 3.0)
        return default


# Module-level engine reference — lets scheduler helpers access clubs/celebrity
_current_engine: "SimEngine | None" = None


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
        from core.milestones import MilestoneRegistry
        from core.opportunities import OpportunityManager
        from core.ancestry import AncestryLedger

        self.milestones = MilestoneRegistry()
        self.opportunities = OpportunityManager()
        self.ancestry = AncestryLedger()

        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=ADJ_WORKERS)
        self._pending: list[PendingInteraction] = []

        self._audio_sensor = AudioEnvironmentSensor()
        self.venues_catalog = list(VENUES)
        self._venue: dict = {**random.choice(VENUES), **self._audio_sensor.sense()}
        self.natives = NativeRegistry(self)
        self.households: list = []

        # ── New systems ───────────────────────────────────────────────────────
        from world.clubs import ClubManager
        from world.social_events import SocialEventManager

        self.clubs = ClubManager()
        self.social_events = SocialEventManager()
        # Clubs are formed after households are assigned; deferred to first tick
        self._clubs_formed = False
        # Coworker assignment
        self._assign_coworkers()
        # Register self so scheduler helpers can reach clubs/celebrity data
        import engine.engine as _self_mod

        _self_mod._current_engine = self

        self._relationship_story_arcs: dict[tuple[str, str], dict[str, object]] = {}
        self._pregnancies: dict[str, object] = {}  # pregnancy_id → PregnancyRecord
        self._try_for_baby_intents: list[dict[str, str | int]] = []
        self._custody: dict[str, dict[str, object]] = {}
        self._calendar_events: list[dict[str, object]] = []

        # New systems (Tier 1 & 2 gaps)
        from world.weather import WeatherSystem
        from world.crafting import CraftingEngine
        from world.phone import PhoneSystem
        from world.gigs import GigManager
        from world.property import PropertyManager
        from world.calendar import GameCalendar
        from core.illness import IllnessSystem
        from narrative.drama import DramaCascade
        from narrative.pregnancy import PregnancySystem

        self.weather = WeatherSystem()
        self.crafting = CraftingEngine()
        self.phone = PhoneSystem()
        self.gigs = GigManager()
        self.properties = PropertyManager()
        self.calendar = GameCalendar(
            ticks_per_year=getattr(self, "_ticks_per_year", 365)
        )
        self.illness = IllnessSystem()
        self.drama = DramaCascade()
        self.pregnancy = PregnancySystem()

        # moodlets, career_id, career_days, _sleeping, _last_dream
        # are all initialised in Sim.__init__ — nothing to do here.

        from world.dreams import DreamSystem

        self.dream_system = DreamSystem()

        from world.career_manager import CareerManager as _CareerManager
        from world.cleanliness import CleanlinessSystem
        from world.programming import ProgrammingSystem
        from world.cooking import CookingSystem
        from world.wellness import WellnessSystem
        from world.skill_classes import SkillClassSystem
        from world.life_state import LifeStateSystem
        from world.neighborhoods import NeighborhoodSystem
        from world.objects import ObjectManager
        from world.shopping import ShoppingCenter
        from world.pets import PetManager
        from world.ledger import SimLedger
        from world.contracts import ContractEngine
        from world.stocks import StockMarket as WorldStockMarket
        from world.tokens import TokenEconomy
        from world.bookie import BookieSystem
        from config import MARKET_SHOPS
        from world.lot_layout import LotLayout
        from world.dynasty import DynastyManager
        from world.burglar import BurglarSystem
        from world.grim_reaper import GrimReaperNPC
        from engine.neural_policy import NeuralInteractionPolicy

        self.career_manager = _CareerManager()
        self.cleanliness = CleanlinessSystem()
        self.programming = ProgrammingSystem()
        self.cooking = CookingSystem()
        self.wellness = WellnessSystem()
        self.skill_classes = SkillClassSystem()
        self.life_states = LifeStateSystem()
        self.neighborhoods = NeighborhoodSystem()
        self.objects = ObjectManager()
        self.lot_layout = LotLayout()
        self.grim_reaper = GrimReaperNPC()
        self.shopping = ShoppingCenter()
        self.dynasties = DynastyManager()
        self.burglar = BurglarSystem()
        self.neural_policy = NeuralInteractionPolicy()
        self.pets = PetManager()
        self.ledger = SimLedger(block_interval=5)
        self.contracts_engine = ContractEngine()
        self.stocks = WorldStockMarket()
        self.tokens = TokenEconomy()
        self.bookie = BookieSystem(
            api_key=os.getenv("PANDASCORE_API_KEY", ""), poll_interval_ticks=5
        )
        self.wallet_nonces: dict[str, str] = {}
        self.sim_wallet_links: dict[str, dict] = {}
        self.wallet_mirror: dict[str, dict] = {}
        self.chain_intents: list[dict] = []
        for sim in self.sims:
            self.tokens.ensure_wallet(sim.sim_id)
        self._bus.on("gig_completed", self._on_gig_completed_economy)
        self._bus.on("economy.purchase", self._on_economy_purchase)
        self._bus.on("economy.trade", self._on_economy_trade)
        self._bus.on("economy.rent_income", self._on_economy_rent_income)
        self._bus.on("economy.gift", self._on_economy_gift)
        self._bus.on(
            "economy.contract_settlement", self._on_economy_contract_settlement
        )
        self._bus.on("economy.contract_breach", self._on_economy_contract_breach)
        self._bus.on("item_crafted", self._on_item_crafted_tokenization)
        self._bus.on("burglary_started", self._on_burglar_market_shock)
        self._bus.on("burglary_resolved", self._on_burglar_market_shock)
        self._bus.on("sim_died", self._on_grim_market_shock)

        # Seed world lots and sim inventories from object catalog (if available)
        lot_ids = [
            lot.lot_id
            for district in self.neighborhoods.world.districts
            for neighborhood in district.neighborhoods
            for lot in neighborhood.lots
        ]
        lot_rules = {
            lot.lot_id: {
                "type": lot.type,
                "venue_assignment": lot.venue_assignment,
            }
            for district in self.neighborhoods.world.districts
            for neighborhood in district.neighborhoods
            for lot in neighborhood.lots
        }
        self.objects.assign_world_objects(lot_ids, lot_rules=lot_rules)
        if self.shopping.lot_id not in self.objects.lot_object_stock:
            self.objects.assign_world_objects([self.shopping.lot_id], density=20)
        shop_ids = [s.get("lot_id") for s in MARKET_SHOPS if s.get("lot_id")]
        if shop_ids:
            shop_rules = {
                s["lot_id"]: {
                    "type": "business",
                    "venue_assignment": s.get("venue_assignment", "retail_store"),
                    "focus_types": list(s.get("focus", [])),
                    "strict_focus": True,
                }
                for s in MARKET_SHOPS
                if s.get("lot_id")
            }
            self.objects.assign_world_objects(
                shop_ids, density=22, lot_rules=shop_rules
            )
        for sim in self.sims:
            self.objects.assign_sim_inventory(sim)

        by_household: dict[str, list[str]] = {}
        for sim in self.sims:
            hid = str(getattr(sim, "household_id", "") or "")
            if hid:
                by_household.setdefault(hid, []).append(sim.sim_id)
        for hid, member_ids in by_household.items():
            if not member_ids:
                continue
            d = self.dynasties.create_dynasty(
                creator_id=member_ids[0],
                name=f"Household {hid[:8]}",
                description="Auto-created household dynasty",
                member_ids=member_ids[1:],
            )
            for sid in member_ids:
                sim_obj = self._sim_lookup.get(sid)
                if sim_obj:
                    self.dynasties.assign_sim(sim_obj, d.dynasty_id)

        # Central life event engine (P0 context-aware framework)
        from narrative.event_engine import EventEngine as _EventEngine

        self.event_engine = _EventEngine()
        from core.lifetime_aspirations import AspirationSystem

        self.aspiration_system = AspirationSystem()
        from core.adaptive_policy import AdaptiveBandit
        from core.conversation_arc_policy import ConversationArcPolicy

        self.adaptive_policy = AdaptiveBandit()
        self.arc_policy = ConversationArcPolicy()

        self._bus.on("tick_complete", self._on_tick_complete)
        assign_lod_tiers(self.sims)

        # ── Scalability systems ───────────────────────────────────────────────
        from engine.shard import ShardManager
        from engine.budget import BudgetedScheduler
        from engine.aoi import AOIManager
        from engine.pair_cache import PairFeatureCache
        from persistence.event_log import EventLog
        from config import (
            ACTIVE_SIMS_PER_TICK,
            BG_SIMS_PER_TICK,
            SNAPSHOT_INTERVAL,
            SIM_DB_PATH,
        )

        self._shard_manager = ShardManager(self._bus)
        self._budget = BudgetedScheduler(
            budget=ACTIVE_SIMS_PER_TICK, bg_budget=BG_SIMS_PER_TICK
        )
        self.aoi = AOIManager()
        self._pair_cache = PairFeatureCache()

        _event_log_path = SIM_DB_PATH.replace(".db", "_events.db")
        self._event_log = EventLog(_event_log_path)

        # Assign sims to shards by current lot / household
        self._sim_shard_cache: dict[str, str] = {}
        for sim in sims:
            shard_id = (
                str(getattr(sim, "current_lot_id", "") or "")
                or str(getattr(sim, "household_id", "") or "")
                or "global"
            )
            self._shard_manager.assign(sim.sim_id, shard_id)
            self._sim_shard_cache[sim.sim_id] = shard_id

        # Per-sim state hash for NATS compact diffs
        self._state_hash: dict[str, int] = {}

        # ── SimChain (blockchain) ─────────────────────────────────────────────
        from config import CHAIN_BLOCK_INTERVAL
        from blockchain.chain import SimChain
        from blockchain.wallet import SimWallet
        from blockchain.node import ChainNode
        from blockchain.contracts.simcoin import SimCoin
        from blockchain.contracts.shop_registry import ShopRegistry
        from blockchain.contracts.sim_agreement import AgreementEngine
        from blockchain.contracts.stock_market import StockMarket
        from world.web3_bridge import Web3Bridge

        _validator_wallet = SimWallet.from_label("validator")
        self.chain = SimChain(validator_address=_validator_wallet.address)

        # Deploy contracts
        self.chain.deploy(SimCoin())
        self.chain.deploy(ShopRegistry())
        self.chain.deploy(AgreementEngine())
        self.chain.deploy(StockMarket())

        self.chain_node = ChainNode(
            chain=self.chain,
            wallet=_validator_wallet,
            bus=self._bus,
            block_interval=CHAIN_BLOCK_INTERVAL,
        )
        self.web3 = Web3Bridge(self.chain)

        # Register wallets and mint genesis SimCoin from starting simoleons
        for sim in sims:
            self.web3.register_sim(sim.sim_id, initial_simoleons=sim.simoleons)

        logger.info(
            "[Engine] SimChain online — validator=%s, %d wallets",
            _validator_wallet.address[:12],
            len(sims),
        )

        # Network layer — None until attach_network() is called
        self._network = None
        self._current_room: str = "global"
        self._local_sim_ids: set[str] = {s.sim_id for s in sims}

        # ChainBridge — financial chokepoint routing simoleons ↔ $SIM
        from engine.chain_bridge import ChainBridge
        self._bridge = ChainBridge(self.web3)

        # ── ACID Financial Ledger ─────────────────────────────────────────────
        from persistence.ledger import FinancialLedger
        from config import SIM_DB_PATH
        _ledger_path = SIM_DB_PATH.replace(".db", "_ledger.db")
        self.financial_ledger = FinancialLedger(_ledger_path)
        logger.info("[Engine] ACID financial ledger → %s", _ledger_path)

        # ── City Bank + Collateral ────────────────────────────────────────────
        from world.bank import CityBank
        from core.collateral import CollateralEngine
        _bank_path = SIM_DB_PATH.replace(".db", "_bank.db")
        self.bank       = CityBank(_bank_path)
        self.collateral = CollateralEngine()

        # Create bank accounts for all starting sims
        for sim in sims:
            self.bank.ensure_account(sim.sim_id)

        # ── Real-time heartbeat loop ──────────────────────────────────────────
        from engine.heartbeat import HeartbeatLoop
        self.heartbeat = HeartbeatLoop(self)

        logger.info("[Engine] City Bank → %s | Heartbeat ready", _bank_path)

        # Give each sim a back-reference so world modules can reach the bridge
        for sim in sims:
            sim._engine_ref = self

        # ── Closed-loop cognition systems ────────────────────────────────────
        from core.intention import IntentionStack, maybe_generate_intention
        from core.beliefs import BeliefGraph
        from core.rumor import RumorNetwork
        from core.consequences_hard import HardConsequenceEngine
        from world.institutions import InstitutionalSanctions
        from engine.pressure import PressureIndex
        from core.negotiation import NegotiationEngine
        from analytics.emergence import EmergenceDashboard
        from core.identity_drift import TraitDriftEngine

        # Attach per-sim cognition state
        for sim in sims:
            if not hasattr(sim, "intentions"):
                sim.intentions = IntentionStack()
            if not hasattr(sim, "beliefs"):
                sim.beliefs = BeliefGraph()

        self.rumor_network       = RumorNetwork()
        self.hard_consequences   = HardConsequenceEngine()
        self.institutions        = InstitutionalSanctions()
        self.pressure_engine     = PressureIndex()
        self.negotiation         = NegotiationEngine()
        self.emergence           = EmergenceDashboard()
        self.trait_drift         = TraitDriftEngine()

        logger.info("[Engine] Closed-loop cognition systems initialised")

    # ── Unified financial transaction method ─────────────────────────────────
    #
    # _tx() is the SINGLE correct way to change sim.simoleons.
    # It writes to the ACID ledger FIRST, then updates sim.simoleons.
    # Direct sim.simoleons mutations elsewhere bypass the audit trail.

    def _tx(
        self,
        sim: "Sim",
        amount: float,
        tx_type: str,
        counterpart: str = "",
        description: str = "",
        metadata: dict | None = None,
        allow_overdraft: bool = False,
    ) -> bool:
        """
        Record + apply a financial transaction atomically.

        amount > 0 = income (salary, gig, dividend …)
        amount < 0 = expense (shop, tax, living cost …)

        Returns True on success, False if rejected (insufficient funds /
        ledger error). sim.simoleons is unchanged on False.
        Also mirrors to ChainBridge for on-chain $SIM accounting.
        """
        from persistence.ledger import InsufficientFundsError
        from config import COLLATERAL_TRIGGER_BALANCE
        if amount == 0.0:
            return True
        try:
            self.financial_ledger.record_tx(
                sim, amount, tx_type,
                tick=self._tick_count,
                counterpart=counterpart,
                description=description,
                metadata=metadata,
                allow_overdraft=allow_overdraft,
            )
        except InsufficientFundsError as exc:
            logger.debug("[_tx] Rejected: %s", exc)
            return False
        except Exception as exc:
            logger.warning("[_tx] Ledger error (%s %s %.2f): %s",
                           getattr(sim, "name", "?"), tx_type, amount, exc)
            # Fallback: still update simoleons so the game doesn't stall
            if amount > 0:
                sim.simoleons += amount
            else:
                sim.simoleons = max(-1_000_000.0, sim.simoleons + amount)
            return True

        # Mirror to chain (async, non-blocking — never blocks the tick)
        try:
            from persistence.ledger import _INCOME_TYPES, _EXPENSE_TYPES
            if amount > 0 and tx_type in _INCOME_TYPES:
                self._bridge.pay(sim, amount, tx_type)
            elif amount < 0 and tx_type in _EXPENSE_TYPES:
                self._bridge.charge(sim, abs(amount), tx_type)
        except Exception:
            pass

        # Collateral evaluation — fires when balance crosses the trigger
        if hasattr(self, "collateral") and sim.simoleons < COLLATERAL_TRIGGER_BALANCE:
            try:
                self.collateral.evaluate(sim, self)
            except Exception:
                pass

        return True

    def _tx_transfer(
        self,
        from_sim: "Sim",
        to_sim: "Sim",
        amount: float,
        tx_type_out: str = "",
        tx_type_in: str = "",
        counterpart_label: str = "",
        description: str = "",
        metadata: dict | None = None,
    ) -> bool:
        """
        Atomic peer-to-peer transfer. Both legs recorded; either both succeed
        or the whole operation is rejected (sender checked first).
        """
        from persistence.ledger import TX_TRANSFER_OUT, TX_TRANSFER_IN, InsufficientFundsError
        out_type = tx_type_out or TX_TRANSFER_OUT
        in_type  = tx_type_in  or TX_TRANSFER_IN
        label    = counterpart_label or from_sim.sim_id
        try:
            self.financial_ledger.record_tx(
                from_sim, -amount, out_type,
                tick=self._tick_count,
                counterpart=to_sim.sim_id,
                description=description or f"transfer → {to_sim.name}",
                metadata=metadata,
            )
            self.financial_ledger.record_tx(
                to_sim, amount, in_type,
                tick=self._tick_count,
                counterpart=from_sim.sim_id,
                description=description or f"transfer ← {from_sim.name}",
                metadata=metadata,
            )
        except InsufficientFundsError as exc:
            logger.debug("[_tx_transfer] Rejected: %s", exc)
            return False
        except Exception as exc:
            logger.warning("[_tx_transfer] Ledger error: %s", exc)
            return False
        # Chain mirror
        try:
            self._bridge.transfer(from_sim, to_sim, amount, description or out_type)
        except Exception:
            pass
        return True

    # ── Backward-compat shims (callers gradually migrated to _tx) ─────────────

    def _pay(self, sim: "Sim", amount: float, reason: str) -> None:
        from persistence.ledger import TX_SALARY
        self._tx(sim, abs(amount), reason if reason else TX_SALARY,
                 description=reason)

    def _charge(self, sim: "Sim", amount: float, reason: str,
                shop_name: str = "", item: str = "") -> bool:
        from persistence.ledger import TX_SHOP_PURCHASE
        tx_type = TX_SHOP_PURCHASE if reason == "shop_purchase" else reason
        meta = {"shop_name": shop_name, "item": item} if shop_name else None
        return self._tx(sim, -abs(amount), tx_type,
                        counterpart=shop_name or "",
                        description=f"{item} at {shop_name}" if item else reason,
                        metadata=meta)

    def _transfer_funds(self, from_sim: "Sim", to_sim: "Sim",
                        amount: float, reason: str) -> bool:
        return self._tx_transfer(from_sim, to_sim, amount, description=reason)

    @property
    def tick_count(self) -> int:
        return self._tick_count


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
                    "inventory": list(sim.inventory),
                    "inventory_objects": list(getattr(sim, "inventory_objects", [])),
                    "inventory_weight": self.objects.inventory_weight(sim),
                    "inventory_capacity": {
                        "max_slots": int(getattr(sim, "inventory_max_slots", 0)),
                        "max_weight": float(getattr(sim, "inventory_max_weight", 0.0)),
                        "slot_limits": dict(getattr(sim, "inventory_slot_limits", {})),
                    },
                    "properties": list(sim.properties),
                    "health_status": sim.health_status,
                    "temperature_risk": round(sim.temperature_risk, 2),
                    "internal_temperature": round(
                        getattr(sim, "internal_temperature", 0.0), 2
                    ),
                    "thermal_state": getattr(sim, "thermal_state", "comfortable"),
                    "perk_points": sim.perk_points,
                    "perks": sorted(sim.perks),
                    "active_gig": sim.active_gig,
                    "active_odd_job": sim.active_odd_job,
                    "odd_job_reputation": round(sim.odd_job_reputation, 1),
                    "hazard_flags": dict(sim.hazard_flags),
                    "last_threat_response": dict(
                        getattr(sim, "_last_threat_response", {})
                    ),
                    "owned_businesses": list(sim.owned_businesses),
                    "career_level": sim.career_level,
                    "career_branch": sim.career_branch,
                    "work_from_home_task": sim.work_from_home_task,
                    "school_performance": round(sim.school_performance, 1),
                    "homework_progress": round(sim.homework_progress, 1),
                    "scholarship_points": round(sim.scholarship_points, 1),
                    "university_readiness": round(sim.university_readiness, 1),
                    "university_status": sim.university_status,
                    "degree_track": sim.degree_track,
                    "degree_progress": round(sim.degree_progress, 1),
                    "occult_power": round(sim.occult_power, 1),
                    "occult_perks": list(sim.occult_perks),
                    "occult_weaknesses": list(sim.occult_weaknesses),
                    "pet_ids": list(sim.pet_ids),
                    "travel_history": list(sim.travel_history),
                    "pending_invitations": list(sim.pending_invitations),
                    "is_ghost": sim.is_ghost,
                    "occult_type": sim.occult_type,
                    "ocean": sim.profile["ocean"],
                    "household_id": sim.household_id,
                    "parent_ids": sim.parent_ids,
                    "married_to": getattr(sim, "_married_to", None),
                    "celebrity_tier": getattr(sim, "celebrity_tier", "none"),
                    "celebrity_score": round(getattr(sim, "celebrity_score", 0.0), 1),
                    "health_status": getattr(sim, "health_status", "healthy"),
                    "active_gig": sim.active_gig.gig_type
                    if getattr(sim, "active_gig", None)
                    else None,
                    "moodlets": sim.moodlets.active()
                    if hasattr(sim, "moodlets")
                    else [],
                    "dominant_moodlet": sim.moodlets.dominant_label()
                    if hasattr(sim, "moodlets")
                    else None,
                    "career": self.career_manager.career_summary(sim),
                    "programming_projects": self.programming.project_state(sim.sim_id),
                    "hacker_reputation": round(
                        getattr(sim, "hacker_reputation", 0.0), 2
                    ),
                    "last_meal_quality": self.cooking.last_meal_quality.get(sim.sim_id),
                    "wellness_state": self.wellness.state_for(sim.sim_id),
                    "certificates": self.skill_classes.certificates_for(sim.sim_id),
                    "ts4_emotion": sim.moodlets.ts4_emotion()
                    if hasattr(sim, "moodlets")
                    else "Fine",
                    "ts4_intensity": sim.moodlets.ts4_intensity()
                    if hasattr(sim, "moodlets")
                    else 0,
                    "ts4_color": sim.moodlets.ts4_color()
                    if hasattr(sim, "moodlets")
                    else "#e9e9e9",
                    "last_dream": getattr(sim, "_last_dream", None),
                    "property_count": len(getattr(sim, "properties", [])),
                    "property_value": round(
                        self.properties.total_portfolio_value(sim.sim_id), 2
                    ),
                    "crafted_items": len(getattr(sim, "crafted_inventory", [])),
                    "lifetime_wish": {
                        "description": sim.lifetime_wish.description,
                        "fulfilled": sim.lifetime_wish.fulfilled,
                        "progress": round(sim.lifetime_wish._progress_cache, 2),
                    }
                    if hasattr(sim, "lifetime_wish")
                    else None,
                    "lifetime_aspiration": {
                        "id": getattr(
                            getattr(sim, "lifetime_aspiration", None), "id", ""
                        ),
                        "category": getattr(
                            getattr(sim, "lifetime_aspiration", None), "category", ""
                        ),
                        "progress": round(
                            getattr(
                                getattr(sim, "lifetime_aspiration", None),
                                "progress",
                                0.0,
                            ),
                            3,
                        ),
                        "completion_state": bool(
                            getattr(
                                getattr(sim, "lifetime_aspiration", None),
                                "completion_state",
                                False,
                            )
                        ),
                    },
                    "aspiration_discoveries": list(
                        getattr(sim, "aspiration_discoveries", [])
                    ),
                    "aspiration_fulfillment": {
                        "life_satisfaction": round(
                            getattr(
                                getattr(sim, "aspiration_fulfillment", None),
                                "life_satisfaction",
                                50.0,
                            ),
                            2,
                        ),
                        "aligned_traits_bonus": round(
                            getattr(
                                getattr(sim, "aspiration_fulfillment", None),
                                "aligned_traits_bonus",
                                0.0,
                            ),
                            2,
                        ),
                        "failed_goals_penalty": round(
                            getattr(
                                getattr(sim, "aspiration_fulfillment", None),
                                "failed_goals_penalty",
                                0.0,
                            ),
                            2,
                        ),
                        "abandoned_goal_penalty": round(
                            getattr(
                                getattr(sim, "aspiration_fulfillment", None),
                                "abandoned_goal_penalty",
                                0.0,
                            ),
                            2,
                        ),
                    },
                    "generated_aspirations": list(
                        getattr(sim, "generated_aspirations", [])
                    ),
                    "completed_aspirations": list(
                        getattr(sim, "completed_aspirations", [])
                    ),
                    "knowledge_aspiration": {
                        "curiosity": round(
                            getattr(
                                getattr(sim, "knowledge_aspiration", None),
                                "curiosity",
                                0.0,
                            ),
                            3,
                        ),
                        "learning_drive": round(
                            getattr(
                                getattr(sim, "knowledge_aspiration", None),
                                "learning_drive",
                                0.0,
                            ),
                            3,
                        ),
                        "fearlessness": round(
                            getattr(
                                getattr(sim, "knowledge_aspiration", None),
                                "fearlessness",
                                0.0,
                            ),
                            3,
                        ),
                        "fulfillment": round(
                            getattr(
                                getattr(sim, "knowledge_aspiration", None),
                                "fulfillment",
                                0.0,
                            ),
                            2,
                        ),
                        "title": getattr(
                            getattr(sim, "knowledge_aspiration", None),
                            "title",
                            "",
                        ),
                    },
                    "club_count": len(getattr(sim, "club_ids", [])),
                    "dynasty_id": getattr(sim, "dynasty_id", None),
                    "dynasty_role": getattr(sim, "dynasty_role", "member"),
                    "known_events": self.event_engine.get_events_known_by(
                        sim.sim_id, limit=5
                    ),
                    "autonomy_profile": dict(getattr(sim, "autonomy_profile", {})),
                    "reward_traits": sorted(getattr(sim, "reward_traits", set())),
                    "death_traits": sorted(getattr(sim, "death_traits", set())),
                    "temporary_traits": sorted(getattr(sim, "temporary_traits", set())),
                    "formative_traits": sorted(getattr(sim, "formative_traits", set())),
                    "trait_knowledge": getattr(sim, "trait_knowledge", {}),
                    "autonomy_debug": getattr(sim, "_last_autonomy_choice", {}),
                    "adaptive_policy": self.adaptive_policy.debug_for(
                        sim.sim_id, limit=8
                    ),
                    "arc_policy": self.arc_policy.debug_sim(sim.sim_id),
                    "conversation_stage": getattr(
                        sim, "_conversation_stage", "small_talk"
                    ),
                    "conversation_stage_turns": getattr(
                        sim, "_conversation_stage_turns", 0
                    ),
                    "consent_state": getattr(sim, "_consent_state", {}),
                    "control_mode": str(getattr(sim, "control_mode", "autonomous")),
                    "player_action_queue": list(
                        getattr(sim, "player_action_queue", [])
                    ),
                    "current_directive": getattr(sim, "current_directive", None),
                    "species": getattr(sim, "occult_type", "human")
                    if not getattr(sim, "is_ghost", False)
                    else "ghost",
                    "life_state_data": self.life_states.state_for(sim),
                    "milestones": self.milestones.recent_for(sim.sim_id, limit=8),
                    "opportunities": self.opportunities.for_sim(sim.sim_id),
                    "ancestry": self.ancestry.lineage_snapshot(sim.sim_id),
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
            "pregnancies": list(self._pregnancies),
            "try_for_baby_intents": list(self._try_for_baby_intents),
            "custody": self._custody,
            "calendar_events": list(self._calendar_events),
            "clubs": [
                {
                    "name": c.name,
                    "interest": c.interest,
                    "venue": c.meeting_venue,
                    "members": c.member_ids,
                    "rule": c.rule,
                }
                for c in self.clubs.clubs
            ],
            "pending_events": len(self.social_events.get_pending()),
            "weather": self.weather.state_dict(),
            "room_cleanliness": self.cleanliness.room_state(),
            "skill_classes": self.skill_classes.classes_state(),
            "institution_reputation": dict(self.skill_classes.institution_reputation),
            "lecture_history": list(self.skill_classes.lecture_history[-25:]),
            "calendar": self.calendar.date_dict(self._tick_count),
            "active_pregnancies": len(self._pregnancies),
            "legacy": dict(self.aspiration_system.legacy),
            "world_sim": self.neighborhoods.state_dict(),
            "world_objects": {
                lot_id: self.objects.lot_state(lot_id)
                for lot_id in self.objects.lot_objects.keys()
            },
            "world_object_stock": {
                lot_id: dict(stock)
                for lot_id, stock in self.objects.lot_object_stock.items()
            },
            "market": self.objects.market_state(),
            "dynasties": self.dynasties.state(),
            "burglar": self.burglar.state(),
            "neural_policy": self.neural_policy.debug_state(),
            "pet_catalog_size": len(self.pets.list_catalog()),
            "ledger": self.ledger.state(),
            "contracts": self.contracts_engine.stats(),
            "stocks": self.stocks.state(),
            "tokens": self.tokens.state(),
            "bookie": self.bookie.state(),
        }

    def list_pet_catalog(self) -> list[dict]:
        return self.pets.list_catalog()

    def adopt_pet(self, sim_id: str, species: str | None = None) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        result = self.pets.adopt_pet(sim, species=species)
        if result.get("ok"):
            self._bus.emit(
                "pet_adopted", sim=sim, pet=result.get("pet"), tick=self._tick_count
            )
        return result

    def buy_pet(self, sim_id: str, species: str | None = None) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        result = self.pets.buy_pet(sim, species=species)
        if result.get("ok"):
            self._bus.emit(
                "pet_bought", sim=sim, pet=result.get("pet"), tick=self._tick_count
            )
        return result

    def feed_pet(self, sim_id: str, pet_id: str) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        result = self.pets.feed_pet(sim, pet_id=pet_id)
        if result.get("ok"):
            self._bus.emit(
                "pet_fed", sim=sim, pet=result.get("pet"), tick=self._tick_count
            )
        return result

    def pet_pet(self, sim_id: str, pet_id: str) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        result = self.pets.pet_pet(sim, pet_id=pet_id)
        if result.get("ok"):
            self._bus.emit(
                "pet_interaction",
                sim=sim,
                pet=result.get("pet"),
                tick=self._tick_count,
            )
        return result

    def play_with_pet(self, sim_id: str, pet_id: str) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        result = self.pets.play_with_pet(sim, pet_id=pet_id)
        if result.get("ok"):
            self._bus.emit(
                "pet_play", sim=sim, pet=result.get("pet"), tick=self._tick_count
            )
        return result

    def refill_pet_bowl(self, sim_id: str, lot_id: str) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        result = self.pets.refill_food_bowl(sim, self.lot_layout, lot_id)
        if result.get("ok"):
            self._bus.emit(
                "pet_bowl_refilled", sim=sim, lot_id=lot_id, tick=self._tick_count
            )
        return result

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
        if self._network:
            self._network.shutdown()
        self._pool.shutdown(wait=False)
        if self._db:
            self._db.close()

    # ── Network API ───────────────────────────────────────────────────────────

    def attach_network(self, network, room_id: str = "global") -> None:
        """
        Wire a NATSNetwork into this engine.  Call after construction.

        The network handles:
          - Broadcasting local sim states after every tick
          - Discovering remote sims from the registry for interaction pairing
          - Request-reply for cross-client social interactions
          - Syncing relationship deltas and gossip across clients
        """
        self._network = network
        self._current_room = room_id
        network.set_adjudicator(self._handle_remote_interaction_request)
        network.set_relationship_handler(self._on_network_relationship)
        network.set_gossip_handler(self._on_network_gossip)
        network.set_client_left_handler(
            lambda cid: logger.info("[Engine] client %s left", cid[:8])
        )
        logger.info("[Engine] Network attached — room='%s'", room_id)

    def _sim_to_network_state(self, sim) -> dict:
        """Richer state dict for NATS broadcasts — includes profile fields that
        RemoteSimStub needs to reconstruct a full proxy object."""
        p = sim.profile
        return {
            "id": sim.sim_id,
            "name": sim.name,
            "job": p.get("job", ""),
            "age": p.get("age", 25),
            "gender": p.get("gender", ""),
            "traits": p.get("traits", []),
            "reward_traits": sorted(getattr(sim, "reward_traits", set())),
            "death_traits": sorted(getattr(sim, "death_traits", set())),
            "temporary_traits": sorted(getattr(sim, "temporary_traits", set())),
            "formative_traits": sorted(getattr(sim, "formative_traits", set())),
            "autonomy_profile": dict(getattr(sim, "autonomy_profile", {})),
            "dealbreakers": p.get("dealbreakers", []),
            "aspiration": p.get("aspiration", ""),
            "humor_type": p.get("humor_type", ""),
            "comm_style": p.get("comm_style", ""),
            "attachment": p.get("attachment", ""),
            "interests": p.get("interests", []),
            "mbti": p.get("mbti", ""),
            "zodiac": p.get("zodiac", ""),
            "ocean": p.get("ocean", {}),
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
            "fears": [f.label for f in sim.fears],
            "skills": sim.skills.levels,
            "household_id": sim.household_id,
            "parent_ids": sim.parent_ids,
            "reputation_score": sim.reputation_score,
            "ei_reputation": sim.ei_reputation,
            "social_orientation": sim.social_orientation,
        }

    def _build_state_diffs(self) -> list[dict]:
        """
        Return only sims whose mutable state changed since the last publish.

        We hash the four most volatile fields (needs, emotion, simoleons, lod).
        Profile/trait fields are static enough to skip on every tick — they
        were already sent on first publish (hash=0 sentinel).
        """
        diffs: list[dict] = []
        for sim in self.sims:
            needs = sim.needs
            h = hash(
                (
                    round(needs.hunger, 0),
                    round(needs.energy, 0),
                    round(needs.social, 0),
                    round(needs.fun, 0),
                    sim.emotion.dominant,
                    round(sim.simoleons, 0),
                    sim.lod_tier.name,
                )
            )
            if self._state_hash.get(sim.sim_id) != h:
                self._state_hash[sim.sim_id] = h
                diffs.append(self._sim_to_network_state(sim))
        return diffs

    def _submit_remote_interaction(
        self, sim_a, target_sim_id: str, interaction: str
    ) -> None:
        """
        Called in thread-pool.  Sends an interaction request to the owner of
        target_sim_id via NATS request-reply, then applies the result locally.
        """
        if not self._network:
            return
        result = self._network.request_interaction(
            room_id=self._current_room,
            sim_a_state=self._sim_to_network_state(sim_a),
            target_sim_id=target_sim_id,
            action=interaction,
            venue=dict(self._venue),
            tick=self._tick_count,
        )
        if result and "error" not in result:
            self._apply_network_result(sim_a, target_sim_id, interaction, result)
        elif result:
            logger.warning("[Engine] remote interact failed: %s", result.get("error"))

    def _apply_network_result(
        self, sim_a, target_sim_id: str, interaction: str, result: dict
    ) -> None:
        """Apply initiator-side deltas after a cross-client interaction resolves."""
        fd = _to_float(result.get("friendship_delta", 0))
        rd = _to_float(result.get("romance_delta", 0))
        valence = max(-1.0, min(1.0, _to_float(result.get("valence", 0.5), 0.5)))

        rel = self.relationships.get(sim_a.sim_id, target_sim_id)
        rel.apply_deltas(fd, rd)

        emo_a = result.get("emotion_a", "")
        if emo_a:
            sim_a.emotion.add(emo_a, 0.7, duration=4, source=interaction)

        sim_a.needs.restore("social", _to_float(result.get("social_need_restore_a", 0)))
        sim_a.needs.restore("fun", _to_float(result.get("fun_restore_a", 0)))

        memory_tag = result.get("memory_tag", interaction)
        self.memory_store.write(
            sim_a.sim_id,
            target_sim_id,
            memory_tag,
            valence,
            tick=self._tick_count,
        )
        rel.add_memory(memory_tag, valence)

        # Publish rel delta so the target client (and other observers) sync
        if self._network:
            self._network.publish_relationship(
                self._current_room,
                sim_a.sim_id,
                target_sim_id,
                fd,
                rd,
                memory_tag,
                valence,
                self._tick_count,
            )

        self._bus.emit(
            "interaction_resolved",
            sim_a=sim_a,
            sim_b=None,  # remote — no local Sim object
            result=result,
            valence=valence,
            interaction=interaction,
            tick=self._tick_count,
        )
        logger.info(
            "[Net] %s → remote %s  [%s]  fd=%+.1f  val=%+.2f",
            sim_a.name,
            target_sim_id[:8],
            interaction[:30],
            fd,
            valence,
        )

    def _handle_remote_interaction_request(
        self, our_sim_id: str, request_data: dict
    ) -> dict:
        """
        Called in thread-pool by NATSNetwork when a remote sim initiates an
        interaction with one of our local sims (sim_b).  Runs a full LLM
        adjudication, applies deltas to sim_b, and returns the result dict.
        """
        sim_b = self._sim_lookup.get(our_sim_id)
        if sim_b is None:
            return {"error": f"sim {our_sim_id} not found locally"}

        # Build a proxy for the remote initiator
        initiator_state = request_data.get("initiator_state", {})
        sim_a_stub = self._network.registry.make_stub(initiator_state)
        action = request_data.get("action", "say hello")
        venue = request_data.get("venue", self._venue)

        rel = self.relationships.get(sim_a_stub.sim_id, sim_b.sim_id)
        memories = self.memory_store.recall(
            sim_b.sim_id, sim_a_stub.sim_id, query=action
        )
        norms: list[str] = []
        if self._datasets and self._datasets.social_norms:
            import random as _rnd

            norms = _rnd.sample(
                self._datasets.social_norms,
                min(SOCIAL_NORMS_COUNT, len(self._datasets.social_norms)),
            )

        system = build_adjudicator_system(
            norms, datasets=self._datasets, interaction=action
        )
        context_str = get_interaction_context(
            action,
            sim_a_stub,
            sim_b,
            datasets=self._datasets,
            memory_store=self.memory_store,
            current_tick=self._tick_count,
        )
        user_msg = self._build_user_message(
            sim_a_stub, sim_b, action, rel, memories, venue, context_str
        )

        try:
            result = call_adjudicator(self._llm, system, user_msg, interaction=action)
        except Exception as exc:
            logger.warning("[Engine] remote adjudication failed: %s", exc)
            return {"error": str(exc)}

        # Apply sim_b-side deltas locally
        fd = _to_float(result.get("friendship_delta", 0))
        rd = _to_float(result.get("romance_delta", 0))
        valence = max(-1.0, min(1.0, _to_float(result.get("valence", 0.5), 0.5)))
        try:
            self._witness_micro_effects(sim_a_stub, sim_b, valence)
        except Exception:
            pass
        rel.apply_deltas(fd, rd)

        emo_b = result.get("emotion_b", "")
        if emo_b:
            sim_b.emotion.add(emo_b, 0.7, duration=4, source=action)

        sim_b.needs.restore("social", _to_float(result.get("social_need_restore_b", 0)))
        sim_b.needs.restore("fun", _to_float(result.get("fun_restore_b", 0)))

        memory_tag = result.get("memory_tag", action)
        self.memory_store.write(
            sim_b.sim_id,
            sim_a_stub.sim_id,
            memory_tag,
            valence,
            tick=self._tick_count,
        )
        rel.add_memory(memory_tag, valence)

        # Broadcast the rel delta into the room
        if self._network:
            self._network.publish_relationship(
                self._current_room,
                sim_a_stub.sim_id,
                sim_b.sim_id,
                fd,
                rd,
                memory_tag,
                valence,
                self._tick_count,
            )

        logger.info(
            "[Net] remote %s → %s  [%s]  fd=%+.1f  val=%+.2f",
            sim_a_stub.name,
            sim_b.name,
            action[:30],
            fd,
            valence,
        )
        return result

    def _on_network_relationship(self, delta: dict) -> None:
        """Apply an incoming relationship delta from another client."""
        sim_a_id = delta.get("sim_a_id", "")
        sim_b_id = delta.get("sim_b_id", "")
        if not sim_a_id or not sim_b_id:
            return
        # Only apply if at least one side is our local sim (avoid double-apply
        # for interactions we originated or adjudicated ourselves)
        if sim_a_id in self._local_sim_ids or sim_b_id in self._local_sim_ids:
            return
        fd = _to_float(delta.get("friendship_delta", 0))
        rd = _to_float(delta.get("romance_delta", 0))
        rel = self.relationships.get(sim_a_id, sim_b_id)
        rel.apply_deltas(fd, rd)
        memory_tag = delta.get("memory_tag", "")
        valence = _to_float(delta.get("valence", 0.0))
        if memory_tag:
            rel.add_memory(memory_tag, valence)

    def _on_network_gossip(self, data: dict) -> None:
        """Apply incoming gossip spread from another client."""
        spreader_id = data.get("spreader_id", "")
        receiver_id = data.get("receiver_id", "")
        subject_id = data.get("subject_id", "")
        memory_tag = data.get("memory_tag", "")
        if spreader_id and receiver_id and subject_id:
            self.gossip.learn(receiver_id, subject_id, memory_tag)

    # ── Internal helpers ───────────────────────────────────────────────────────

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
                min(SOCIAL_NORMS_COUNT, len(self._datasets.social_norms)),
            )
        system = build_adjudicator_system(
            norms, datasets=self._datasets, interaction=interaction
        )
        # Systems 1 + 2: inject semantic memories + dialogue buffer into context
        context_str = get_interaction_context(
            interaction,
            sim_a,
            sim_b,
            datasets=self._datasets,
            memory_store=self.memory_store,
            current_tick=self._tick_count,
        )
        user_msg = self._build_user_message(
            sim_a, sim_b, interaction, rel, memories, venue, context_str
        )
        future = self._pool.submit(
            call_adjudicator, self._llm, system, user_msg, interaction
        )
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

    @staticmethod
    def _advance_conversation_stage(
        sim: "Sim",
        partner_id: str,
        rel,
        valence: float,
        current_tick: int,
        arc_mult: float = 1.0,
    ) -> None:
        """Advance or regress the multi-turn conversation arc for *sim* toward *partner_id*.

        Stages: small_talk → teasing → disclosure → affectionate_intent

        Transitions are driven by:
        - Relationship depth (friendship + romance thresholds)
        - Moodlet state (flirty/alluring accelerates romantic track)
        - Recent valence momentum (avg of last 3 buffer turns)
        - Explicit consent tracking (prevents re-escalation after rejection)
        - arc_mult: personality-adaptive multiplier from ConversationArcPolicy
          > 1.3 → lower dwell threshold by 1 turn (advance faster)
          < 0.7 → raise dwell threshold by 1 turn (stay cautious)
        """
        stage = getattr(sim, "_conversation_stage", "small_talk")
        turns = getattr(sim, "_conversation_stage_turns", 0)
        consent_map: dict = getattr(sim, "_consent_state", {})
        consent = consent_map.get(partner_id, "")

        friendship = rel.friendship
        romance = rel.romance

        # Personality-adaptive dwell adjustment
        dwell_adj = -1 if arc_mult > 1.3 else (1 if arc_mult < 0.7 else 0)

        # Moodlet check — flirty state lowers escalation thresholds
        is_flirty = False
        moodlets = getattr(sim, "moodlets", None)
        if moodlets is not None:
            try:
                is_flirty = any(
                    moodlets.has(k)
                    for k in ("flirty", "alluring", "in_the_mood", "love_is_in_the_air")
                )
            except Exception:
                pass

        # Recent valence momentum from buffer (weighted toward newest)
        buf = getattr(sim, "_dialogue_buffer", [])
        recent_v = [
            t.get("valence", valence)
            for t in buf[-3:]
            if (current_tick - t.get("tick", 0)) <= 15
        ]
        momentum = (sum(recent_v) / len(recent_v)) if recent_v else valence

        # ── Consent bookkeeping ───────────────────────────────────────────────
        if valence < -0.35 and stage in ("affectionate_intent", "teasing"):
            consent_map[partner_id] = "withdrawn"
        elif valence > 0.25 and romance >= 15 and consent != "withdrawn":
            consent_map[partner_id] = "given"
        sim._consent_state = consent_map

        # ── Regress on sustained negative momentum ────────────────────────────
        if momentum < -0.3:
            regress = {
                "affectionate_intent": "disclosure",
                "disclosure": "teasing",
                "teasing": "small_talk",
            }
            new = regress.get(stage, stage)
            sim._conversation_stage = new
            sim._conversation_stage_turns = 0
            return

        # ── Advance logic — dwell thresholds shifted by arc_mult ─────────────
        turns += 1
        new_stage = stage
        # Minimum turns in stage before advancing; clamped to [1, 4]
        dwell_teasing = max(1, 2 + dwell_adj)  # default 2
        dwell_disclosure = max(1, 2 + dwell_adj)  # default 2

        if stage == "small_talk":
            if (
                (friendship >= 15 or romance > 10 or is_flirty)
                and momentum > 0
                and turns >= max(1, 1 + dwell_adj)
            ):
                new_stage = "teasing"

        elif stage == "teasing":
            # Fast-track to affectionate if moodlet + romance without needing disclosure
            if romance >= 20 and is_flirty and momentum > 0.15:
                new_stage = "affectionate_intent"
            elif friendship >= 30 and momentum > 0.05 and turns >= dwell_teasing:
                new_stage = "disclosure"

        elif stage == "disclosure":
            if (
                romance >= 25
                and momentum > 0.05
                and turns >= dwell_disclosure
                and consent_map.get(partner_id) == "given"
            ):
                new_stage = "affectionate_intent"

        # affectionate_intent: persist until consent withdrawn (handled above)

        if new_stage != stage:
            sim._conversation_stage = new_stage
            sim._conversation_stage_turns = 0
        else:
            sim._conversation_stage = stage
            sim._conversation_stage_turns = turns

    def _apply_resolved(self, item: PendingInteraction, result: dict) -> None:
        sim_a = self._sim_lookup.get(item.sim_a_id)
        sim_b = self._sim_lookup.get(item.sim_b_id)
        if not sim_a or not sim_b:
            return

        rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
        fd = _to_float(result.get("friendship_delta", 0))
        rd = _to_float(result.get("romance_delta", 0))
        from core.traits import relationship_growth_multiplier

        fd *= relationship_growth_multiplier(sim_a, "friendship_gain")
        rd *= relationship_growth_multiplier(sim_a, "romance_gain")

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
                    labels = (
                        ekman_result[0]
                        if isinstance(ekman_result[0], list)
                        else ekman_result
                    )
                    surprise_score = next(
                        (
                            r["score"]
                            for r in labels
                            if r["label"].lower() == "surprise"
                        ),
                        0.0,
                    )
                    if surprise_score > 0.4:
                        fd = round(fd * (1.0 + surprise_score * 0.3), 2)
        except Exception:
            pass

        rel.apply_deltas(fd, rd)
        # Invalidate pair score cache after relationship delta
        self._pair_cache.bump_pair(sim_a.sim_id, sim_b.sim_id)

        valence = max(-1.0, min(1.0, _to_float(result.get("valence", 0.5), 0.5)))
        # Online adaptive policy feedback (phase 1 contextual bandit)
        try:
            reward = (
                fd * 0.6
                + rd * 0.4
                + valence * 6.0
                + _to_float(result.get("social_need_restore_a", 0)) * 0.05
                + _to_float(result.get("fun_restore_a", 0)) * 0.03
            )
            self.adaptive_policy.observe(sim_a.sim_id, item.interaction, reward)
            plan = getattr(sim_a, "_neural_plan", None)
            if plan and plan.get("social_action") == item.interaction:
                success = reward > 0.2
                self.neural_policy.observe(
                    sim_a, plan, reward=reward * 0.1, success=success
                )
        except Exception:
            pass

        sim_a.needs.restore("social", _to_float(result.get("social_need_restore_a", 0)))
        sim_a.needs.restore("fun", _to_float(result.get("fun_restore_a", 0)))
        sim_b.needs.restore("social", _to_float(result.get("social_need_restore_b", 0)))
        sim_b.needs.restore("fun", _to_float(result.get("fun_restore_b", 0)))

        emo_a = result.get("emotion_a", "")
        emo_b = result.get("emotion_b", "")
        if emo_a:
            sim_a.emotion.add(emo_a, 0.7, duration=4, source=item.interaction)
        if emo_b:
            sim_b.emotion.add(emo_b, 0.7, duration=4, source=item.interaction)

        if result.get("charisma_xp_a"):
            from core.traits import skill_gain_multiplier

            sim_a.skills.gain_xp(
                "charisma",
                float(result["charisma_xp_a"])
                * skill_gain_multiplier(sim_a, "charisma"),
            )
        if result.get("comedy_xp_a"):
            from core.traits import skill_gain_multiplier

            sim_a.skills.gain_xp(
                "comedy",
                float(result["comedy_xp_a"]) * skill_gain_multiplier(sim_a, "comedy"),
            )

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
                    sim.emotion.add(
                        classified[0], 0.75, duration=4, source="goemo_primary"
                    )
                    if len(classified) > 1:
                        sim.emotion.add(
                            classified[1], 0.35, duration=2, source="goemo_secondary"
                        )
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
        try:
            from core.consequences import record_consequence

            sim_a._last_consequence = record_consequence(
                sim_a, sim_b, rel, item.interaction, valence
            )
        except Exception:
            pass

        # ── Closed-loop cognition post-processing ─────────────────────────────

        # Trait drift: record behavioral event
        try:
            if hasattr(self, "trait_drift"):
                self.trait_drift.record(sim_a, item.interaction, valence)
        except Exception:
            pass

        # Belief graph: sim_a observes sim_b's reaction
        try:
            if hasattr(self, "_observe_interaction"):
                emotion_b = result.get("emotion_b", "")
                if emotion_b:
                    self._observe_interaction(
                        sim_a, sim_b.sim_id,
                        "expressed_emotion", emotion_b,
                        confidence=0.9,
                    )
                if valence < -0.4:
                    self._observe_interaction(
                        sim_a, sim_b.sim_id,
                        "reacted_negatively_to", item.interaction,
                        confidence=0.75,
                    )
        except Exception:
            pass

        # Causal belief update: "action → valence outcome"
        try:
            beliefs_a = getattr(sim_a, "beliefs", None)
            if beliefs_a:
                outcome = "positive_response" if valence > 0 else "negative_response"
                beliefs_a.update_causal(
                    item.interaction, sim_b.sim_id, outcome, valence, confidence=0.6
                )
        except Exception:
            pass

        # Emergence dashboard: record interaction type
        try:
            if hasattr(self, "emergence"):
                self.emergence.record_interaction(item.interaction)
        except Exception:
            pass

        # Rumor seed: high-valence events propagate as rumors
        try:
            if abs(valence) > 0.7 and hasattr(self, "rumor_network"):
                self.rumor_network.seed_rumor(
                    subject_id=sim_b.sim_id,
                    predicate=item.interaction,
                    object_=f"valence={valence:.2f}",
                    origin_id=sim_a.sim_id,
                    truth=True,
                    confidence=0.8,
                    sims=self.sims,
                )
        except Exception:
            pass

        # Capture arc state before buffer/stage advance (used in interaction_resolved emit)
        _obs_stage_a = getattr(sim_a, "_conversation_stage", "small_talk")
        _obs_arc_mult = 1.0

        # System 2: update dialogue buffer + advance conversation arc for both sims
        try:
            turn = {
                "speaker_a": sim_a.name,
                "speaker_b": sim_b.name,
                "content_a": item.interaction[:100],
                "content_b": result.get("sim_b_reaction", memory_tag)[:100],
                "emotion_a": emo_a,
                "emotion_b": emo_b,
                "valence": valence,
                "tick": self._tick_count,
            }
            _BUFFER_MAX = 6
            for sim, partner_id, partner_sim in (
                (sim_a, sim_b.sim_id, sim_b),
                (sim_b, sim_a.sim_id, sim_a),
            ):
                if sim._dialogue_partner != partner_id:
                    sim._dialogue_buffer = []
                    sim._dialogue_partner = partner_id
                    sim._conversation_stage = "small_talk"
                    sim._conversation_stage_turns = 0
                sim._dialogue_buffer.append(turn)
                sim._dialogue_buffer = sim._dialogue_buffer[-_BUFFER_MAX:]
                sim._dialogue_last_tick = self._tick_count

                # Capture stage before advance for policy learning
                stage_before = getattr(sim, "_conversation_stage", "small_talk")

                # Personality-adaptive multiplier from arc policy
                arc_mult = 1.0
                try:
                    arc_mult = self.arc_policy.stage_multiplier(
                        sim, partner_sim, rel, stage_before
                    )
                except Exception:
                    pass

                if sim is sim_a:
                    _obs_arc_mult = arc_mult

                self._advance_conversation_stage(
                    sim, partner_id, rel, valence, self._tick_count, arc_mult
                )

                # Online learning: update arc policy from this interaction
                try:
                    self.arc_policy.observe(
                        sim, partner_sim, rel, stage_before, valence, reward
                    )
                except Exception:
                    pass
        except Exception:
            pass

        resolve_fears(sim_a, valence)
        resolve_fears(sim_b, valence)
        if valence < -0.45:
            try:
                from core.knowledge_aspiration import register_knowledge_failure

                if sim_a.profile.get("aspiration") == "Knowledge":
                    register_knowledge_failure(
                        sim_a, severity=min(0.8, abs(valence) * 0.6)
                    )
                if sim_b.profile.get("aspiration") == "Knowledge":
                    register_knowledge_failure(
                        sim_b, severity=min(0.8, abs(valence) * 0.45)
                    )
            except Exception:
                pass
        new_fear = self.wants_engine.check_fear_acquisition(
            sim_a, item.interaction, valence
        )
        if new_fear and new_fear not in sim_a.fears:
            sim_a.fears.append(new_fear)

        self.gossip.learn(sim_a.sim_id, sim_b.sim_id, memory_tag)
        self.gossip.learn(sim_b.sim_id, sim_a.sim_id, memory_tag)
        from core.traits import discover_traits

        social_reveal = any(
            token in item.interaction.lower()
            for token in ("confide", "secret", "deep", "story", "advice")
        )
        discover_traits(sim_a, sim_b, rel.friendship, social_reveal=social_reveal)
        discover_traits(sim_b, sim_a, rel.friendship, social_reveal=social_reveal)
        if rel.friendship >= 45 and random.random() < GOSSIP_SPREAD_CHANCE:
            others = [
                s for s in self.sims if s.sim_id not in (sim_a.sim_id, sim_b.sim_id)
            ]
            if others:
                target = random.choice(others)
                self.gossip.spread(sim_a.sim_id, target.sim_id, sim_b.sim_id)
                self.gossip.spread_trait_gossip(sim_a, target, sim_b)

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
                        sim_a.name,
                        verdict,
                        sim_a.reputation_score,
                    )
                    # Gap 3: Reputation gossip propagation — scandal spreads
                    rep_drop = rep_before - sim_a.reputation_score
                    if rep_drop > 10:
                        scandal = (
                            f"{sim_a.name} was judged '{verdict}' after "
                            f"'{item.interaction[:40]}' — community is talking"
                        )
                        bystanders = [
                            s
                            for s in self.sims
                            if s.sim_id not in (sim_a.sim_id, sim_b.sim_id)
                        ][:3]
                        for bystander in bystanders:
                            self.gossip.learn(bystander.sim_id, sim_a.sim_id, scandal)
                        logger.info(
                            "[SCANDAL] %s rep drop %.0f → gossip spread to %d sims",
                            sim_a.name,
                            rep_drop,
                            len(bystanders),
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
                    "loss"
                    if "loss" in item.interaction.lower()
                    else "conflict"
                    if valence < -0.7
                    else "rejection"
                )
                apply_trauma_drift(sim_a, event_hint)
            except Exception:
                pass

        # Jealousy system — detect when a romantic interaction is observed by a partner
        if (
            "flirt" in item.interaction.lower()
            or "romantic" in item.interaction.lower()
        ):
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
                logger.debug(
                    "[JEALOUSY] %s reassured %s → score %.0f",
                    sim_a.name,
                    sim_b.name,
                    rel.jealousy_score,
                )
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
                        "sim_a": sim_a.name,
                        "sim_b": sim_b.name,
                        "action": item.interaction,
                        "valence": valence,
                        "friendship_delta": fd,
                        "romance_delta": rd,
                        "emotion_a": result.get("emotion_a", ""),
                        "emotion_b": result.get("emotion_b", ""),
                        "dialogue": result.get("dialogue", ""),
                        "sim_b_reaction": result.get("sim_b_reaction", ""),
                        "memory_tag": memory_tag,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to log interaction event: %s", exc)

        # Print live dialogue to stdout
        import re as _re

        _dialogue = result.get("dialogue", "")
        _reaction = result.get("sim_b_reaction", "")
        _ea = result.get("emotion_a", "")
        _eb = result.get("emotion_b", "")
        if _dialogue or _reaction:
            _sign = "+" if valence >= 0 else ""
            _venue = getattr(self, "_venue", {}).get("name", "")
            _mood = ":)" if valence >= 0 else ":("
            print(
                "\n  -- "
                + sim_a.name
                + " & "
                + sim_b.name
                + "  ["
                + item.interaction
                + "]  "
                + _venue
                + "  F"
                + ("+" if fd >= 0 else "")
                + str(round(fd, 1))
                + "  "
                + _mood,
                flush=True,
            )
            if _dialogue:
                for _ln in _re.split(r"\s*/\s*", _dialogue.strip()):
                    _ln = _ln.strip()
                    if not _ln:
                        continue
                    _m = _re.match(r"^(\w+):\s*(.+)$", _ln)
                    if _m:
                        _spk = _m.group(1)
                        _txt = _m.group(2).strip("”'")
                        _emo = ""
                        if _spk.lower() == sim_a.name.split()[0].lower() and _ea:
                            _emo = " [" + _ea + "]"
                        elif _spk.lower() == sim_b.name.split()[0].lower() and _eb:
                            _emo = " [" + _eb + "]"
                        print("    " + _spk + _emo + ": “" + _txt + "”", flush=True)
                    else:
                        print("    " + _ln, flush=True)
            if _reaction:
                print("    *" + _reaction + "*", flush=True)
            print(flush=True)

        logger.info(
            "[Tick %d] RESOLVED [%s]: %s -> %s (%s) fd=%+.1f rd=%+.1f valence=%.2f",
            self._tick_count,
            item.interaction_id,
            sim_a.name,
            sim_b.name,
            item.interaction,
            fd,
            rd,
            valence,
        )
        # ── Rumour disproven check ────────────────────────────────────────────
        if valence > 0.65 and any(
            kw in item.interaction.lower()
            for kw in ("clear the air", "defend", "confront", "reconcile", "prove")
        ):
            try:
                from narrative.event_triggers import _rumour_pool
                from core.event_record import EventType as _ET, Visibility as _V
                from narrative.event_templates import build_consequences as _bc
                from core.event_record import LifeEvent as _LE

                for rumour in list(_rumour_pool):
                    if rumour.get("subject_id") == sim_a.sim_id:
                        _rumour_pool.remove(rumour)
                        c = _bc(
                            _ET.RUMOUR_DISPROVEN,
                            sim_a.sim_id,
                            [sim_b.sim_id],
                            self,
                            extra={"spreader_id": sim_b.sim_id},
                        )
                        ev = _LE.make(
                            _ET.RUMOUR_DISPROVEN,
                            sim_a.sim_id,
                            f"{sim_a.name} successfully disproved the rumour against them.",
                            self._tick_count,
                            secondary_sim_ids=[sim_b.sim_id],
                            visibility=_V.PUBLIC,
                            valence=+0.7,
                            intensity=0.6,
                            duration_ticks=20,
                            consequences=c,
                            source="resolved_interaction",
                        )
                        self.event_engine.process(ev, self)
                        break
            except Exception:
                pass

                # ── LLM-suggested life event ──────────────────────────────────────────
        suggested = result.get("suggested_event")
        if suggested and isinstance(suggested, dict):
            try:
                from narrative.event_triggers import EventTriggerSystem

                ev = EventTriggerSystem.from_llm_suggestion(
                    sim_a.sim_id, sim_b.sim_id, suggested, self
                )
                if ev:
                    self.event_engine.process(ev, self)
                    logger.info(
                        "[LLM Event] %s suggested: %s",
                        sim_a.name,
                        ev.narrative[:60],
                    )
            except Exception as exc:
                logger.debug("[LLM Event] parse failed: %s", exc)

        # ── Drama cascade ─────────────────────────────────────────────────────
        self.drama.on_resolved(sim_a, sim_b, valence, item.interaction, self)

        # ── Moodlet generation from interaction outcomes ───────────────────────
        interaction_lower = item.interaction.lower()
        is_romantic = any(
            w in interaction_lower
            for w in (
                "flirt",
                "kiss",
                "romance",
                "woo",
                "love",
                "date",
                "cuddle",
                "confess",
            )
        )
        is_creative = any(
            w in interaction_lower
            for w in (
                "paint",
                "music",
                "write",
                "art",
                "craft",
                "sing",
                "guitar",
                "piano",
            )
        )
        is_conflict = any(
            w in interaction_lower
            for w in (
                "argue",
                "fight",
                "insult",
                "accuse",
                "betray",
                "cheat",
                "yell",
                "mock",
            )
        )
        is_fun = any(
            w in interaction_lower
            for w in ("joke", "play", "game", "laugh", "tease", "prank", "silly")
        )

        if hasattr(sim_a, "moodlets"):
            if valence > 0.8:
                sim_a.moodlets.add("on_a_roll", source=item.interaction)
                if is_romantic:
                    sim_a.moodlets.add("head_over_heels", source=item.interaction)
            elif valence > 0.5:
                sim_a.moodlets.add("just_had_fun", source=item.interaction)
                if is_romantic:
                    sim_a.moodlets.add("flirty", source=item.interaction)
                if is_creative:
                    sim_a.moodlets.add("inspired", source=item.interaction)
                if is_fun:
                    sim_a.moodlets.add("feeling_playful", source=item.interaction)
            elif valence > 0.3:
                sim_a.moodlets.add("good_vibes", source=item.interaction)
            elif valence < -0.7:
                sim_a.moodlets.add(
                    "furious" if is_conflict else "stressed", source=item.interaction
                )
            elif valence < -0.4:
                sim_a.moodlets.add(
                    "irritated" if is_conflict else "uncomfortable",
                    source=item.interaction,
                )

        if hasattr(sim_b, "moodlets"):
            if valence > 0.8:
                sim_b.moodlets.add("deeply_connected", source=item.interaction)
                if is_romantic:
                    sim_b.moodlets.add("enchanted", source=item.interaction)
            elif valence > 0.5:
                sim_b.moodlets.add("just_had_fun", source=item.interaction)
                if is_romantic:
                    sim_b.moodlets.add("flirty", source=item.interaction)
            elif valence < -0.7:
                sim_b.moodlets.add(
                    "mortified" if is_conflict else "stressed", source=item.interaction
                )
            elif valence < -0.4:
                sim_b.moodlets.add("embarrassed", source=item.interaction)

        # ── Skill gain from interaction content ───────────────────────────────
        for _sim in (sim_a, sim_b):
            if hasattr(_sim, "skills"):
                for skill, amount in _sim.skills.gains_from_interaction(
                    item.interaction
                ):
                    from core.traits import skill_gain_multiplier

                    leveled = _sim.skills.gain_xp(
                        skill, amount * skill_gain_multiplier(_sim, skill)
                    )
                    if leveled and hasattr(_sim, "moodlets"):
                        max_lv = _sim.skills.levels.get(skill, 0) >= 10
                        _sim.moodlets.add(
                            "skill_mastered" if max_lv else "proud",
                            source=f"skill_levelup:{skill}",
                        )

        # ── Sentiments ────────────────────────────────────────────────────────
        from core.sentiments import detect_sentiment, add_sentiment

        triggered = detect_sentiment(
            item.interaction,
            valence,
            result,
            rel.friendship,
            rel.romance,
            self._tick_count,
            sim_a=sim_a,
            sim_b=sim_b,
        )
        for sent_name in triggered:
            if add_sentiment(rel, sent_name, self._tick_count, source=item.interaction):
                logger.info("[Sentiment] %s↔%s: +%s", sim_a.name, sim_b.name, sent_name)
                if sent_name in {"first_kiss", "first_love", "betrayal", "heartbreak"}:
                    if self.milestones.grant(
                        sim_a.sim_id,
                        sent_name,
                        self._tick_count,
                        source="interaction_sentiment",
                        meta={"with": sim_b.sim_id},
                    ):
                        sim_a.milestones.append(
                            {"id": sent_name, "tick": self._tick_count}
                        )

        self._trait_evolution_tick(sim_a, item.interaction, valence)
        self._trait_evolution_tick(sim_b, result.get("sim_b_reaction", ""), valence)

        # ── Marriage proposal check ───────────────────────────────────────────
        if (
            "propose" in item.interaction.lower()
            and valence > 0.6
            and rel.romance >= 85
            and not getattr(sim_a, "_married_to", None)
            and not getattr(sim_b, "_married_to", None)
        ):
            from narrative.marriage import marry

            marry(sim_a, sim_b, self)

        if (
            (
                "try for baby" in item.interaction.lower()
                or "baby" in item.interaction.lower()
            )
            and rel.romance >= 70
            and valence >= 0.15
        ):
            self._try_for_baby_intents.append(
                {
                    "parent_a_id": sim_a.sim_id,
                    "parent_b_id": sim_b.sim_id,
                    "resolve_at": self._tick_count + 1,
                }
            )

        # ── Celebrity score boost from positive high-valence interactions ─────
        if valence > 0.7 and rel.friendship >= 60:
            sim_a.celebrity_score = min(100, sim_a.celebrity_score + 0.5)
        if valence > 0.8:
            sim_b.celebrity_score = min(100, sim_b.celebrity_score + 0.3)

        # ── Emotional contagion ───────────────────────────────────────────────
        # Close sims absorb a fraction of each other's post-interaction emotion.
        # Strength scales linearly with friendship above the threshold.
        self._apply_emotional_contagion(sim_a, sim_b, rel)

        try:
            self.dynasties.on_interaction(self, sim_a, sim_b, item.interaction, valence)
        except Exception:
            pass

        self._bus.emit(
            "interaction_resolved",
            sim_a=sim_a,
            sim_b=sim_b,
            result=result,
            valence=valence,
            friendship_delta=fd,
            romance_delta=rd,
            stage_before=_obs_stage_a,
            stage_after=getattr(sim_a, "_conversation_stage", "small_talk"),
            arc_mult=_obs_arc_mult,
            tick=self._tick_count,
            interaction_id=item.interaction_id,
            interaction=item.interaction,
        )

    def _trait_evolution_tick(self, sim: "Sim", text_hint: str, valence: float) -> None:
        hint = text_hint.lower()
        history = getattr(sim, "action_history", {})
        if "argue" in hint or "insult" in hint:
            history["conflict"] = history.get("conflict", 0) + 1
        if "confide" in hint or "advice" in hint or "help" in hint:
            history["supportive"] = history.get("supportive", 0) + 1
        if "creative" in hint or "art" in hint or "music" in hint:
            history["creative"] = history.get("creative", 0) + 1
        if valence < -0.75:
            history["trauma"] = history.get("trauma", 0) + 1
        sim.action_history = history

        if history.get("conflict", 0) >= 6 and "hot-headed" not in sim.profile.get(
            "traits", []
        ):
            sim.add_trait("hot-headed", source="temporary")
        if history.get("supportive", 0) >= 7 and "good" not in sim.profile.get(
            "traits", []
        ):
            sim.add_trait("good", source="formative")
        if history.get("creative", 0) >= 7 and "creative" not in sim.profile.get(
            "traits", []
        ):
            sim.add_trait("creative", source="reward")
        if history.get("trauma", 0) >= 4 and "skeptical" not in sim.profile.get(
            "traits", []
        ):
            sim.add_trait("skeptical", source="temporary")

    def _wire_event_bus(self) -> None:
        """
        Subscribe to existing bus events and route them through EventEngine
        so they gain visibility propagation, consequence application, and
        per-sim memory storage.
        """
        from core.event_record import LifeEvent, EventType, Visibility
        from narrative.event_templates import build_consequences

        def _make_and_process(
            event_type,
            primary_sim,
            secondary_sims,
            narrative,
            visibility,
            valence,
            intensity,
            duration,
            extra=None,
        ):
            primary_id = getattr(primary_sim, "sim_id", "") if primary_sim else ""
            secondary_ids = [getattr(s, "sim_id", "") for s in secondary_sims if s]
            if not primary_id:
                return
            c = build_consequences(
                event_type, primary_id, secondary_ids, self, extra=extra or {}
            )
            ev = LifeEvent.make(
                event_type=event_type,
                primary_sim_id=primary_id,
                secondary_sim_ids=secondary_ids,
                narrative=narrative,
                tick=self._tick_count,
                visibility=visibility,
                valence=valence,
                intensity=intensity,
                duration_ticks=duration,
                consequences=c,
                source="bus_redirect",
            )
            self.event_engine.process(ev, self)

        # Marriage
        self._bus.on(
            "married",
            lambda **kw: _make_and_process(
                EventType.MARRIAGE,
                kw.get("sim_a"),
                [kw.get("sim_b")],
                f"{getattr(kw.get('sim_a'), 'name', '?')} and {getattr(kw.get('sim_b'), 'name', '?')} got married!",
                Visibility.PUBLIC,
                +0.9,
                0.9,
                30,
            ),
        )
        # Divorce
        self._bus.on(
            "divorced",
            lambda **kw: _make_and_process(
                EventType.DIVORCE,
                kw.get("sim_a"),
                [kw.get("sim_b")],
                f"{getattr(kw.get('sim_a'), 'name', '?')} and {getattr(kw.get('sim_b'), 'name', '?')} divorced.",
                Visibility.HOUSEHOLD,
                -0.7,
                0.8,
                25,
            ),
        )
        # Child born
        self._bus.on(
            "child_born",
            lambda **kw: _make_and_process(
                EventType.BIRTH,
                kw.get("parent_a"),
                [kw.get("parent_b"), kw.get("child")],
                f"{getattr(kw.get('child'), 'name', '?')} was born to "
                f"{getattr(kw.get('parent_a'), 'name', '?')} and {getattr(kw.get('parent_b'), 'name', '?')}!",
                Visibility.HOUSEHOLD,
                +0.9,
                0.9,
                30,
                extra={"child_id": getattr(kw.get("child"), "sim_id", "")},
            ),
        )
        # Sim died
        self._bus.on(
            "sim_died",
            lambda **kw: _make_and_process(
                EventType.DEATH,
                kw.get("sim"),
                [],
                f"{getattr(kw.get('sim'), 'name', '?')} has passed away at age {kw.get('age', '?')}.",
                Visibility.PUBLIC,
                -0.9,
                1.0,
                60,
            ),
        )
        # Illness update → sick
        self._bus.on(
            "illness_update",
            lambda **kw: (
                _make_and_process(
                    EventType.ILLNESS,
                    kw.get("sim"),
                    [],
                    f"{getattr(kw.get('sim'), 'name', '?')} fell ill ({kw.get('status', '?')}).",
                    Visibility.HOUSEHOLD,
                    -0.5,
                    0.6,
                    12,
                    extra={
                        "severity": getattr(kw.get("sim"), "illness_severity", "mild")
                    },
                )
                if kw.get("status") == "sick"
                else None
            ),
        )
        # Gig completed
        self._bus.on(
            "gig_completed",
            lambda **kw: (
                _make_and_process(
                    EventType.GIG_SUCCESS,
                    kw.get("sim"),
                    [],
                    f"{getattr(kw.get('sim'), 'name', '?')} completed '{kw.get('label', 'a gig')}' (§{kw.get('pay', 0):.0f}).",
                    Visibility.HOUSEHOLD,
                    +0.6,
                    0.5,
                    10,
                )
                if kw.get("success")
                else None
            ),
        )
        # Property purchased
        self._bus.on(
            "property_purchased",
            lambda **kw: _make_and_process(
                EventType.PROPERTY_BOUGHT,
                kw.get("sim"),
                [],
                f"{getattr(kw.get('sim'), 'name', '?')} bought {kw.get('property_name', 'a property')}.",
                Visibility.CLUB,
                +0.7,
                0.6,
                15,
            ),
        )
        # Wish fulfilled
        self._bus.on(
            "wish_fulfilled",
            lambda **kw: _make_and_process(
                EventType.WISH_FULFILLED,
                kw.get("sim"),
                [],
                f"{getattr(kw.get('sim'), 'name', '?')} fulfilled their lifetime wish!",
                Visibility.PUBLIC,
                +1.0,
                1.0,
                40,
            ),
        )
        # Milestone achieved
        self._bus.on(
            "milestone_achieved",
            lambda **kw: _make_and_process(
                EventType.MILESTONE,
                kw.get("sim"),
                [],
                f"{getattr(kw.get('sim'), 'name', '?')} reached milestone: {kw.get('milestone', '?')}.",
                Visibility.CLUB,
                +0.6,
                0.5,
                15,
            ),
        )
        # Holiday
        self._bus.on(
            "holiday", lambda **kw: None
        )  # holidays handled by CalendarSystem already

    def enqueue_player_action(
        self, sim_id: str, action: str, target_sim_id: str | None = None
    ) -> bool:
        sim = self._sim_lookup.get(sim_id)
        if not sim:
            return False
        sim.player_action_queue.append(
            {"action": action, "target_sim_id": target_sim_id}
        )
        sim.control_mode = "queued"
        return True

    def interrupt_sim(self, sim_id: str, reason: str = "manual") -> bool:
        sim = self._sim_lookup.get(sim_id)
        if not sim:
            return False
        sim.current_directive = None
        sim.player_action_queue.clear()
        sim.control_mode = "interrupted"
        sim.emotion.add("annoyance", 0.2, duration=2, source=f"interrupt:{reason}")
        return True

    def _process_control_directives(self) -> None:
        if self._pending:
            return
        for sim in self.sims:
            if str(getattr(sim, "control_mode", "autonomous")) not in (
                "queued",
                "player_directed",
            ):
                continue
            queue = getattr(sim, "player_action_queue", [])
            if not queue:
                if str(getattr(sim, "control_mode", "autonomous")) == "queued":
                    sim.control_mode = "autonomous"
                continue
            directive = queue.pop(0)
            target_id = directive.get("target_sim_id")
            action = str(directive.get("action", "chat"))
            target = self._sim_lookup.get(target_id) if target_id else None
            if target is None:
                others = [o for o in self.sims if o.sim_id != sim.sim_id]
                if not others:
                    continue
                target = random.choice(others)
            sim.current_directive = directive
            sim.control_mode = "player_directed"
            self._submit_interaction(sim, target, action, self._venue)
            if not queue:
                sim.control_mode = "autonomous"
            break

    def _run_neural_planning(self, active_only: bool = True) -> None:
        sims = self.sims
        if active_only:
            sims = [
                s
                for s in self.sims
                if s.lod_tier == LODTier.ACTIVE and not getattr(s, "_sleeping", False)
            ]
        for sim in sims:
            plan = self.neural_policy.plan_for_sim(self, sim)
            if not plan:
                continue
            if plan.get("action") == "use_item" and plan.get("object_id") is not None:
                outcome = self.use_item(sim.sim_id, int(plan["object_id"]))
                success = bool(outcome.get("ok"))
                effect = outcome.get("effect", {}) if success else {}
                restore = float(effect.get("restore", 0.0))
                reward = (restore * 0.12) + (0.3 if success else -0.35)
                self.neural_policy.observe(sim, plan, reward, success)
                if success:
                    self.neural_policy.stats["uses"] += 1

    def _process_neural_consequences(self) -> None:
        for evt in self.neural_policy.pop_consequences(limit=24):
            sim = self._sim_lookup.get(str(evt.get("sim_id", "")))
            if sim is None:
                continue
            typ = str(evt.get("type", ""))
            intensity = float(evt.get("intensity", 0.3))
            if typ == "mentorship_opportunity":
                others = [s for s in self.sims if s.sim_id != sim.sim_id]
                if others:
                    target = max(others, key=lambda x: x.skills.levels.get("logic", 0))
                    self.relationships.get(sim.sim_id, target.sim_id).apply_deltas(
                        2.0 * intensity, 0.0
                    )
                    sim.emotion.add(
                        "inspired", 0.4, duration=3, source="mentor_opportunity"
                    )
                    mentor_knowledge = self.neural_policy.debug_state().get(
                        "discovered_affordances", {}
                    )
                    target.profile.setdefault("known_affordances", {})
                    for need, types in mentor_knowledge.items():
                        cur = set(target.profile["known_affordances"].get(need, []))
                        cur.update(types[:2])
                        target.profile["known_affordances"][need] = sorted(cur)
            elif typ == "trust_debt":
                sim.emotion.add("pride", 0.3, duration=3, source="trust_debt")
                did = str(getattr(sim, "dynasty_id", "") or "")
                if did:
                    d = self.dynasties.dynasties.get(did)
                    if d:
                        d.unity = min(100.0, d.unity + 0.8 * intensity)
            elif typ == "scandal_ripple":
                did = str(getattr(sim, "dynasty_id", "") or "")
                if did:
                    d = self.dynasties.dynasties.get(did)
                    if d:
                        d.prestige_points = max(
                            0.0, d.prestige_points - 0.8 * intensity
                        )
                        d.unity = max(0.0, d.unity - 1.2 * intensity)
            elif typ == "rivalry_escalation":
                peers = [s for s in self.sims if s.sim_id != sim.sim_id]
                if peers:
                    target = random.choice(peers)
                    self.relationships.get(sim.sim_id, target.sim_id).apply_deltas(
                        -2.2 * intensity, 0.0
                    )
            elif typ == "relationship_milestone":
                sim.emotion.add(
                    "hope", 0.35, duration=3, source="relationship_milestone"
                )
            elif typ == "memory_bonding":
                sim.emotion.add("nostalgia", 0.3, duration=3, source="memory_bonding")
            self._bus.emit(
                "neural_consequence", sim=sim, payload=evt, tick=self._tick_count
            )

    def _risk_counterplay_tick(self) -> None:
        # Ecology loop: if burglar pressure rises, households organically invest in defense.
        burglar_state = self.burglar.state()
        recent = burglar_state.get("recent_events", [])
        unresolved = [
            e
            for e in recent
            if e.get("type") == "burglary_resolved" and e.get("outcome") == "escaped"
        ]
        if len(unresolved) < 2:
            return
        for sim in self.sims:
            if sim.simoleons < 300:
                continue
            if random.random() < 0.05:
                # Buy defensive utility when market supports it
                self.neural_policy._try_store_acquire(self, sim, need="hygiene")

    def _world_event_memory(
        self,
        sims: list,
        tag: str,
        valence: float = 0.0,
        gossip: bool = False,
    ) -> None:
        participants = [s for s in sims if s is not None]
        for sim in participants:
            for other in participants:
                if sim.sim_id == other.sim_id:
                    continue
                try:
                    self.memory_store.write(
                        sim.sim_id,
                        other.sim_id,
                        tag,
                        float(valence),
                        tick=self._tick_count,
                    )
                except Exception:
                    pass
                try:
                    rel = self.relationships.get(sim.sim_id, other.sim_id)
                    rel.shared_memory_count = (
                        int(getattr(rel, "shared_memory_count", 0)) + 1
                    )
                except Exception:
                    pass
        if (
            gossip
            and len(participants) >= 2
            and len(self.sims) >= 3
            and random.random() < 0.55
        ):
            spreader = participants[0]
            receiver = participants[1]
            pool = [
                s
                for s in self.sims
                if s.sim_id not in {spreader.sim_id, receiver.sim_id}
            ]
            if pool:
                subject = random.choice(pool)
                self.gossip.learn(receiver.sim_id, subject.sim_id, tag)

    def _witness_micro_effects(self, sim_a: Sim, sim_b: Sim, valence: float) -> None:
        witnesses = [
            s
            for s in self.sims
            if s.sim_id not in {sim_a.sim_id, sim_b.sim_id}
            and s.lod_tier == LODTier.ACTIVE
            and not getattr(s, "_sleeping", False)
        ]
        if not witnesses:
            return
        sample = random.sample(witnesses, k=min(2, len(witnesses)))
        for w in sample:
            if valence >= 0:
                w.emotion.add(
                    "warmth", 0.1, duration=2, source="witness_positive_social"
                )
                self.relationships.get(w.sim_id, sim_a.sim_id).apply_deltas(0.2, 0.0)
            else:
                w.emotion.add(
                    "unease", 0.12, duration=2, source="witness_negative_social"
                )
                self.relationships.get(w.sim_id, sim_a.sim_id).apply_deltas(-0.25, 0.0)

    def _pair_memory_count(self, sim_a_id: str, sim_b_id: str) -> int:
        key = f"{min(sim_a_id, sim_b_id)}_{max(sim_a_id, sim_b_id)}"
        store = getattr(self.memory_store, "_store", {})
        return int(len(store.get(key, [])))

    def buy_item(self, sim_id: str, lot_id: str, object_id: int, qty: int = 1) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        before_bal = float(sim.simoleons)
        ok = self.objects.buy_object(sim, lot_id, int(object_id), int(qty))
        if not ok:
            return {"ok": False, "reason": "buy_failed"}
        spend = max(0.0, before_bal - float(sim.simoleons))
        self._emit_economy_event(
            "economy.purchase",
            sim_id=sim_id,
            lot_id=lot_id,
            object_id=int(object_id),
            qty=int(qty),
            spent=round(spend, 2),
        )
        plan = getattr(sim, "_neural_plan", None)
        purchase = plan.get("purchase") if isinstance(plan, dict) else None
        if (
            isinstance(plan, dict)
            and isinstance(purchase, dict)
            and int(purchase.get("object_id", -1)) == int(object_id)
        ):
            self.neural_policy.observe(sim, plan, reward=0.2, success=True)

        # Link economy purchases to placeable home security state.
        try:
            inv = list(getattr(sim, "inventory_objects", []))
            placed = False
            for item in inv:
                if int(item.get("id", -1)) != int(object_id):
                    continue
                name = str(item.get("name", "")).lower()
                typ = str(item.get("type", "")).lower()
                if (
                    ("alarm" in name)
                    or ("security" in name)
                    or typ in {"tool", "weapon", "armor"}
                ):
                    home = str(getattr(sim, "household_id", "") or "")
                    if home:
                        zone = (
                            "garage"
                            if typ in {"tool", "weapon", "armor"}
                            else "living_room"
                        )
                        p = self.lot_layout.place(home, zone, item)
                        if p.get("ok"):
                            sim.inventory_objects = [
                                o
                                for o in inv
                                if int(o.get("id", -1)) != int(object_id)
                                or o is not item
                            ]
                            sim.inventory = [o["name"] for o in sim.inventory_objects]
                            placed = True
                    break
            if placed:
                self._bus.emit(
                    "object_placed_home",
                    sim=sim,
                    lot_id=str(getattr(sim, "household_id", "") or ""),
                    object_id=int(object_id),
                    tick=self._tick_count,
                )
        except Exception:
            pass
        self._bus.emit(
            "object_bought",
            sim=sim,
            lot_id=lot_id,
            object_id=int(object_id),
            qty=int(qty),
            tick=self._tick_count,
        )
        return {
            "ok": True,
            "sim_id": sim_id,
            "lot_id": lot_id,
            "object_id": int(object_id),
            "qty": int(qty),
            "simoleons": round(sim.simoleons, 2),
            "inventory_weight": self.objects.inventory_weight(sim),
        }

    def sell_item(self, sim_id: str, object_id: int, qty: int = 1) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        ok = self.objects.sell_object(sim, int(object_id), int(qty))
        if not ok:
            return {"ok": False, "reason": "sell_failed"}
        self._bus.emit(
            "object_sold",
            sim=sim,
            object_id=int(object_id),
            qty=int(qty),
            tick=self._tick_count,
        )
        return {
            "ok": True,
            "sim_id": sim_id,
            "object_id": int(object_id),
            "qty": int(qty),
            "simoleons": round(sim.simoleons, 2),
            "inventory_weight": self.objects.inventory_weight(sim),
        }

    def gift_item(
        self,
        giver_id: str,
        receiver_id: str,
        object_id: int | None = None,
    ) -> dict:
        giver = self._sim_lookup.get(giver_id)
        receiver = self._sim_lookup.get(receiver_id)
        if giver is None or receiver is None:
            return {"ok": False, "reason": "sim_not_found"}
        if giver.sim_id == receiver.sim_id:
            return {"ok": False, "reason": "same_sim"}

        inv = list(getattr(giver, "inventory_objects", []))
        if not inv:
            return {"ok": False, "reason": "empty_inventory"}

        selected: dict[str, Any] | None = None
        if object_id is not None:
            for item in inv:
                if int(item.get("id", -1)) == int(object_id):
                    selected = dict(item)
                    break
        else:
            best_price = -1.0
            for item in inv:
                price = float(item.get("market_price", 0.0))
                if price > best_price:
                    best_price = price
                    selected = dict(item)

        if selected is None:
            return {"ok": False, "reason": "item_not_found"}

        receiver_inventory = list(getattr(receiver, "inventory_objects", []))
        receiver_inventory.append(dict(selected))
        constrained = self.objects._apply_inventory_constraints(
            receiver, receiver_inventory
        )
        if len(constrained) < len(receiver_inventory):
            return {"ok": False, "reason": "receiver_inventory_full"}

        removed = False
        kept = []
        selected_id = int(selected.get("id", -1))
        for item in giver.inventory_objects:
            if not removed and int(item.get("id", -1)) == selected_id:
                removed = True
                continue
            kept.append(item)
        if not removed:
            return {"ok": False, "reason": "item_not_found"}

        giver.inventory_objects = kept
        receiver.inventory_objects = constrained
        giver.inventory = [o["name"] for o in giver.inventory_objects]
        receiver.inventory = [o["name"] for o in receiver.inventory_objects]

        rel = self.relationships.get(giver.sim_id, receiver.sim_id)
        affinity_bonus = 4.0
        interests_a = set(giver.profile.get("interests", []))
        interests_b = set(receiver.profile.get("interests", []))
        if interests_a & interests_b:
            affinity_bonus += 3.0
        rel.apply_deltas(affinity_bonus, affinity_bonus * 0.2)

        self._bus.emit(
            "object_gifted",
            giver=giver,
            receiver=receiver,
            object_id=int(selected.get("id", -1)),
            tick=self._tick_count,
        )
        self.dynasties.on_gift(giver, receiver)
        return {
            "ok": True,
            "giver_id": giver_id,
            "receiver_id": receiver_id,
            "object": dict(selected),
        }

    def use_item(self, sim_id: str, object_id: int) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        result = self.objects.use_object(sim, int(object_id))
        if not result.get("ok"):
            return result
        self._bus.emit(
            "object_used",
            sim=sim,
            object_id=int(object_id),
            tick=self._tick_count,
        )
        self.dynasties.on_item_use(sim, result.get("effect", {}))
        plan = getattr(sim, "_neural_plan", None)
        if isinstance(plan, dict) and int(plan.get("object_id", -1)) == int(object_id):
            restore = float(result.get("effect", {}).get("restore", 0.0))
            self.neural_policy.observe(
                sim, plan, reward=0.25 + restore * 0.1, success=True
            )
        return {
            "ok": True,
            "sim_id": sim_id,
            "object": result.get("item", {}),
            "effect": result.get("effect", {}),
            "simoleons": round(sim.simoleons, 2),
        }

    def trade_item(
        self,
        from_sim_id: str,
        to_sim_id: str,
        object_id: int,
        qty: int = 1,
        unit_price: float | None = None,
    ) -> dict:
        seller = self._sim_lookup.get(from_sim_id)
        buyer = self._sim_lookup.get(to_sim_id)
        if seller is None or buyer is None:
            return {"ok": False, "reason": "sim_not_found"}
        if seller.sim_id == buyer.sim_id:
            return {"ok": False, "reason": "same_sim"}
        if qty <= 0:
            return {"ok": False, "reason": "invalid_qty"}

        transfers = []
        for item in getattr(seller, "inventory_objects", []):
            if int(item.get("id", -1)) == int(object_id):
                transfers.append(dict(item))
                if len(transfers) >= qty:
                    break
        if len(transfers) < qty:
            return {"ok": False, "reason": "seller_missing_items"}

        price_each = (
            float(unit_price)
            if unit_price is not None
            else float(transfers[0].get("market_price", 1.0)) * 0.85
        )
        total = max(1.0, price_each) * qty
        if buyer.simoleons < total:
            return {"ok": False, "reason": "buyer_insufficient_funds"}

        candidate_inventory = list(getattr(buyer, "inventory_objects", [])) + transfers
        constrained = self.objects._apply_inventory_constraints(
            buyer, candidate_inventory
        )
        if len(constrained) < len(candidate_inventory):
            return {"ok": False, "reason": "buyer_inventory_full"}

        remaining = []
        to_remove = qty
        for item in getattr(seller, "inventory_objects", []):
            if to_remove > 0 and int(item.get("id", -1)) == int(object_id):
                to_remove -= 1
                continue
            remaining.append(item)

        seller.inventory_objects = remaining
        buyer.inventory_objects = constrained
        seller.inventory = [o["name"] for o in seller.inventory_objects]
        buyer.inventory = [o["name"] for o in buyer.inventory_objects]
        buyer.simoleons -= total
        seller.simoleons += total
        self._emit_economy_event(
            "economy.trade",
            seller_id=from_sim_id,
            buyer_id=to_sim_id,
            object_id=int(object_id),
            qty=int(qty),
            total=round(total, 2),
        )

        self._bus.emit(
            "object_traded",
            seller=seller,
            buyer=buyer,
            object_id=int(object_id),
            qty=int(qty),
            price=round(total, 2),
            tick=self._tick_count,
        )
        self.dynasties.on_trade(
            seller, buyer, total, item=transfers[0] if transfers else None
        )
        return {
            "ok": True,
            "from_sim_id": from_sim_id,
            "to_sim_id": to_sim_id,
            "object_id": int(object_id),
            "qty": int(qty),
            "total_price": round(total, 2),
            "seller_simoleons": round(seller.simoleons, 2),
            "buyer_simoleons": round(buyer.simoleons, 2),
        }

    def create_contract_loan(
        self,
        lender_id: str,
        borrower_id: str,
        principal: float,
        interest_rate: float = 0.05,
        duration_ticks: int = 40,
    ) -> dict:
        out = self.contracts_engine.create_loan(
            lender_id,
            borrower_id,
            float(principal),
            float(interest_rate),
            int(duration_ticks),
            self._tick_count,
        )
        if out.get("ok"):
            self.ledger.record(
                "contract_created", self._tick_count, {"type": "loan", **out}
            )
            # Mirror on-chain: AgreementEngine handles collateral + installments
            if hasattr(self, "web3"):
                try:
                    self.web3.create_loan(
                        lender_id, borrower_id, float(principal),
                        float(interest_rate), int(duration_ticks), self._tick_count,
                    )
                except Exception as _ce:
                    logger.debug("[Bridge] loan chain error: %s", _ce)
        return out

    def create_contract_employment(
        self,
        employer_id: str,
        employee_id: str,
        wage: float,
        period_ticks: int = 5,
        severance: float = 50.0,
    ) -> dict:
        out = self.contracts_engine.create_employment(
            employer_id,
            employee_id,
            float(wage),
            int(period_ticks),
            float(severance),
            self._tick_count,
        )
        if out.get("ok"):
            self.ledger.record(
                "contract_created", self._tick_count, {"type": "employment", **out}
            )
            if hasattr(self, "web3"):
                try:
                    self.web3.create_employment_contract(
                        employer_id, employee_id, float(wage),
                        int(period_ticks), 100, self._tick_count,
                    )
                except Exception as _ce:
                    logger.debug("[Bridge] employment chain error: %s", _ce)
        return out

    def create_contract_partnership(
        self,
        a_id: str,
        b_id: str,
        revenue_share: float = 0.2,
        buyout: float = 10000.0,
    ) -> dict:
        out = self.contracts_engine.create_partnership(
            a_id,
            b_id,
            float(revenue_share),
            float(buyout),
            self._tick_count,
        )
        if out.get("ok"):
            self.ledger.record(
                "contract_created", self._tick_count, {"type": "partnership", **out}
            )
            # No direct AgreementEngine type for partnership — use loan as proxy
            if hasattr(self, "web3"):
                try:
                    self.web3.create_loan(
                        a_id, b_id, float(buyout) * 0.1,
                        float(revenue_share), 200, self._tick_count,
                    )
                except Exception as _ce:
                    logger.debug("[Bridge] partnership chain error: %s", _ce)
        return out

    def stock_buy(self, sim_id: str, ticker: str, shares: int) -> dict:
        ok = self.stocks.buy(sim_id, str(ticker).upper(), int(shares), self)
        if ok:
            self.ledger.record(
                "stock_buy",
                self._tick_count,
                {
                    "sim_id": sim_id,
                    "ticker": str(ticker).upper(),
                    "shares": int(shares),
                },
            )
        return {"ok": bool(ok)}

    def stock_sell(self, sim_id: str, ticker: str, shares: int) -> dict:
        ok = self.stocks.sell(sim_id, str(ticker).upper(), int(shares), self)
        if ok:
            self.ledger.record(
                "stock_sell",
                self._tick_count,
                {
                    "sim_id": sim_id,
                    "ticker": str(ticker).upper(),
                    "shares": int(shares),
                },
            )
        return {"ok": bool(ok)}

    def token_wallet(self, sim_id: str) -> dict:
        if sim_id not in self._sim_lookup:
            return {"ok": False, "reason": "sim_not_found"}
        return {"ok": True, "sim_id": sim_id, "wallet": self.tokens.wallet(sim_id)}

    def gift_money(
        self,
        from_sim_id: str,
        to_sim_id: str,
        amount: float,
        channel: str = "direct",
    ) -> dict:
        giver = self._sim_lookup.get(from_sim_id)
        receiver = self._sim_lookup.get(to_sim_id)
        if giver is None or receiver is None:
            return {"ok": False, "reason": "sim_not_found"}
        if from_sim_id == to_sim_id:
            return {"ok": False, "reason": "same_sim"}
        amt = float(amount)
        if amt <= 0:
            return {"ok": False, "reason": "invalid_amount"}
        if float(giver.simoleons) < amt:
            return {"ok": False, "reason": "insufficient_funds"}

        giver.simoleons -= amt
        receiver.simoleons += amt
        self._emit_economy_event(
            "economy.gift",
            from_sim_id=from_sim_id,
            to_sim_id=to_sim_id,
            amount=round(amt, 2),
            channel=str(channel),
        )
        self._bus.emit(
            "money_gifted",
            giver=giver,
            receiver=receiver,
            amount=round(amt, 2),
            channel=str(channel),
            tick=self._tick_count,
        )
        return {
            "ok": True,
            "from_sim_id": from_sim_id,
            "to_sim_id": to_sim_id,
            "amount": round(amt, 2),
            "giver_simoleons": round(float(giver.simoleons), 2),
            "receiver_simoleons": round(float(receiver.simoleons), 2),
        }

    def property_purchase(
        self,
        sim_id: str,
        venue_type: str,
        ownership_state: str = "partner",
        district: str = "central",
    ) -> dict:
        self.properties._sim_lookup = self._sim_lookup
        out = self.properties.purchase_property(
            sim_id, venue_type, ownership_state, district
        )
        if out.get("ok"):
            self._emit_economy_event(
                "economy.rent_income",
                sim_id=sim_id,
                action="purchase",
                venue_type=venue_type,
                property_id=out.get("property_id"),
                buy_in=out.get("buy_in"),
            )
        return out

    def property_collect_income(self, sim_id: str, property_id: str) -> dict:
        self.properties._sim_lookup = self._sim_lookup
        out = self.properties.collect_income(sim_id, property_id)
        if out.get("ok"):
            self._emit_economy_event(
                "economy.rent_income",
                sim_id=sim_id,
                action="collect",
                property_id=property_id,
                collected=out.get("collected"),
            )
        return out

    def property_upgrade(self, sim_id: str, property_id: str) -> dict:
        self.properties._sim_lookup = self._sim_lookup
        out = self.properties.upgrade_property(sim_id, property_id)
        if out.get("ok"):
            self._emit_economy_event(
                "economy.rent_income",
                sim_id=sim_id,
                action="upgrade",
                property_id=property_id,
                upgrade_level=out.get("upgrade_level"),
                cost=out.get("cost"),
            )
        return out

    def property_sell(self, sim_id: str, property_id: str) -> dict:
        self.properties._sim_lookup = self._sim_lookup
        out = self.properties.sell_property(sim_id, property_id)
        if out.get("ok"):
            self._emit_economy_event(
                "economy.rent_income",
                sim_id=sim_id,
                action="sell",
                property_id=property_id,
                payout=out.get("payout"),
            )
        return out

    def property_manage_employee(
        self, sim_id: str, property_id: str, action: str, employee_id: str = ""
    ) -> dict:
        self.properties._sim_lookup = self._sim_lookup
        out = self.properties.manage_employee(sim_id, property_id, action, employee_id)
        if out.get("ok"):
            self._stock_event(
                "employee_hire" if action == "hire" else "employee_fire", 1.0
            )
            self.ledger.record(
                "property_employee",
                self._tick_count,
                {
                    "sim_id": sim_id,
                    "property_id": property_id,
                    "action": action,
                    "employee_id": employee_id,
                },
            )
        return out

    def token_market_list(
        self, owner_id: str, token_id: str, price_simcoin: float
    ) -> dict:
        ok = self.tokens.list_item_token(owner_id, token_id, float(price_simcoin))
        if ok:
            self.ledger.record(
                "token_listed",
                self._tick_count,
                {
                    "owner_id": owner_id,
                    "token_id": token_id,
                    "price": float(price_simcoin),
                },
            )
        return {"ok": bool(ok)}

    def token_market_cancel(self, owner_id: str, token_id: str) -> dict:
        ok = self.tokens.cancel_listing(owner_id, token_id)
        return {"ok": bool(ok)}

    def token_market_buy(self, buyer_id: str, token_id: str) -> dict:
        ok = self.tokens.buy_listed_token(buyer_id, token_id)
        if ok:
            self.ledger.record(
                "token_sold",
                self._tick_count,
                {"buyer_id": buyer_id, "token_id": token_id},
            )
            self._queue_chain_intent(
                "token_market_buy", {"buyer_id": buyer_id, "token_id": token_id}
            )
        return {"ok": bool(ok)}

    def token_marketplace(self) -> dict:
        return {"ok": True, "listings": self.tokens.marketplace()}

    def bookie_refresh(self) -> dict:
        return self.bookie.refresh_matches()

    def bookie_matches(self) -> dict:
        return {"ok": True, "matches": list(self.bookie.matches.values())}

    def place_sim_bet(
        self, sim_id: str, match_id: str, selection: str, stake: float
    ) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        amt = float(stake)
        if amt <= 0:
            return {"ok": False, "reason": "invalid_stake"}
        if float(sim.simoleons) < amt:
            return {"ok": False, "reason": "insufficient_funds"}
        out = self.bookie.place_bet(
            bettor_type="sim",
            bettor_id=sim_id,
            match_id=match_id,
            selection=selection,
            stake=amt,
            tick=self._tick_count,
        )
        if not out.get("ok"):
            return out
        sim.simoleons -= amt
        self.ledger.record(
            "bookie_bet",
            self._tick_count,
            {
                "bettor_type": "sim",
                "bettor_id": sim_id,
                "match_id": match_id,
                "selection": selection,
                "stake": round(amt, 2),
                "odds": out.get("odds"),
                "bet_id": out.get("bet_id"),
            },
        )
        return {"ok": True, **out, "simoleons": round(float(sim.simoleons), 2)}

    def place_player_bet(
        self, player_id: str, match_id: str, selection: str, stake: float
    ) -> dict:
        pid = str(player_id)
        amt = float(stake)
        if amt <= 0:
            return {"ok": False, "reason": "invalid_stake"}
        bal = float(self.bookie.player_balances.get(pid, 0.0))
        if bal < amt:
            return {
                "ok": False,
                "reason": "insufficient_player_balance",
                "balance": round(bal, 2),
            }
        out = self.bookie.place_bet(
            bettor_type="player",
            bettor_id=pid,
            match_id=match_id,
            selection=selection,
            stake=amt,
            tick=self._tick_count,
        )
        if not out.get("ok"):
            return out
        self.bookie.player_balances[pid] = bal - amt
        self.ledger.record(
            "bookie_bet",
            self._tick_count,
            {
                "bettor_type": "player",
                "bettor_id": pid,
                "match_id": match_id,
                "selection": selection,
                "stake": round(amt, 2),
                "odds": out.get("odds"),
                "bet_id": out.get("bet_id"),
            },
        )
        return {
            "ok": True,
            **out,
            "balance": round(self.bookie.player_balances[pid], 2),
        }

    def player_bookie_fund(self, player_id: str, amount: float) -> dict:
        pid = str(player_id)
        amt = max(0.0, float(amount))
        self.bookie.player_balances[pid] = (
            float(self.bookie.player_balances.get(pid, 0.0)) + amt
        )
        return {
            "ok": True,
            "player_id": pid,
            "balance": round(self.bookie.player_balances[pid], 2),
        }

    def wallet_nonce(self, sim_id: str, address: str = "") -> dict:
        """
        SIWE-compliant challenge for MetaMask.
        Returns the exact message string to pass to MetaMask personal_sign.
        """
        if sim_id not in self._sim_lookup:
            return {"ok": False, "reason": "sim_not_found"}
        from blockchain.siwe import create_challenge
        from blockchain.eip712 import CHAIN_ID
        addr = address if address else f"0x{'0'*40}"
        challenge = create_challenge(addr, domain="simchain.game")
        self.wallet_nonces[sim_id] = challenge["nonce"]
        return {
            "ok":        True,
            "sim_id":    sim_id,
            "nonce":     challenge["nonce"],
            "message":   challenge["message"],
            "expires_at": challenge["expires_at"],
            "chain_id":  CHAIN_ID,
        }

    def wallet_link(
        self,
        sim_id: str,
        wallet_address: str,
        signature: str,
        chain_id: int = 1,
        message: str = "",
        nonce: str = "",
        auth_user_id: str = "",     # if provided, caller must own this sim
        auth_store=None,            # AuthStore instance for persistence + ownership check
    ) -> dict:
        """
        Verify a MetaMask SIWE signature and record the MetaMask address as the
        player's identity for this sim.

        The game wallet (deterministic) is NOT replaced — it keeps signing all
        automatic game transactions. MetaMask is an identity/consent layer only.

        SIWE mode (preferred): pass `message` + `nonce` from wallet_nonce().
        Legacy mode: omit message/nonce.

        auth_user_id + auth_store enforce ownership: only the user whose
        account is bound to this sim_id may link a MetaMask address.
        """
        from blockchain.eip712 import CHAIN_ID as SIM_CHAIN_ID
        if sim_id not in self._sim_lookup:
            return {"ok": False, "reason": "sim_not_found"}

        # ── Ownership check ───────────────────────────────────────────────────
        if auth_user_id and auth_store:
            user = auth_store.get_by_id(auth_user_id)
            if user is None or user.sim_id != sim_id:
                return {
                    "ok": False,
                    "reason": "forbidden: this sim does not belong to your account",
                }

        # ── Signature verification ────────────────────────────────────────────
        recovered = ""
        if message and nonce:
            try:
                from blockchain.siwe import verify_challenge
                recovered = verify_challenge(nonce, wallet_address, signature, message)
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
        else:
            stored_nonce = self.wallet_nonces.get(sim_id)
            if not stored_nonce:
                return {"ok": False, "reason": "nonce_missing — call /wallet/nonce first"}
            legacy_msg = (
                f"Link wallet to Sim {sim_id}. Nonce: {stored_nonce}. "
                f"ChainId: {SIM_CHAIN_ID}"
            )
            recovered = self._recover_wallet_address(legacy_msg, signature)
            if not recovered:
                return {"ok": False, "reason": "signature_invalid"}

        if recovered.lower() != str(wallet_address).lower():
            return {
                "ok": False,
                "reason": "signature_address_mismatch",
                "recovered": recovered[:12] + "…",
            }

        # ── Register MetaMask as identity layer (game wallet unchanged) ───────
        if hasattr(self, "web3"):
            ok = self.web3.link_metamask_wallet(sim_id, wallet_address)
            if not ok:
                return {"ok": False, "reason": "metamask_address_already_bound_to_another_sim"}

        # ── Persist to auth DB ────────────────────────────────────────────────
        if auth_store:
            if auth_user_id:
                auth_store.link_metamask(auth_user_id, wallet_address)
            else:
                # Best-effort: find user by sim_id
                user = auth_store.get_by_sim_id(sim_id)
                if user:
                    auth_store.link_metamask(user.user_id, wallet_address)

        # ── In-memory link record ─────────────────────────────────────────────
        self.sim_wallet_links[sim_id] = {
            "wallet_address": str(wallet_address),
            "chain_id":       SIM_CHAIN_ID,
            "linked_at_tick": int(self._tick_count),
        }
        self.wallet_nonces.pop(sim_id, None)

        self.ledger.record(
            "wallet_linked",
            self._tick_count,
            {
                "sim_id":         sim_id,
                "wallet_address": str(wallet_address),
                "chain_id":       SIM_CHAIN_ID,
                "method":         "siwe" if message else "legacy",
            },
        )

        wallet_info = self.web3.wallet_info(sim_id) if hasattr(self, "web3") else {}
        return {
            "ok":             True,
            "sim_id":         sim_id,
            "wallet_address": str(wallet_address),
            "chain_id":       SIM_CHAIN_ID,
            "game_wallet":    wallet_info.get("game_wallet"),
            "game_balance_sim": wallet_info.get("game_balance_sim", 0.0),
        }

    def wallet_unlink(self, sim_id: str) -> dict:
        if sim_id not in self._sim_lookup:
            return {"ok": False, "reason": "sim_not_found"}
        had = self.sim_wallet_links.pop(sim_id, None)
        if not had:
            return {"ok": False, "reason": "not_linked"}
        self.ledger.record("wallet_unlinked", self._tick_count, {"sim_id": sim_id})
        return {"ok": True, "sim_id": sim_id}

    def wallet_status(self, sim_id: str) -> dict:
        if sim_id not in self._sim_lookup:
            return {"ok": False, "reason": "sim_not_found"}
        return {
            "ok": True,
            "sim_id": sim_id,
            "linked": sim_id in self.sim_wallet_links,
            "link": self.sim_wallet_links.get(sim_id),
            "mirror": self.wallet_mirror.get(sim_id, {}),
        }

    def wallet_set_mirror(
        self,
        sim_id: str,
        native_balance: float = 0.0,
        simcoin_erc20: float = 0.0,
        nfts: list[dict] | None = None,
    ) -> dict:
        if sim_id not in self._sim_lookup:
            return {"ok": False, "reason": "sim_not_found"}
        self.wallet_mirror[sim_id] = {
            "native_balance": float(native_balance),
            "simcoin_erc20": float(simcoin_erc20),
            "nfts": list(nfts or []),
        }
        return {"ok": True, "sim_id": sim_id, "mirror": self.wallet_mirror[sim_id]}

    def chain_intents_view(self, limit: int = 100) -> dict:
        return {"ok": True, "intents": list(self.chain_intents[-max(1, int(limit)) :])}

    def economy_overview(self) -> dict:
        return {
            "tick": self._tick_count,
            "ledger": self.ledger.state(),
            "contracts": {
                "stats": self.contracts_engine.stats(),
                "active": self.contracts_engine.list_contracts(active_only=True),
            },
            "stocks": self.stocks.state(),
            "tokens": self.tokens.state(),
            "bookie": self.bookie.state(),
        }

    def sim_portfolio(self, sim_id: str) -> dict:
        sim = self._sim_lookup.get(sim_id)
        if sim is None:
            return {"ok": False, "reason": "sim_not_found"}
        obligations = self.contracts_engine.obligations_for(sim_id)
        token_wallet = self.tokens.wallet(sim_id)
        stock_portfolio = self.stocks.portfolio(sim_id)
        properties = self.properties.investment_dashboard(sim_id)
        liquid = float(sim.simoleons)
        asset_value = float(properties.get("portfolio_value", 0.0)) + float(
            stock_portfolio.get("value", 0.0)
        )
        liability_value = float(obligations.get("total_outstanding", 0.0))
        net_worth = liquid + asset_value - liability_value
        normalized = {
            "sim_id": sim_id,
            "liquid_simoleons": round(liquid, 2),
            "token_wallet": token_wallet,
            "stocks": stock_portfolio,
            "properties": properties,
            "contracts": obligations,
            "asset_value": round(asset_value, 2),
            "liability_value": round(liability_value, 2),
            "net_worth": round(net_worth, 2),
            "wallet_link": self.sim_wallet_links.get(sim_id),
            "wallet_mirror": self.wallet_mirror.get(sim_id, {}),
        }
        setattr(sim, "_portfolio_view", normalized)
        return {
            "ok": True,
            "sim_id": sim_id,
            "name": sim.name,
            "portfolio": normalized,
        }

    def _on_gig_completed_economy(self, **kw: Any) -> None:
        if not kw.get("success"):
            return
        sim = kw.get("sim")
        if sim is None:
            return
        pay = float(kw.get("pay", 0.0))
        self._emit_economy_event(
            "economy.contract_settlement",
            settlement_type="gig",
            sim_id=sim.sim_id,
            gig_type=kw.get("gig_type"),
            label=kw.get("label"),
            amount=round(pay, 2),
        )

    def _emit_economy_event(self, event_name: str, **payload: Any) -> None:
        self._bus.emit(event_name, tick=self._tick_count, **payload)

    def _on_economy_purchase(self, **kw: Any) -> None:
        self.contracts_engine.observe_economy_event("economy.purchase", kw)
        self.ledger.record("shop_purchase", self._tick_count, dict(kw))
        spent = float(kw.get("spent", 0.0))
        sim_id = str(kw.get("sim_id", ""))
        if spent > 0:
            self._stock_event("shop_purchase", max(1.0, spent / 100.0))
            if sim_id:
                self.tokens.mint_simcoin(sim_id, max(0.25, spent * 0.01))
                self._queue_chain_intent("economy_purchase", dict(kw))

    def _on_economy_trade(self, **kw: Any) -> None:
        self.contracts_engine.observe_economy_event("economy.trade", kw)
        self.ledger.record("p2p_trade", self._tick_count, dict(kw))
        seller_id = str(kw.get("seller_id", ""))
        total = float(kw.get("total", 0.0))
        if seller_id and total > 0:
            self.tokens.mint_simcoin(seller_id, max(0.1, total * 0.005))
            self._queue_chain_intent("economy_trade", dict(kw))
            seller = self._sim_lookup.get(seller_id)
            buyer = self._sim_lookup.get(str(kw.get("buyer_id", "")))
            if seller is not None and buyer is not None:
                self.dynasties.on_trade(seller, buyer, total, item=None)

    def _on_economy_rent_income(self, **kw: Any) -> None:
        self.contracts_engine.observe_economy_event("economy.rent_income", kw)
        ev = str(kw.get("action", ""))
        self.ledger.record(f"property_{ev or 'event'}", self._tick_count, dict(kw))
        sim_id = str(kw.get("sim_id", ""))
        if ev == "purchase":
            self._stock_event("property_purchase", 1.2)
            self.tokens.mint_simcoin(
                sim_id, max(1.0, float(kw.get("buy_in", 0.0)) * 0.003)
            )
            pid = str(kw.get("property_id", ""))
            if sim_id and pid:
                deed_tid = self.tokens.mint_item_token(
                    owner_id=sim_id,
                    item_ref=f"deed:{pid}",
                    rarity="legendary",
                    tick=self._tick_count,
                )
                self.ledger.record(
                    "token_minted",
                    self._tick_count,
                    {"sim_id": sim_id, "token_id": deed_tid, "kind": "property_deed"},
                )
                self._queue_chain_intent(
                    "mint_property_deed",
                    {"sim_id": sim_id, "property_id": pid, "token_id": deed_tid},
                )
        elif ev == "collect":
            self.tokens.mint_simcoin(
                sim_id, max(0.2, float(kw.get("collected", 0.0)) * 0.01)
            )
        elif ev == "upgrade":
            self._stock_event("property_upgrade", 0.8)
        elif ev == "sell":
            self._stock_event("property_sell", 1.0)

    def _on_economy_gift(self, **kw: Any) -> None:
        self.contracts_engine.observe_economy_event("economy.gift", kw)
        self.ledger.record("money_gift", self._tick_count, dict(kw))
        giver = self._sim_lookup.get(str(kw.get("from_sim_id", "")))
        receiver = self._sim_lookup.get(str(kw.get("to_sim_id", "")))
        if giver is not None and receiver is not None:
            self.dynasties.on_gift(giver, receiver)
            self.gossip.learn(receiver.sim_id, giver.sim_id, "gifted_money")
            self._queue_chain_intent("economy_gift", dict(kw))

    def _on_economy_contract_settlement(self, **kw: Any) -> None:
        self.contracts_engine.observe_economy_event("economy.contract_settlement", kw)
        self.ledger.record("contract_event", self._tick_count, dict(kw))
        amount = float(kw.get("amount", kw.get("payout", 0.0)) or 0.0)
        sim_id = str(kw.get("sim_id", ""))
        if sim_id and amount > 0:
            self.tokens.mint_simcoin(sim_id, max(0.5, amount * 0.02))
            self._queue_chain_intent("contract_settlement", dict(kw))
        self._stock_event("contract_settlement", 0.9)
        self._apply_contract_social_effect(kw, breach=False)

    def _on_economy_contract_breach(self, **kw: Any) -> None:
        self.contracts_engine.observe_economy_event("economy.contract_breach", kw)
        self.ledger.record("contract_event", self._tick_count, dict(kw))
        self._stock_event("contract_breach", 1.0)
        self._apply_contract_social_effect(kw, breach=True)

    def _apply_contract_social_effect(self, kw: dict[str, Any], breach: bool) -> None:
        from core.sentiments import add_sentiment

        cid = str(kw.get("contract_id", ""))
        if not cid:
            return
        cmap = {
            c["contract_id"]: c
            for c in self.contracts_engine.list_contracts(active_only=False)
        }
        c = cmap.get(cid)
        if not c:
            return
        a = self._sim_lookup.get(str(c.get("party_a", "")))
        b = self._sim_lookup.get(str(c.get("party_b", "")))
        if a is None or b is None:
            return
        rel = self.relationships.get(a.sim_id, b.sim_id)
        if breach:
            rel.apply_deltas(-8.0, -2.0)
            rel.jealousy_score = min(100.0, rel.jealousy_score + 10.0)
            add_sentiment(rel, "betrayal", self._tick_count, source="contract_breach")
            add_sentiment(
                rel, "financial_strain", self._tick_count, source="contract_breach"
            )
            a.moodlets.add("betrayed", source="contract_breach")
            b.moodlets.add("stressed", source="contract_breach")
            a.reputation_score = max(0.0, a.reputation_score - 0.8)
            b.reputation_score = max(0.0, b.reputation_score - 0.6)
            self.gossip.learn(a.sim_id, b.sim_id, "contract_breach")
        else:
            age = max(
                0,
                int(self._tick_count) - int(c.get("created_tick", self._tick_count)),
            )
            if age >= 8:
                rel.apply_deltas(+2.5, +0.3)
                add_sentiment(
                    rel,
                    "reliable_partner",
                    self._tick_count,
                    source="contract_settlement",
                )
                a.moodlets.add("proud", source="contract_settlement")
                b.moodlets.add("grateful", source="contract_settlement")
                a.reputation_score = min(100.0, a.reputation_score + 0.4)
                b.reputation_score = min(100.0, b.reputation_score + 0.4)

    def _adjudicate_contract_dispute(self, evt: dict) -> None:
        cid = str(evt.get("contract_id", ""))
        if not cid:
            return
        contract_map = {
            c["contract_id"]: c
            for c in self.contracts_engine.list_contracts(active_only=False)
        }
        c = contract_map.get(cid)
        if not c:
            return
        a = self._sim_lookup.get(str(c.get("party_a", "")))
        b = self._sim_lookup.get(str(c.get("party_b", "")))
        if a is None or b is None:
            return
        system = "You adjudicate contract disputes between sims. Return strict JSON."
        user_msg = (
            f"A={a.name} B={b.name} contract={c.get('type')} reason={evt.get('reason', 'breach')} "
            f"friendship={self.relationships.get(a.sim_id, b.sim_id).friendship:.1f}. "
            "Resolve fairness and suggest penalties in output fields."
        )
        try:
            result = call_adjudicator(
                self._llm, system, user_msg, interaction="legal dispute"
            )
        except Exception:
            result = {"valence": -0.4, "reasoning": "fallback"}
        self.ledger.record(
            "contract_dispute",
            self._tick_count,
            {"contract_id": cid, "reason": evt.get("reason"), "result": result},
        )
        valence = float(result.get("valence", -0.3) or -0.3)
        rel = self.relationships.get(a.sim_id, b.sim_id)
        rel.apply_deltas(max(-8.0, min(4.0, valence * 6.0)), 0.0)
        if valence < 0:
            a.moodlets.add("stressed", source="contract_dispute")
            b.moodlets.add("stressed", source="contract_dispute")
        else:
            a.moodlets.add("proud", source="contract_dispute")
            b.moodlets.add("grateful", source="contract_dispute")

    def _feed_stock_from_properties(self) -> None:
        props = list(getattr(self.properties, "_properties", {}).values())
        if not props:
            return
        signal = 0.0
        for p in props:
            occ = 1.0 if getattr(p, "employees", []) else 0.7
            clean = float(getattr(p, "cleanliness", 0.5))
            svc = float(getattr(p, "service_quality", 0.5))
            rep = float(getattr(p, "reputation", 0.5))
            signal += (occ + clean + svc + rep) / 4.0
        avg = signal / max(1.0, float(len(props)))
        self._stock_event("property_ops", max(0.1, abs(avg - 0.5) * 2.0))

    def _on_item_crafted_tokenization(self, **kw: Any) -> None:
        sim = kw.get("sim")
        if sim is None:
            return
        quality = float(kw.get("quality", 0.0))
        if quality < 0.9:
            return
        item_name = str(kw.get("item_name", "crafted_item"))
        token_id = self.tokens.mint_item_token(
            owner_id=sim.sim_id,
            item_ref=f"crafted:{item_name}",
            rarity="rare",
            tick=self._tick_count,
        )
        self.ledger.record(
            "token_minted",
            self._tick_count,
            {"sim_id": sim.sim_id, "token_id": token_id, "kind": "crafted_rare"},
        )
        self._queue_chain_intent(
            "mint_crafted_item",
            {"sim_id": sim.sim_id, "item_name": item_name, "token_id": token_id},
        )

    def _on_burglar_market_shock(self, **kw: Any) -> None:
        _ = kw
        self._stock_event("burglary", 1.1)

    def _on_grim_market_shock(self, **kw: Any) -> None:
        _ = kw
        self._stock_event("grim_event", 1.2)

    # ── Stock market dual-write helper ───────────────────────────────────────

    # Mapping from engine WorldStockMarket event names → blockchain StockMarket
    _STOCK_EVENT_MAP: dict[str, str] = {
        "shop_purchase":      "shop_visit_cafe",
        "property_purchase":  "property_purchased",
        "property_upgrade":   "property_purchased",
        "property_sell":      "property_purchased",
        "property_ops":       "property_purchased",
        "contract_settlement":"gig_completed",
        "contract_breach":    "sim_fired",
        "burglary":           "high_social",
        "grim_event":         "illness_outbreak",
        "career_promotion":   "sim_promoted",
        "career_fired":       "sim_fired",
        "marriage":           "sim_married",
        "divorce":            "sim_divorced",
        "graduation":         "graduation",
    }

    # ── Closed-loop cognition helpers ─────────────────────────────────────────

    def _tick_intentions(self) -> None:
        """Tick intention stacks; generate new goal if sim has none."""
        from core.intention import maybe_generate_intention
        for sim in self.sims:
            stack = getattr(sim, "intentions", None)
            if stack is None:
                continue
            # Find what interaction the sim had this tick (from last pending)
            last_type = ""
            for item in self._pending:
                if item.sim_a_id == sim.sim_id:
                    last_type = item.interaction
                    break
            stack.tick(sim, recent_interaction_type=last_type)
            # Auto-generate intention when stack is empty
            if not stack.active_goal() and self._tick_count % 7 == 0:
                new_goal = maybe_generate_intention(sim, self._tick_count)
                if new_goal:
                    new_goal.status = __import__("core.intention", fromlist=["GoalStatus"]).GoalStatus.ACTIVE
                    stack.push(new_goal)

            # Hard-consequence check on every intention tick
            if hasattr(self, "hard_consequences"):
                self.hard_consequences.check_auto_triggers(
                    sim, self._tick_count, bus=self._bus
                )

    def _tick_beliefs(self) -> None:
        """Decay belief confidence; write new observations from resolved interactions."""
        for sim in self.sims:
            beliefs = getattr(sim, "beliefs", None)
            if beliefs:
                beliefs.decay_tick(self._tick_count)

    def _observe_interaction(
        self,
        observer: "Sim",
        subject_id: str,
        predicate: str,
        object_: str,
        confidence: float = 0.85,
    ) -> None:
        """Write a direct observation into an observer's belief graph."""
        beliefs = getattr(observer, "beliefs", None)
        if beliefs:
            from core.beliefs import BeliefSource
            beliefs.observe(
                subject_id, predicate, object_,
                confidence=confidence,
                source=BeliefSource.OBSERVATION,
                tick=self._tick_count,
            )

    def _stock_event(self, event_name: str, magnitude: float = 1.0) -> None:
        """Dual-write stock events to both WorldStockMarket and blockchain StockMarket."""
        self.stocks.on_event(event_name, magnitude)
        chain_event = self._STOCK_EVENT_MAP.get(event_name, "")
        if chain_event and hasattr(self, "web3"):
            try:
                self.web3.on_world_event(chain_event)
            except Exception:
                pass

    def _recover_wallet_address(self, message: str, signature: str) -> str:
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
        except Exception:
            return ""
        try:
            msg = encode_defunct(text=message)
            return str(Account.recover_message(msg, signature=signature))
        except Exception:
            return ""

    def _queue_chain_intent(self, intent_type: str, payload: dict) -> None:
        """Log a chain intent AND actually submit the transaction to the chain."""
        linked_sim_id = ""
        for k in ("sim_id", "owner_id", "buyer_id", "from_sim_id"):
            if payload.get(k):
                linked_sim_id = str(payload.get(k))
                break
        if not linked_sim_id:
            return
        link = self.sim_wallet_links.get(linked_sim_id)
        if not link:
            return
        intent = {
            "tick": int(self._tick_count),
            "type": str(intent_type),
            "sim_id": linked_sim_id,
            "wallet_address": link.get("wallet_address"),
            "payload": dict(payload),
        }
        self.chain_intents.append(intent)
        self.chain_intents = self.chain_intents[-500:]
        self.ledger.record("chain_intent", self._tick_count, intent)

        # Actually submit to the chain based on intent_type
        if not hasattr(self, "web3"):
            return
        try:
            w = self.web3
            if intent_type == "shop_purchase":
                w.submit_shop_purchase(
                    linked_sim_id,
                    payload.get("shop_name", ""),
                    payload.get("item", ""),
                    float(payload.get("cost", 0)),
                    self._tick_count,
                )
            elif intent_type == "stock_buy":
                w.submit_stock_buy(
                    linked_sim_id,
                    payload.get("ticker", ""),
                    int(payload.get("shares", 0)),
                )
            elif intent_type == "stock_sell":
                w.submit_stock_sell(
                    linked_sim_id,
                    payload.get("ticker", ""),
                    int(payload.get("shares", 0)),
                )
            elif intent_type == "loan_create":
                w.create_loan(
                    linked_sim_id,
                    payload.get("borrower_id", ""),
                    float(payload.get("principal", 0)),
                    float(payload.get("interest_rate", 0.05)),
                    int(payload.get("duration_ticks", 50)),
                    self._tick_count,
                )
            elif intent_type in ("transfer", "gift"):
                to_id = payload.get("to_sim_id", "")
                if to_id:
                    w.submit_transfer(
                        linked_sim_id, to_id,
                        float(payload.get("amount", 0)),
                        intent_type,
                    )
        except Exception as _ce:
            logger.debug("[ChainIntent] submit error (%s): %s", intent_type, _ce)

    def resolve_dynamic_threat(self, lot_id: str, threat_tag: str = "burglary") -> dict:
        """Generic emergent threat response resolver (traits + defensive items)."""
        return self.burglar.resolve_dynamic_threat_response(self, lot_id, threat_tag)

    def add_sim(self, sim: "Sim") -> bool:
        """
        Dynamically add a newly created sim to the running engine.

        Safe to call after __init__ completes — used by the signup flow to
        inject a player-owned sim without restarting. Returns True on success,
        False if the sim_id is already registered (duplicate guard).
        """
        if sim.sim_id in self._sim_lookup:
            logger.warning("[Engine] add_sim: sim_id %s already registered", sim.sim_id)
            return False

        # Core registration
        self.sims.append(sim)
        self._sim_lookup[sim.sim_id] = sim
        self._local_sim_ids.add(sim.sim_id)

        # State back-reference for bridge helpers
        sim._engine_ref = self

        # Cognition systems
        from core.intention import IntentionStack
        from core.beliefs import BeliefGraph
        if not hasattr(sim, "intentions"):
            sim.intentions = IntentionStack()
        if not hasattr(sim, "beliefs"):
            sim.beliefs = BeliefGraph()

        # Blockchain wallet + initial $SIM mint
        initial_simoleons = float(getattr(sim, "simoleons", 0.0))
        self.web3.register_sim(sim.sim_id, initial_simoleons=initial_simoleons)

        # Restore MetaMask identity link from persistent auth store (server restart)
        try:
            import importlib
            _auth_mod = importlib.import_module("server")
            _auth_store = getattr(_auth_mod, "_auth_store", None)
            if _auth_store is not None:
                _auth_user = _auth_store.get_by_sim_id(sim.sim_id)
                if _auth_user and _auth_user.metamask_address:
                    self.web3.restore_metamask_link(sim.sim_id, _auth_user.metamask_address)
                    self.sim_wallet_links[sim.sim_id] = {
                        "wallet_address": _auth_user.metamask_address,
                        "chain_id": self.chain.chain_id,
                        "linked_at_tick": 0,
                    }
        except Exception:
            pass  # no auth store present (e.g. tests, headless mode)

        # Shard assignment (default global until sim moves to a lot)
        self._shard_manager.assign(sim.sim_id, "global")
        self._sim_shard_cache[sim.sim_id] = "global"

        # Object inventory seed
        try:
            self.objects.assign_sim_inventory(sim)
        except Exception:
            pass

        # LOD reassignment will sort this sim into the right tier on next tick
        from engine.lod import assign_lod_tiers
        assign_lod_tiers(self.sims)

        logger.info(
            "[Engine] add_sim: %s (%s) added — total sims=%d",
            sim.name, sim.sim_id[:8], len(self.sims),
        )
        return True

    def _assign_coworkers(self) -> None:
        """Group sims by job category and assign them as each other's coworkers."""
        from collections import defaultdict

        by_job: dict[str, list[str]] = defaultdict(list)
        for sim in self.sims:
            by_job[sim.profile.get("job", "Unknown")].append(sim.sim_id)
        for sim in self.sims:
            job = sim.profile.get("job", "Unknown")
            sim.coworker_ids = [sid for sid in by_job[job] if sid != sim.sim_id]

    def _update_celebrity_scores(self) -> None:
        """
        Recalculate celebrity_score and celebrity_tier for all sims.
        Score rises with: positive reputation, large friend network, career success.
        """
        from config import CELEBRITY_TIERS, CELEBRITY_SCORE_DECAY

        for sim in self.sims:
            # Reputation contribution
            rep_contrib = max(0.0, sim.reputation_score / 5.0)
            # Friend count contribution
            friend_count = sum(
                1
                for other in self.sims
                if other.sim_id != sim.sim_id
                and self.relationships.get(sim.sim_id, other.sim_id).friendship >= 60
            )
            network_contrib = min(10.0, friend_count * 0.5)
            # Career contribution
            career_contrib = max(0.0, (sim.career_performance - 50) / 10.0)

            target = rep_contrib + network_contrib + career_contrib
            # Smooth toward target
            sim.celebrity_score += (target - sim.celebrity_score) * 0.05
            sim.celebrity_score = max(0.0, min(100.0, sim.celebrity_score))

            # Update tier label
            for tier, (lo, hi) in CELEBRITY_TIERS.items():
                if lo <= sim.celebrity_score < hi:
                    sim.celebrity_tier = tier
                    break
            else:
                sim.celebrity_tier = "icon"

    def _apply_emotional_contagion(self, sim_a: "Sim", sim_b: "Sim", rel) -> None:
        """
        After a resolved interaction, each sim absorbs a fraction of the other's
        dominant emotion proportional to their friendship level.

        This creates mood waves across the social graph: a grieving sim saddens
        their close friends; a joyful sim lifts the people they're close to.
        No effect between strangers or acquaintances.
        """
        from config import (
            CONTAGION_FRIENDSHIP_MIN,
            CONTAGION_MAX_STRENGTH,
            CONTAGION_SKIP_EMOTIONS,
        )

        friendship = rel.friendship
        if friendship < CONTAGION_FRIENDSHIP_MIN:
            return

        # Linear scale: 0 at threshold → CONTAGION_MAX_STRENGTH at 100
        strength = (
            (friendship - CONTAGION_FRIENDSHIP_MIN) / (100.0 - CONTAGION_FRIENDSHIP_MIN)
        ) * CONTAGION_MAX_STRENGTH

        emo_a = sim_a.emotion.dominant
        emo_b = sim_b.emotion.dominant

        # B catches A's emotion (e.g. A's grief bleeds into B)
        if emo_a and emo_a not in CONTAGION_SKIP_EMOTIONS:
            sim_b.emotion.add(
                emo_a, round(strength * 0.6, 3), duration=2, source="contagion"
            )
            logger.debug(
                "[Contagion] %s→%s catches %s from %s (strength=%.2f)",
                sim_b.name,
                emo_a,
                sim_a.name,
                emo_a,
                strength,
            )

        # A catches B's emotion (symmetrical — shared experience creates synchrony)
        if emo_b and emo_b not in CONTAGION_SKIP_EMOTIONS:
            sim_a.emotion.add(
                emo_b, round(strength * 0.6, 3), duration=2, source="contagion"
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
        weather_line = ""
        try:
            curw = getattr(self.weather, "current", None)
            if curw is not None:
                weather_line = (
                    f"Weather: {getattr(curw, 'condition', 'clear')} "
                    f"({getattr(curw, 'temperature', 20)}C)"
                )
        except Exception:
            pass
        date = self.calendar.date_dict(self._tick_count)
        date_line = f"Date: day {date.get('day_of_year', 0)} season={date.get('season', 'unknown')}"
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
        from config import GGUF_USE_NO_THINK

        think_prefix = "/no_think\n\n" if GGUF_USE_NO_THINK else ""
        return (
            f"{think_prefix}"
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
            f"{weather_line}\n"
            f"{date_line}\n"
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

            # Pregnancy gestation: partners enter a 3-tick pregnancy, then child is born
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
                    self._start_pregnancy(sim_a, sim_b)
                    fired = True
                    break

            # Divorce: hostile partner state can split household and funds
            if rec.romance >= 70 and rec.friendship < -20 and random.random() < 0.18:
                self._trigger_divorce(sim_a, sim_b)
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

        self._local_sim_ids.add(child.sim_id)
        if self._network:
            self._network.add_owned_sim(child.sim_id)

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
        self.ancestry.register_birth(
            child.sim_id,
            [parent_a.sim_id, parent_b.sim_id],
            inherited={
                "traits": list(child.profile.get("traits", [])),
                "genes": dict(child.profile.get("genes", {})),
            },
        )
        if self.milestones.grant(
            child.sim_id,
            "birth",
            self._tick_count,
            source="spawn_child",
            meta={"parents": [parent_a.sim_id, parent_b.sim_id]},
        ):
            child.milestones.append({"id": "birth", "tick": self._tick_count})
        self.life_states.register_hybrid_offspring(child, parent_a, parent_b)
        self.dynasties.on_child_born(child, parent_a, parent_b)
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
                logger.info(
                    "[Grief] %s enters grief arc for '%s'", sim_a.name, target_label
                )

                # Gap 8: Multi-generational grief — children of sim_b also grieve
                if sim_b:
                    for child in self.sims:
                        if sim_b.sim_id in child.parent_ids and child.grief_stage < 0:
                            start_grief(child, sim_b.name)
                            logger.info(
                                "[GEN-GRIEF] %s (child of %s) enters grief arc",
                                child.name,
                                sim_b.name,
                            )

            # System 4: NLI-inferred goal toward closest friend after notable life events
            try:
                from core.goals import set_goal_from_life_event

                closest_id = self._find_closest_friend_id(sim_a)
                if closest_id:
                    set_goal_from_life_event(
                        sim_a,
                        event_type,
                        closest_id,
                        self._tick_count,
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
                    pressures = compute_conformity_pressure(
                        participants, "agreeableness"
                    )
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

    # ── Life cycle ───────────────────────────────────────────────────────────────

    def _advance_all_ages(self) -> None:
        """Called every TICKS_PER_YEAR ticks. Ages sims, fires transitions, marks deaths."""
        from core.life_stage import (
            advance_age,
            apply_stage_transition,
            should_die,
            elder_tick_effects,
        )

        for sim in list(self.sims):
            new_age, old_stage, new_stage = advance_age(sim)

            # Life stage transition
            if old_stage != new_stage:
                msgs = apply_stage_transition(sim, old_stage, new_stage)
                for m in msgs:
                    logger.info("[AGE] %s", m)
                self._bus.emit(
                    "stage_transition",
                    sim=sim,
                    old_stage=old_stage,
                    new_stage=new_stage,
                    age=new_age,
                    tick=self._tick_count,
                )
                self._assign_age_and_formative_traits(sim, new_stage)

            # Birthday log
            logger.info("[AGE] %s is now %d (%s)", sim.name, new_age, new_stage)

            # Check natural death
            if should_die(sim) and not getattr(sim, "_death_queued", False):
                sim._death_queued = True
                logger.info("[DEATH] %s age %d — natural causes", sim.name, new_age)
                self._queue_death(sim, cause="old_age")

        # Elder extra tick effects (energy drain)
        for sim in self.sims:
            if sim.profile.get("age", 0) >= 60:
                elder_tick_effects(sim)

    def _queue_death(self, sim: Sim, cause: str = "unknown") -> None:
        """Fire the death life event and schedule sim removal."""
        if not hasattr(self, "_death_queue"):
            self._death_queue: list[tuple[str, str]] = []
        self._death_queue.append((sim.sim_id, cause))

        # Trigger grief in children and closest friends
        from core.arcs import start_grief

        for other in self.sims:
            if other.sim_id == sim.sim_id:
                continue
            is_child = sim.sim_id in other.parent_ids
            rel = self.relationships.get(sim.sim_id, other.sim_id)
            is_close_friend = rel.friendship >= 60
            if (is_child or is_close_friend) and other.grief_stage < 0:
                start_grief(other, sim.name)
                logger.info("[GRIEF] %s grieves for %s", other.name, sim.name)

        # Inheritance
        children = [s for s in self.sims if sim.sim_id in s.parent_ids]
        if children and sim.simoleons > 0:
            share = sim.simoleons / len(children)
            for child in children:
                child.simoleons += share
                child.emotion.add("relief", 0.4, duration=4, source="inheritance")
            logger.info(
                "[INHERITANCE] %s → §%.0f to %d child(ren)",
                sim.name,
                sim.simoleons,
                len(children),
            )

        self._bus.emit(
            "sim_died",
            sim=sim,
            age=sim.profile.get("age", 0),
            tick=self._tick_count,
        )

    def _process_deaths(self) -> None:
        """Remove queued dead sims from the roster and invoke the Grim Reaper."""
        if not hasattr(self, "_death_queue") or not self._death_queue:
            return
        for entry in self._death_queue:
            # Support both old str entries and new (str, cause) tuples
            if isinstance(entry, tuple):
                sim_id, cause = entry
            else:
                sim_id, cause = entry, "unknown"

            dead_sim = self._sim_lookup.get(sim_id)
            if dead_sim:
                # Grim Reaper arrival + tombstone
                lot_id = getattr(dead_sim, "household_id", "") or ""
                grim_result = self.grim_reaper.on_sim_death(
                    dead_sim, cause, lot_id, self._tick_count
                )
                ghost_trait = grim_result.get("ghost_trait", "haunting_presence")

                # Ghost spawning (35% chance, tagged by death cause)
                if random.random() < 0.35:
                    from core.sim import Sim as SimClass

                    ghost_profile = dict(dead_sim.profile)
                    ghost_profile["id"] = f"ghost_{dead_sim.sim_id}"
                    ghost_profile["name"] = f"{dead_sim.name} (Ghost)"
                    ghost = SimClass(ghost_profile)
                    ghost.is_ghost = True
                    ghost.occult_type = "ghost"
                    ghost.add_trait(ghost_trait, source="death")
                    ghost.household_id = dead_sim.household_id
                    ghost.simoleons = 0.0
                    self.sims.append(ghost)
                    self._sim_lookup[ghost.sim_id] = ghost

                logger.info(
                    "[GRIM] %s (%s) — linger=%s chance=%.0f%%",
                    dead_sim.name,
                    cause,
                    grim_result.get("grim_lingering"),
                    grim_result.get("linger_chance", 0) * 100,
                )

            self.sims = [s for s in self.sims if s.sim_id != sim_id]
            self._sim_lookup.pop(sim_id, None)
            self._pending = [
                p
                for p in self._pending
                if p.sim_a_id != sim_id and p.sim_b_id != sim_id
            ]
        self._death_queue.clear()

    @property
    def all_sims_dead(self) -> bool:
        return len(self.sims) == 0

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

    def _assign_age_and_formative_traits(self, sim: "Sim", stage: str) -> None:
        from config import AGE_TRAIT_CANDIDATES

        options = AGE_TRAIT_CANDIDATES.get(stage.lower(), [])
        if options and random.random() < 0.55:
            sim.add_trait(random.choice(options), source="formative")
        if stage.lower() in ("child", "teen") and random.random() < 0.35:
            sim.add_trait(
                random.choice(["explorer_past", "caregiver_past", "rebellious_past"]),
                source="formative",
            )

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
                sim.sim_id,
                npc.npc_id,
                outcome["memory_tag"],
                outcome["valence"],
                tick=self._tick_count,
            )
            logger.info(
                "[NPC] %s met %s → '%s' (valence=%.2f)",
                sim.name,
                npc.name,
                dialogue[:40] if dialogue else "",
                outcome["valence"],
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
            self._submit_interaction(
                rep_a,
                rep_b,
                interaction,
                {
                    "name": "house party",
                    "noise": 0.8,
                    "intimacy": 0.4,
                    "crowd": 0.9,
                },
            )
            logger.info(
                "[CROSS-HH] %s ↔ %s social event triggered",
                hh_a.name,
                hh_b.name,
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
                children = [s for s in self.sims if sim.sim_id in s.parent_ids]
                if not children:
                    continue
                share = sim.simoleons / len(children)
                for child in children:
                    child.simoleons += share
                    child.emotion.add("relief", 0.5, duration=5, source="inheritance")
                    self.memory_store.write(
                        child.sim_id,
                        sim.sim_id,
                        f"inherited simoleons from {sim.name}",
                        0.6,
                        tick=self._tick_count,
                    )
                sim.simoleons = 0
                sim._inheritance_done = True
                logger.info(
                    "[INHERITANCE] %s distributed §%.0f to %d children",
                    sim.name,
                    share * len(children),
                    len(children),
                )
        except Exception as exc:
            logger.debug("Inheritance check failed: %s", exc)

    def _apply_seasonal_mood(self, sim: Sim, hour: int) -> None:
        """Apply time-of-day and seasonal mood modulation (Tier 3, #11)."""
        month = 1 + (self._tick_count // 200) % 12  # 1 month per 200 ticks

        # Seasonal effects
        if month in (12, 1, 2):  # winter
            sim.needs.energy = max(0, sim.needs.energy - 0.15)
            sim.needs.social = max(0, sim.needs.social - 0.10)
        elif month in (6, 7, 8):  # summer
            sim.needs.fun = min(100, sim.needs.fun + 0.15)

        # Time-of-day effects
        if 6 <= hour < 9:  # morning
            sim.needs.energy = max(0, sim.needs.energy - 0.20)
        elif 18 <= hour < 21:  # evening social/fun bonus
            sim.needs.social = min(100, sim.needs.social + 0.10)
            sim.needs.fun = min(100, sim.needs.fun + 0.10)

    def _check_jealousy(self, flirter: Sim, target: Sim, valence: float) -> None:
        """Detect jealousy in the target's existing partner when flirting fires."""
        for sim in self.sims:
            if sim.sim_id in (flirter.sim_id, target.sim_id):
                continue
            rel_with_target = self.relationships.get(sim.sim_id, target.sim_id)
            if rel_with_target.romance < 80:
                continue
            # This sim is target's partner — they may feel jealous
            neuro = sim.ocean.get("neuroticism", 0.5)
            increase = 15 * neuro * max(0, valence)
            rel_with_target.jealousy_score = min(
                100, rel_with_target.jealousy_score + increase
            )
            if rel_with_target.jealousy_score > 50:
                sim.emotion.add("annoyance", 0.7, duration=5, source="jealousy")
                # Damage flirter-partner relationship
                rel_with_flirter = self.relationships.get(sim.sim_id, flirter.sim_id)
                rel_with_flirter.apply_deltas(-5, 0)
                logger.info(
                    "[JEALOUSY] %s jealous of %s flirting with %s (score=%.0f)",
                    sim.name,
                    flirter.name,
                    target.name,
                    rel_with_target.jealousy_score,
                )
            if rel_with_target.jealousy_score > 70:
                # Romance damage on the primary relationship
                rel_with_target.apply_deltas(-3, -5)
                rel_with_target.jealousy_score = 50  # reset after consequence

    def _process_gestation(self) -> None:
        # Handled by self.pregnancy.tick() (PregnancySystem) in the new system.
        pass

    def _start_pregnancy(self, parent_a: Sim, parent_b: Sim) -> None:
        # Redirect to the new 3-stage PregnancySystem.
        # Guard: skip if already pregnant.
        already = any(
            rec.parent_a_id in (parent_a.sim_id, parent_b.sim_id)
            for rec in self._pregnancies.values()
        )
        if not already:
            self.pregnancy.begin(parent_a, parent_b, self)

    def _try_adoption_event(self) -> None:
        if len(self.sims) < 2 or random.random() > 0.08:
            return
        parents = random.sample(self.sims, 2)
        if any(s.profile.get("age", 0) < 20 for s in parents):
            return
        rel = self.relationships.get(parents[0].sim_id, parents[1].sim_id)
        family_intent = any(
            s.profile.get("aspiration") == "Family"
            or "family-oriented" in s.profile.get("traits", [])
            for s in parents
        )
        if rel.friendship < 35 or not family_intent:
            return
        from identity.profile_factory import generate_sim_profile
        from core.sim import Sim as SimClass

        profile = generate_sim_profile()
        profile["parent_ids"] = [parents[0].sim_id, parents[1].sim_id]
        profile["age"] = random.randint(1, 10)
        child = SimClass(profile)
        child.simoleons = random.uniform(100, 350)
        self.sims.append(child)
        self._sim_lookup[child.sim_id] = child
        if parents[0].household_id:
            child.household_id = parents[0].household_id
            for hh in self.households:
                if hh.id == parents[0].household_id:
                    hh.member_ids.append(child.sim_id)
                    break
        self._bus.emit(
            "child_born",
            child=child,
            parent_a=parents[0],
            parent_b=parents[1],
            tick=self._tick_count,
        )

    def _process_illness_and_transmission(self) -> None:
        for sim in self.sims:
            if (
                sim.health_status == "healthy"
                and sim.needs.energy < 20
                and random.random() < 0.04
            ):
                sim.health_status = "ill"
                sim.illness_ticks_left = random.randint(3, 6)
                sim.emotion.add("nervousness", 0.6, duration=4, source="illness")
            elif sim.health_status == "ill":
                sim.illness_ticks_left -= 1
                sim.needs.energy = max(0, sim.needs.energy - 1.0)
                if sim.illness_ticks_left <= 0:
                    sim.health_status = "healthy"

        for sim_a in self.sims:
            if sim_a.health_status != "ill":
                continue
            for sim_b in self.sims:
                if sim_a.sim_id == sim_b.sim_id or sim_b.health_status != "healthy":
                    continue
                same_home = (
                    sim_a.household_id and sim_a.household_id == sim_b.household_id
                )
                rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
                close_contact = rel.friendship > 45
                if (same_home or close_contact) and random.random() < 0.10:
                    sim_b.health_status = "ill"
                    sim_b.illness_ticks_left = random.randint(2, 5)

    def _process_temperature_risk(self, hour: int) -> None:
        from world.temperature_model import update_internal_temp, zone_from_temp

        month = 1 + (self._tick_count // 200) % 12
        is_winter = month in (12, 1, 2)
        is_summer = month in (6, 7, 8)
        harsh_hour = hour < 6 or hour >= 22
        for sim in self.sims:
            risk_delta = 0.0
            if is_winter and harsh_hour:
                risk_delta += 0.22
            if is_summer and 12 <= hour <= 16:
                risk_delta += 0.22
            if sim.household_id:
                risk_delta -= 0.12
            outdoor_temp = float(getattr(self.weather.current, "temperature", 20.0))
            update_internal_temp(
                sim, outdoor_temp=outdoor_temp, indoor=bool(sim.household_id)
            )
            zone = zone_from_temp(sim.internal_temperature)
            sim.thermal_state = zone
            if zone in ("very_cold", "very_hot"):
                risk_delta += 0.18
            sim.temperature_risk = max(
                0.0, min(1.0, sim.temperature_risk + risk_delta - 0.06)
            )
            if sim.temperature_risk > 0.7:
                sim.needs.energy = max(0, sim.needs.energy - 2.0)
                sim.emotion.add("discomfort", 0.5, duration=2, source="temperature")
            if sim.internal_temperature <= sim.min_temp_limit:
                sim.health_status = "critical"
                sim.emotion.add("fear", 0.8, duration=3, source="freezing")
            if sim.internal_temperature >= sim.max_temp_limit:
                sim.health_status = "critical"
                sim.emotion.add("discomfort", 0.8, duration=3, source="heat_collapse")

    def _run_gig_economy(self) -> None:
        # Replaced by self.gigs (GigManager) — ticked in run_tick directly.
        pass

    def _run_property_system(self) -> None:
        # Replaced by self.properties (PropertyManager) — ticked in run_tick directly.
        pass

    def _run_business_system(self) -> None:
        business_defs = {
            "retail": {
                "buy": 5200.0,
                "upkeep": 26.0,
                "income": 54.0,
                "skill": "charisma",
            },
            "restaurant": {
                "buy": 6800.0,
                "upkeep": 38.0,
                "income": 72.0,
                "skill": "cooking",
            },
            "vet": {"buy": 6100.0, "upkeep": 33.0, "income": 66.0, "skill": "logic"},
        }
        for sim in self.sims:
            if (
                len(sim.owned_businesses) < 1
                and sim.simoleons > 7000
                and random.random() < 0.03
            ):
                name = random.choice(list(business_defs.keys()))
                cfg = business_defs[name]
                _eng = getattr(sim, '_engine_ref', None)
                if _eng:
                    from persistence.ledger import TX_BUSINESS_PURCHASE
                    _eng._tx(sim, -cfg['buy'], TX_BUSINESS_PURCHASE, counterpart=name, description=f'bought {name} business')
                else:
                    sim.simoleons -= cfg['buy']
                sim.owned_businesses.append(name)
            for biz in sim.owned_businesses:
                cfg = business_defs.get(biz)
                if not cfg:
                    continue
                skill_name = str(cfg["skill"])
                skill_bonus = 1.0 + (sim.skills.levels.get(skill_name, 0) / 35.0)
                net = cfg["income"] * skill_bonus - cfg["upkeep"]
                if hasattr(self, '_tx') and net != 0:
                    from persistence.ledger import TX_BUSINESS_NET
                    _eng = getattr(sim, '_engine_ref', None)
                    if _eng: _eng._tx(sim, net, TX_BUSINESS_NET, counterpart=biz, description=f'business net: {biz}')
                    else: sim.simoleons = max(0.0, sim.simoleons + net)
                else:
                    sim.simoleons = max(0.0, sim.simoleons + net)

    def _run_education_system(self) -> None:
        hour = (GAME_START_HOUR + self._tick_count) % 24
        for sim in self.sims:
            age = int(sim.profile.get("age", 25))
            if age > 17:
                continue
            school_time = 8 <= hour <= 14
            if school_time:
                growth = 0.35 + sim.skills.modifier("logic") * 0.4
                fatigue_penalty = 0.4 if sim.needs.energy < 25 else 0.0
                sim.school_performance = max(
                    0.0,
                    min(100.0, sim.school_performance + growth - fatigue_penalty),
                )
                sim.homework_progress = max(0.0, sim.homework_progress - 2.0)
            elif 16 <= hour <= 21:
                if random.random() < 0.45:
                    sim.homework_progress = min(100.0, sim.homework_progress + 9.0)
                    sim.skills.gain_xp("logic", 0.08)
            if self._tick_count % 20 == 0 and sim.homework_progress < 35:
                sim.school_performance = max(0.0, sim.school_performance - 3.0)
                sim.emotion.add(
                    "nervousness", 0.4, duration=3, source="school pressure"
                )
            sim.scholarship_points = min(
                100.0,
                sim.scholarship_points + (sim.school_performance / 1000.0),
            )
            sim.university_readiness = min(
                100.0,
                sim.university_readiness
                + (sim.school_performance / 1200.0)
                + (sim.skills.levels.get("logic", 0) / 500.0),
            )
            if sim.profile.get("aspiration") == "Knowledge":
                try:
                    from core.knowledge_aspiration import apply_academic_progression

                    apply_academic_progression(sim, hour)
                except Exception:
                    pass

    def _run_university_system(self) -> None:
        majors = [
            "computer science",
            "fine arts",
            "biology",
            "business",
            "mathematics",
            "physics",
        ]
        for sim in self.sims:
            age = int(sim.profile.get("age", 25))
            if age < 18:
                continue

            if (
                sim.university_status == "none"
                and sim.university_readiness >= 55
                and sim.scholarship_points >= 20
                and random.random() < 0.06
            ):
                sim.university_status = "enrolled"
                if sim.profile.get("aspiration") == "Knowledge":
                    try:
                        from core.knowledge_aspiration import choose_knowledge_major

                        sim.degree_track = choose_knowledge_major(sim, majors)
                    except Exception:
                        sim.degree_track = random.choice(majors)
                else:
                    sim.degree_track = random.choice(majors)
                sim.degree_progress = 0.0
                if sim.profile.get("aspiration") == "Knowledge":
                    try:
                        from core.knowledge_aspiration import scholarship_value

                        _eng = getattr(sim, '_engine_ref', None)
                        _sv = scholarship_value(sim)
                        if _eng:
                            from persistence.ledger import TX_SCHOLARSHIP
                            _eng._tx(sim, _sv, TX_SCHOLARSHIP, description='knowledge aspiration scholarship')
                        else:
                            sim.simoleons += _sv
                    except Exception:
                        pass
                sim.emotion.add(
                    "optimism", 0.6, duration=6, source="university admission"
                )

            if sim.university_status != "enrolled":
                continue

            study_gain = 0.35 + sim.skills.modifier("logic") * 0.4
            if sim.homework_progress >= 50:
                study_gain += 0.15
            sim.degree_progress = min(100.0, sim.degree_progress + study_gain)
            _eng = getattr(sim, '_engine_ref', None)
            if _eng:
                from persistence.ledger import TX_UNIVERSITY_FEE
                _eng._tx(sim, -8.0, TX_UNIVERSITY_FEE, description='university tuition')
            else:
                sim.simoleons = max(0.0, sim.simoleons - 8.0)

            if sim.degree_progress >= 100.0:
                sim.university_status = "graduated"
                _eng = getattr(sim, '_engine_ref', None)
                if _eng:
                    from persistence.ledger import TX_CAREER_BONUS
                    _eng._tx(sim, 12, TX_CAREER_BONUS, description='career branch bonus')
                else:
                    _eng = getattr(sim, '_engine_ref', None)
                if _eng:
                    from persistence.ledger import TX_GRADUATION_BONUS
                    _eng._tx(sim, 1200, TX_GRADUATION_BONUS, description='graduation bonus')
                else:
                    sim.simoleons += 1200
                sim.career_performance = min(100.0, sim.career_performance + 10.0)
                sim.emotion.add("joy", 0.8, duration=8, source="graduation")

    def _run_career_progression_system(self) -> None:
        if self._tick_count % 6 != 0:
            return
        branch_map = {
            "Software Engineer": ("startup", "enterprise"),
            "Researcher": ("lab", "field"),
            "Artist": ("commercial", "indie"),
        }
        for sim in self.sims:
            if int(sim.profile.get("age", 25)) < 18:
                continue

            if sim.work_from_home_task is None and random.random() < 0.25:
                sim.work_from_home_task = {
                    "task": random.choice(
                        ["file report", "client call", "prototype draft"]
                    ),
                    "due_tick": self._tick_count + random.randint(2, 4),
                    "skill": random.choice(["logic", "charisma", "creativity"]),
                }

            task = sim.work_from_home_task
            if task and self._tick_count >= int(task["due_tick"]):
                level = sim.skills.levels.get(str(task["skill"]), 0)
                if level >= 4:
                    sim.career_performance = min(100.0, sim.career_performance + 4.0)
                    sim.simoleons += 80
                else:
                    sim.career_performance = max(0.0, sim.career_performance - 2.0)
                sim.work_from_home_task = None

            if (
                sim.career_performance < 18
                and sim.career_level > 1
                and random.random() < 0.35
            ):
                sim.career_level -= 1
                sim.career_performance = 35.0
                sim.emotion.add(
                    "disappointment", 0.5, duration=4, source="career demotion"
                )

            if sim.career_performance >= 72:
                sim.career_level = min(10, sim.career_level + 1)
                sim.career_performance = max(45.0, sim.career_performance - 18.0)

            if sim.career_level >= 5 and sim.career_branch == "base":
                branches = branch_map.get(
                    sim.profile.get("job", ""), ("specialist", "management")
                )
                sim.career_branch = random.choice(branches)

            if sim.career_branch in {"startup", "field", "indie", "specialist"}:
                sim.skills.gain_xp("creativity", 0.04)
                _eng = getattr(sim, '_engine_ref', None)
                if _eng:
                    from persistence.ledger import TX_CAREER_BONUS
                    _eng._tx(sim, 10, TX_CAREER_BONUS, description='career branch bonus')
                else:
                    sim.simoleons += 10
            elif sim.career_branch in {"enterprise", "lab", "commercial", "management"}:
                sim.skills.gain_xp("charisma", 0.04)
                sim.simoleons += 12

            if random.random() < 0.20:
                good_outcome = random.random() < 0.62
                if good_outcome:
                    sim.career_performance = min(100.0, sim.career_performance + 2.5)
                    sim.emotion.add("optimism", 0.3, duration=2, source="chance card")
                else:
                    sim.career_performance = max(0.0, sim.career_performance - 2.5)
                    sim.emotion.add(
                        "nervousness", 0.3, duration=2, source="chance card"
                    )

    def _run_occult_system(self) -> None:
        occult_pool = ["vampire", "werewolf", "spellcaster"]
        for sim in self.sims:
            if sim.is_ghost:
                continue
            if sim.occult_type == "none" and random.random() < 0.003:
                sim.occult_type = random.choice(occult_pool)
                sim.occult_power = 10.0
                if sim.occult_type == "vampire":
                    sim.occult_perks = ["night_vision"]
                    sim.occult_weaknesses = ["sunlight_frailty"]
                elif sim.occult_type == "werewolf":
                    sim.occult_perks = ["primal_strength"]
                    sim.occult_weaknesses = ["lunar_rage"]
                elif sim.occult_type == "spellcaster":
                    sim.occult_perks = ["quick_channeling"]
                    sim.occult_weaknesses = ["mana_drain"]
                sim.emotion.add("surprise", 0.7, duration=5, source="occult awakening")
                if sim.profile.get("aspiration") == "Knowledge":
                    try:
                        from core.knowledge_aspiration import apply_occult_curiosity

                        apply_occult_curiosity(sim)
                    except Exception:
                        pass

            if sim.occult_type == "vampire":
                sim.occult_power = min(100.0, sim.occult_power + 0.15)
                sim.needs.energy = min(100.0, sim.needs.energy + 0.25)
                sim.needs.hunger = max(0.0, sim.needs.hunger - 0.05)
                if sim.temperature_risk > 0.8 and random.random() < 0.06:
                    sim.needs.energy = max(0.0, sim.needs.energy - 8.0)
            elif sim.occult_type == "werewolf":
                sim.occult_power = min(100.0, sim.occult_power + 0.12)
                sim.needs.fun = min(100.0, sim.needs.fun + 0.2)
                if random.random() < 0.02:
                    sim.emotion.add("anger", 0.5, duration=2, source="lunar agitation")
            elif sim.occult_type == "spellcaster":
                sim.occult_power = min(100.0, sim.occult_power + 0.18)
                sim.skills.gain_xp("logic", 0.03)
                sim.skills.gain_xp("creativity", 0.03)
                if random.random() < 0.03:
                    sim.needs.energy = max(0.0, sim.needs.energy - 2.5)

            if sim.profile.get("aspiration") == "Knowledge":
                try:
                    from core.knowledge_aspiration import apply_occult_curiosity

                    apply_occult_curiosity(sim)
                except Exception:
                    pass

            if sim.occult_power >= 35 and len(sim.occult_perks) < 2:
                if sim.occult_type == "vampire":
                    sim.occult_perks.append("mesmerize")
                elif sim.occult_type == "werewolf":
                    sim.occult_perks.append("keen_senses")
                elif sim.occult_type == "spellcaster":
                    sim.occult_perks.append("ritual_focus")
            if sim.occult_power >= 60 and len(sim.occult_weaknesses) < 2:
                if sim.occult_type == "vampire":
                    sim.occult_weaknesses.append("thirst_instability")
                elif sim.occult_type == "werewolf":
                    sim.occult_weaknesses.append("feral_impulses")
                elif sim.occult_type == "spellcaster":
                    sim.occult_weaknesses.append("arcane_overload")

    def _run_perk_progression(self) -> None:
        perk_defs = {
            "social_butterfly": lambda s: s.skills.levels.get("charisma", 0) >= 6,
            "craft_master": lambda s: s.skills.levels.get("creativity", 0) >= 6,
            "logic_strategist": lambda s: s.skills.levels.get("logic", 0) >= 6,
            "fit_lifestyle": lambda s: s.skills.levels.get("fitness", 0) >= 6,
            "culinary_instinct": lambda s: s.skills.levels.get("cooking", 0) >= 6,
        }
        for sim in self.sims:
            level_total = int(sum(sim.skills.levels.values()))
            if level_total - sim._last_perk_level_total >= 4:
                sim.perk_points += 1
                sim._last_perk_level_total = level_total
            if sim.perk_points > 0:
                for perk, condition in perk_defs.items():
                    if perk not in sim.perks and condition(sim):
                        sim.perks.add(perk)
                        sim.perk_points -= 1
                        break

    def _process_phone_actions(self) -> None:
        for sim in self.sims:
            if random.random() < 0.10 and len(self.sims) > 1:
                target = random.choice([s for s in self.sims if s.sim_id != sim.sim_id])
                sim.pending_phone_actions.append(
                    {
                        "target_id": target.sim_id,
                        "type": random.choice(["text", "call", "invite"]),
                        "resolve_at": self._tick_count + random.randint(1, 3),
                    }
                )
            carry: list[dict[str, object]] = []
            for action in sim.pending_phone_actions:
                resolve_at = int(action["resolve_at"])
                if self._tick_count < resolve_at:
                    carry.append(action)
                    continue
                target = self._sim_lookup.get(str(action["target_id"]))
                if not target:
                    continue
                rel = self.relationships.get(sim.sim_id, target.sim_id)
                delta = {"text": 1.5, "call": 2.0, "invite": 2.5}.get(
                    str(action["type"]), 1.0
                )
                rel.apply_deltas(delta, 0)
                if str(action["type"]) == "invite" and random.random() < 0.65:
                    invite = {
                        "from_id": sim.sim_id,
                        "to_id": target.sim_id,
                        "type": "hangout",
                        "respond_by": self._tick_count + 2,
                        "status": "pending",
                    }
                    target.pending_invitations.append(invite)
            sim.pending_phone_actions = carry

    def _process_family_planning(self) -> None:
        carry: list[dict[str, str | int]] = []
        for intent in self._try_for_baby_intents:
            resolve_at = int(intent["resolve_at"])
            if self._tick_count < resolve_at:
                carry.append(intent)
                continue
            parent_a = self._sim_lookup.get(str(intent["parent_a_id"]))
            parent_b = self._sim_lookup.get(str(intent["parent_b_id"]))
            if not parent_a or not parent_b:
                continue
            rel = self.relationships.get(parent_a.sim_id, parent_b.sim_id)
            chance = 0.38 if rel.romance >= 80 else 0.22
            if random.random() < chance:
                self._start_pregnancy(parent_a, parent_b)
            else:
                parent_a.emotion.add(
                    "disappointment", 0.4, duration=3, source="family planning"
                )
                parent_b.emotion.add(
                    "disappointment", 0.4, duration=3, source="family planning"
                )
        self._try_for_baby_intents = carry

    def _run_custody_schedule(self) -> None:
        if self._tick_count % 7 != 0:
            return
        for child_id, info in self._custody.items():
            child = self._sim_lookup.get(child_id)
            if not child:
                continue
            primary = str(info.get("primary", ""))
            parent_a_id = str(info.get("parent_a", ""))
            parent_b_id = str(info.get("parent_b", ""))
            visiting_parent_id = (
                parent_b_id
                if child.household_id == primary and parent_b_id
                else parent_a_id
            )
            visiting_parent = self._sim_lookup.get(visiting_parent_id)
            if not visiting_parent:
                continue
            rel = self.relationships.get(child.sim_id, visiting_parent.sim_id)
            rel.apply_deltas(2.5, 0)
            child.needs.restore("social", 6)

    def _run_odd_jobs(self) -> None:
        for sim in self.sims:
            if (
                sim.active_odd_job is None
                and sim.simoleons < 900
                and random.random() < 0.16
            ):
                sim.active_odd_job = {
                    "title": random.choice(
                        ["pet sitting", "delivery run", "yard cleanup", "moving help"]
                    ),
                    "deadline": self._tick_count + random.randint(1, 3),
                    "payout": random.randint(45, 120),
                    "difficulty": random.randint(1, 5),
                    "skill": random.choice(["fitness", "charisma", "logic"]),
                }
            odd_job = sim.active_odd_job
            if not odd_job:
                continue
            if self._tick_count < int(odd_job["deadline"]):
                continue
            skill_level = sim.skills.levels.get(str(odd_job["skill"]), 0)
            base_payout = float(odd_job["payout"])
            payout = (
                base_payout
                if skill_level >= float(odd_job["difficulty"])
                else base_payout * 0.6
            )
            _eng = getattr(sim, '_engine_ref', None)
            if _eng:
                from persistence.ledger import TX_ODD_JOB
                _eng._tx(sim, payout, TX_ODD_JOB, description='odd job payout')
            else:
                sim.simoleons += payout
            sim.odd_job_reputation = min(
                100.0, sim.odd_job_reputation + (2.0 if payout >= base_payout else 0.8)
            )
            sim.active_odd_job = None

    def _process_bills_and_household_expenses(self) -> None:
        if self._tick_count % 12 != 0:
            return
        for hh in self.households:
            members = [s for s in self.sims if s.sim_id in hh.member_ids]
            if not members:
                continue
            property_count = sum(len(s.properties) for s in members)
            bill = 80 + 20 * len(members) + 35 * property_count
            if hh.funds >= bill:
                hh.funds -= bill
            else:
                remaining = bill - hh.funds
                hh.funds = 0.0
                split = remaining / len(members)
                for sim in members:
                    _eng = getattr(sim, '_engine_ref', None)
                    if _eng:
                        from persistence.ledger import TX_HOUSEHOLD_BILL
                        _eng._tx(sim, -split, TX_HOUSEHOLD_BILL, description='household bill split')
                    else:
                        sim.simoleons = max(0.0, sim.simoleons - split)
                    if sim.simoleons < 150:
                        sim.emotion.add("nervousness", 0.5, duration=4, source="bills")
                self._world_event_memory(
                    members,
                    "bills_stress",
                    valence=-0.2,
                    gossip=True,
                )

    def _run_calendar_events(self) -> None:
        for sim in self.sims:
            kept_invites = []
            for invite in sim.pending_invitations:
                if invite.get("status") != "pending":
                    continue
                respond_by = int(invite.get("respond_by", 0))
                if self._tick_count < respond_by:
                    kept_invites.append(invite)
                    continue
                sender = self._sim_lookup.get(str(invite.get("from_id", "")))
                if sender is None:
                    continue
                accept = random.random() < 0.68
                if accept:
                    self._calendar_events.append(
                        {
                            "type": str(invite.get("type", "hangout")),
                            "host_id": sender.sim_id,
                            "guest_id": sim.sim_id,
                            "scheduled_tick": self._tick_count + random.randint(1, 3),
                            "status": "scheduled",
                        }
                    )
                else:
                    rel = self.relationships.get(sender.sim_id, sim.sim_id)
                    rel.apply_deltas(-1.5, 0.0)
                    self._world_event_memory(
                        [sender, sim],
                        "invite_rejected",
                        valence=-0.25,
                        gossip=True,
                    )
            sim.pending_invitations = kept_invites

        for evt in self._calendar_events:
            if evt.get("status") != "scheduled":
                continue
            when = int(evt.get("scheduled_tick", 0))
            if self._tick_count < when:
                continue
            host = self._sim_lookup.get(str(evt.get("host_id", "")))
            guest = self._sim_lookup.get(str(evt.get("guest_id", "")))
            if host and guest:
                rel = self.relationships.get(host.sim_id, guest.sim_id)
                rel.apply_deltas(4.0, 1.0)
                host.needs.restore("fun", 6)
                guest.needs.restore("fun", 6)
                self._bus.emit(
                    "calendar_event",
                    event_type=str(evt.get("type", "hangout")),
                    host=host,
                    guest=guest,
                    tick=self._tick_count,
                )
                self._world_event_memory(
                    [host, guest],
                    f"calendar_{str(evt.get('type', 'hangout'))}",
                    valence=0.35,
                    gossip=False,
                )
            evt["status"] = "completed"

        self._calendar_events = [
            evt
            for evt in self._calendar_events
            if not (
                evt.get("status") == "completed"
                and self._tick_count - int(evt.get("scheduled_tick", 0)) > 20
            )
        ]

    def _run_travel_system(self) -> None:
        destinations = ["Sulani", "Mt. Komorebi", "San Myshuno", "Henford"]
        for sim in self.sims:
            if random.random() < 0.02 and sim.simoleons >= 120:
                place = random.choice(destinations)
                sim.simoleons -= 120
                sim.travel_history.append(place)
                sim.needs.fun = min(100.0, sim.needs.fun + 10.0)
                sim.emotion.add("joy", 0.4, duration=3, source="travel")
                if "travel" in sim.profile.get("interests", []):
                    sim.skills.gain_xp("charisma", 0.05)

    def _run_pet_system(self) -> None:
        species = ["dog", "cat"]
        for sim in self.sims:
            if (
                len(sim.pet_ids) == 0
                and sim.simoleons > 1400
                and random.random() < 0.015
            ):
                pet_id = f"pet_{sim.sim_id}_{self._tick_count}"
                pet_kind = random.choice(species)
                sim.pet_ids.append(f"{pet_kind}:{pet_id}")
                sim.simoleons -= 180
            if sim.pet_ids:
                sim.needs.social = min(100.0, sim.needs.social + 0.35)
                sim.needs.fun = min(100.0, sim.needs.fun + 0.25)
                if random.random() < 0.03:
                    sim.simoleons = max(0.0, sim.simoleons - 15.0)

    def _process_survival_hazards(self) -> None:
        venue_name = str(self._venue.get("name", ""))
        for sim in self.sims:
            if sim.is_ghost:
                sim.hazard_flags = {
                    "fire": 0.0,
                    "electrocution": 0.0,
                    "starvation": 0.0,
                    "drowning": 0.0,
                    "weather_extreme": 0.0,
                }
                continue

            if sim.needs.hunger <= 2:
                sim._starvation_ticks += 1
            else:
                sim._starvation_ticks = max(0, sim._starvation_ticks - 1)
            sim.hazard_flags["starvation"] = min(1.0, sim._starvation_ticks / 4.0)
            if sim._starvation_ticks >= 4 and not getattr(sim, "_death_queued", False):
                sim._death_queued = True
                self._queue_death(sim, cause="starvation")
                self._bus.emit(
                    "hazard_event", hazard="starvation", sim=sim, tick=self._tick_count
                )
                continue

            pool_venue = venue_name in {"park", "gym"}
            if pool_venue and random.random() < 0.025 and sim.needs.energy < 30:
                sim._drowning_ticks += 1
            else:
                sim._drowning_ticks = max(0, sim._drowning_ticks - 1)
            sim.hazard_flags["drowning"] = min(1.0, sim._drowning_ticks / 4.0)
            if sim._drowning_ticks >= 4:
                defense = (
                    self.resolve_dynamic_threat(sim.household_id, "drowning")
                    if getattr(sim, "household_id", None)
                    else {"used": False}
                )
                sim._last_threat_response = {
                    "tick": self._tick_count,
                    "hazard": "drowning",
                    **dict(defense),
                }
                saved = bool(defense.get("success", False))
                if (
                    (not saved)
                    and random.random() < 0.35
                    and not getattr(sim, "_death_queued", False)
                ):
                    sim._death_queued = True
                    self._queue_death(sim, cause="drowning")
                else:
                    sim.needs.energy = max(0.0, sim.needs.energy - 18.0)
                    sim.emotion.add("fear", 0.8, duration=4, source="near drowning")
                sim._drowning_ticks = 1
                self._bus.emit(
                    "hazard_event", hazard="drowning", sim=sim, tick=self._tick_count
                )

            near_fire = venue_name in {"home (1:1)", "house party", "restaurant"}
            if near_fire and random.random() < 0.03:
                sim._near_fire_ticks += 1
            else:
                sim._near_fire_ticks = max(0, sim._near_fire_ticks - 1)
            sim.hazard_flags["fire"] = min(1.0, sim._near_fire_ticks / 5.0)
            if sim._near_fire_ticks >= 5:
                sim.needs.energy = max(0.0, sim.needs.energy - 15.0)
                sim.emotion.add("fear", 0.8, duration=4, source="fire hazard")
                sim._near_fire_ticks = 1
                self._bus.emit(
                    "hazard_event", hazard="fire", sim=sim, tick=self._tick_count
                )

            month = 1 + (self._tick_count // 200) % 12
            weather_risk = 0.0
            if month in (12, 1, 2) and sim.temperature_risk > 0.75:
                weather_risk = 0.6
            elif month in (6, 7, 8) and sim.temperature_risk > 0.8:
                weather_risk = 0.6
            sim.hazard_flags["weather_extreme"] = weather_risk
            if weather_risk > 0 and random.random() < 0.08:
                defense = (
                    self.resolve_dynamic_threat(sim.household_id, "weather_extreme")
                    if getattr(sim, "household_id", None)
                    else {"used": False}
                )
                sim._last_threat_response = {
                    "tick": self._tick_count,
                    "hazard": "weather_extreme",
                    **dict(defense),
                }
                saved = bool(defense.get("success", False))
                sim.needs.energy = max(0.0, sim.needs.energy - 10.0)
                if (
                    (not saved)
                    and random.random() < 0.18
                    and not getattr(sim, "_death_queued", False)
                ):
                    sim._death_queued = True
                    self._queue_death(sim, cause="weather_extreme")
                self._bus.emit(
                    "hazard_event",
                    hazard="weather_extreme",
                    sim=sim,
                    tick=self._tick_count,
                )

            electrical_job = sim.profile.get("job", "") in {
                "Software Engineer",
                "Researcher",
            }
            elec_risk = 0.0
            if electrical_job and sim.needs.energy < 25:
                elec_risk = 0.35
            if electrical_job and random.random() < 0.01 and sim.needs.energy < 18:
                defense = (
                    self.resolve_dynamic_threat(sim.household_id, "electrocution")
                    if getattr(sim, "household_id", None)
                    else {"used": False}
                )
                sim._last_threat_response = {
                    "tick": self._tick_count,
                    "hazard": "electrocution",
                    **dict(defense),
                }
                saved = bool(defense.get("success", False))
                sim.needs.energy = max(0.0, sim.needs.energy - 25.0)
                sim.emotion.add("fear", 0.7, duration=3, source="electrocution")
                if (
                    (not saved)
                    and random.random() < 0.22
                    and not getattr(sim, "_death_queued", False)
                ):
                    sim._death_queued = True
                    self._queue_death(sim, cause="electrocution")
                self._bus.emit(
                    "hazard_event",
                    hazard="electrocution",
                    sim=sim,
                    tick=self._tick_count,
                )
            sim.hazard_flags["electrocution"] = elec_risk

    def _trigger_divorce(self, sim_a: Sim, sim_b: Sim) -> None:
        if not sim_a.household_id or sim_a.household_id != sim_b.household_id:
            return
        household = next(
            (h for h in self.households if h.id == sim_a.household_id), None
        )
        if household is None:
            return

        split_funds = (
            household.funds
            if household.funds > 0
            else (sim_a.simoleons + sim_b.simoleons)
        )
        share = split_funds * 0.5
        sim_a.simoleons = share
        sim_b.simoleons = share
        household.funds = 0.0

        new_id = f"{household.id}_split_{self._tick_count}"
        new_household = type(household)(
            id=new_id,
            name=f"{sim_b.name.split()[0]} household",
            member_ids=[sim_b.sim_id],
            funds=share,
        )
        self.households.append(new_household)
        sim_b.household_id = new_id
        household.member_ids = [
            mid for mid in household.member_ids if mid != sim_b.sim_id
        ]

        children = [
            s
            for s in self.sims
            if sim_a.sim_id in s.parent_ids and sim_b.sim_id in s.parent_ids
        ]
        for child in children:
            stays_with_a = random.random() < 0.5
            child.household_id = (
                sim_a.household_id if stays_with_a else sim_b.household_id
            )
            self._custody[child.sim_id] = {
                "parent_a": sim_a.sim_id,
                "parent_b": sim_b.sim_id,
                "primary": child.household_id,
            }

        rel = self.relationships.get(sim_a.sim_id, sim_b.sim_id)
        rel.apply_deltas(-30, -50)
        sim_a.emotion.add("grief", 0.7, duration=7, source="divorce")
        sim_b.emotion.add("grief", 0.7, duration=7, source="divorce")

    def _apply_gift_outcome(self, giver: Sim, receiver: Sim, result: dict) -> None:
        """Apply friendship bonus based on gift interest-match."""
        gifted_name = "thoughtful note"
        if getattr(giver, "inventory_objects", []):
            outcome = self.gift_item(giver.sim_id, receiver.sim_id)
            if outcome.get("ok"):
                gifted_name = str(outcome.get("object", {}).get("name", gifted_name))
        elif giver.inventory:
            gifted_name = giver.inventory.pop(0)
            receiver.inventory.append(gifted_name)

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
        logger.info(
            "[GIFT] %s→%s item=%s match=%s bonus=+%.1f",
            giver.name,
            receiver.name,
            gifted_name,
            match,
            bonus,
        )

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
        if mentor.profile.get("aspiration") == "Knowledge":
            mentee.skills.gain_xp(gap_skill, 0.2)
            mentor.emotion.add("pride", 0.4, duration=3, source="knowledge_mentoring")
        rel.apply_deltas(3, 0)
        logger.info(
            "[MENTOR] %s teaches %s +0.5 %s", mentor.name, mentee.name, gap_skill
        )
        self._bus.emit(
            "mentor_session",
            mentor=mentor,
            mentee=mentee,
            skill=gap_skill,
            tick=self._tick_count,
        )

    def _on_tick_complete(self, **_: Any) -> None:
        try:
            if self._tick_count % 10 == 0:
                self.adaptive_policy.save()
                self.arc_policy.save()
        except Exception:
            pass
