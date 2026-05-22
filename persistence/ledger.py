"""
persistence/ledger.py — ACID financial ledger for every simoleon transaction.

Every simoleon change — salary, shop, gig, stock, property, betting, inheritance,
tax, gift, crafting, contracts, tokens, blockchain — is recorded here atomically
before the in-memory balance is updated.

ACID guarantees
---------------
Atomicity:   The ledger INSERT and sim.simoleons mutation happen inside a single
             SQLite IMMEDIATE transaction.  If the INSERT fails, simoleons is
             unchanged.  If the Python mutation crashes after INSERT, the next
             startup sees the committed entry and can reconcile.

Consistency: balance_before + amount == balance_after is enforced at write time.
             Violations raise LedgerConsistencyError; the caller must not mutate
             simoleons before calling record_tx().

Isolation:   WAL mode + a per-connection threading.Lock prevents dirty reads from
             concurrent tick threads.  SQLite's IMMEDIATE lock blocks other writers
             during each transaction.

Durability:  synchronous=FULL flushes the WAL to disk before every COMMIT.
             Combined with WAL, this survives OS crashes and power failures.

Transaction taxonomy (TX_* constants below)
--------------------------------------------
Every type maps to a category: INCOME | EXPENSE | TRANSFER_IN | TRANSFER_OUT |
CORRECTION | SYSTEM.  The category drives aggregation in reports.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim

logger = logging.getLogger(__name__)

# ── Transaction type registry ─────────────────────────────────────────────────
# Format: TX_<NAME> = "<snake_case>"  (stored verbatim in DB)

# Income
TX_SALARY               = "salary"
TX_GIG_PAYOUT           = "gig_payout"
TX_ODD_JOB              = "odd_job"
TX_PROPERTY_DIVIDEND    = "property_dividend"
TX_BUSINESS_NET         = "business_net"
TX_STOCK_SALE           = "stock_sale"
TX_CRAFTING_ROYALTY     = "crafting_royalty"
TX_CRAFTING_SALE        = "crafting_sale"
TX_OBJECT_SALE          = "object_sale"
TX_TOKEN_SALE           = "token_sale"
TX_BETTING_WIN          = "betting_win"
TX_INHERITANCE          = "inheritance"
TX_SCHOLARSHIP          = "scholarship"
TX_GRADUATION_BONUS     = "graduation_bonus"
TX_CAREER_BONUS         = "career_bonus"
TX_PROGRAMMING_FREELANCE= "programming_freelance"
TX_PROGRAMMING_HACK     = "programming_hack"
TX_PROGRAMMING_ROYALTY  = "programming_royalty"
TX_SKILL_CLASS_HOST     = "skill_class_host"
TX_NEIGHBORHOOD_BIZ     = "neighborhood_biz"
TX_CONTRACT_LOAN_RECV   = "contract_loan_recv"
TX_CONTRACT_SALARY_RECV = "contract_salary_recv"
TX_CONTRACT_SETTLED_RECV= "contract_settled_recv"
TX_BURGLAR_TAKE         = "burglar_take"
TX_UNION_SUPPORT        = "union_support"
TX_SIMCOIN_MINT         = "simcoin_mint"
TX_LIFETIME_REWARD      = "lifetime_reward"
TX_ACTION_PACK_INCOME   = "action_pack_income"
TX_BANK_DEPOSIT         = "bank_deposit"
TX_BANK_INTEREST        = "bank_interest"
TX_BANK_TRANSFER        = "bank_transfer"

# Expense
TX_LIVING_COST          = "living_cost"
TX_SHOP_PURCHASE        = "shop_purchase"
TX_PROPERTY_PURCHASE    = "property_purchase"
TX_PROPERTY_MAINTENANCE = "property_maintenance"
TX_PROPERTY_TAX         = "property_tax"
TX_PROPERTY_UPGRADE     = "property_upgrade"
TX_BUSINESS_PURCHASE    = "business_purchase"
TX_STOCK_PURCHASE       = "stock_purchase"
TX_CRAFTING_COST        = "crafting_cost"
TX_TOKEN_PURCHASE       = "token_purchase"
TX_BETTING_LOSS         = "betting_loss"
TX_CONTRACT_LOAN_REPAY  = "contract_loan_repay"
TX_CONTRACT_BREACH_FEE  = "contract_breach_fee"
TX_HOUSEHOLD_BILL       = "household_bill"
TX_INCOME_TAX           = "income_tax"
TX_LEGAL_FINE           = "legal_fine"
TX_NOISE_FINE           = "noise_fine"
TX_DEBT_COLLECTION      = "debt_collection"
TX_TRAVEL_COST          = "travel_cost"
TX_PET_COST             = "pet_cost"
TX_SKILL_CLASS_FEE      = "skill_class_fee"
TX_UNIVERSITY_FEE       = "university_fee"
TX_SIMCOIN_BURN         = "simcoin_burn"
TX_ACTION_PACK_COST     = "action_pack_cost"
TX_BANKRUPTCY_SEIZURE   = "bankruptcy_seizure"

# Transfers
TX_GIFT_SENT            = "gift_sent"
TX_GIFT_RECEIVED        = "gift_received"
TX_TRANSFER_OUT         = "transfer_out"
TX_TRANSFER_IN          = "transfer_in"
TX_CONTRACT_SETTLED_PAY = "contract_settled_pay"
TX_INHERITANCE_SHARE    = "inheritance_share"

# System / correction
TX_CORRECTION           = "correction"
TX_CHAIN_SYNC           = "chain_sync"

# ── Category mapping ──────────────────────────────────────────────────────────

_INCOME_TYPES: frozenset[str] = frozenset({
    TX_BANK_INTEREST, TX_BANK_TRANSFER,
    TX_SALARY, TX_GIG_PAYOUT, TX_ODD_JOB, TX_PROPERTY_DIVIDEND,
    TX_BUSINESS_NET, TX_STOCK_SALE, TX_CRAFTING_ROYALTY, TX_CRAFTING_SALE,
    TX_OBJECT_SALE, TX_TOKEN_SALE, TX_BETTING_WIN, TX_INHERITANCE,
    TX_SCHOLARSHIP, TX_GRADUATION_BONUS, TX_CAREER_BONUS,
    TX_PROGRAMMING_FREELANCE, TX_PROGRAMMING_HACK, TX_PROGRAMMING_ROYALTY,
    TX_SKILL_CLASS_HOST, TX_NEIGHBORHOOD_BIZ, TX_CONTRACT_LOAN_RECV,
    TX_CONTRACT_SALARY_RECV, TX_CONTRACT_SETTLED_RECV, TX_BURGLAR_TAKE,
    TX_UNION_SUPPORT, TX_SIMCOIN_MINT, TX_LIFETIME_REWARD, TX_ACTION_PACK_INCOME,
})

_EXPENSE_TYPES: frozenset[str] = frozenset({
    TX_BANK_DEPOSIT,
    TX_LIVING_COST, TX_SHOP_PURCHASE, TX_PROPERTY_PURCHASE,
    TX_PROPERTY_MAINTENANCE, TX_PROPERTY_TAX, TX_PROPERTY_UPGRADE,
    TX_BUSINESS_PURCHASE, TX_STOCK_PURCHASE, TX_CRAFTING_COST,
    TX_TOKEN_PURCHASE, TX_BETTING_LOSS, TX_CONTRACT_LOAN_REPAY,
    TX_CONTRACT_BREACH_FEE, TX_HOUSEHOLD_BILL, TX_INCOME_TAX,
    TX_LEGAL_FINE, TX_NOISE_FINE, TX_DEBT_COLLECTION, TX_TRAVEL_COST,
    TX_PET_COST, TX_SKILL_CLASS_FEE, TX_UNIVERSITY_FEE, TX_SIMCOIN_BURN,
    TX_ACTION_PACK_COST, TX_BANKRUPTCY_SEIZURE,
})

_TRANSFER_IN_TYPES: frozenset[str] = frozenset({
    TX_GIFT_RECEIVED, TX_TRANSFER_IN, TX_INHERITANCE_SHARE, TX_CONTRACT_SETTLED_RECV,
})

_TRANSFER_OUT_TYPES: frozenset[str] = frozenset({
    TX_GIFT_SENT, TX_TRANSFER_OUT, TX_CONTRACT_SETTLED_PAY,
})


def _category(tx_type: str) -> str:
    if tx_type in _INCOME_TYPES:
        return "income"
    if tx_type in _EXPENSE_TYPES:
        return "expense"
    if tx_type in _TRANSFER_IN_TYPES:
        return "transfer_in"
    if tx_type in _TRANSFER_OUT_TYPES:
        return "transfer_out"
    return "system"


# ── Anomaly thresholds ────────────────────────────────────────────────────────
# A transaction is flagged if its absolute amount exceeds this per-type ceiling.
# Set to None to disable per-type flagging.

ANOMALY_CEILINGS: dict[str, float] = {
    TX_SALARY:            2_000.0,
    TX_GIG_PAYOUT:        5_000.0,
    TX_PROPERTY_DIVIDEND: 50_000.0,
    TX_BUSINESS_NET:      500.0,
    TX_STOCK_SALE:        1_000_000.0,
    TX_INHERITANCE:       500_000.0,
    TX_PROGRAMMING_HACK:  200.0,
    TX_BETTING_WIN:       100_000.0,
    TX_CONTRACT_LOAN_RECV:500_000.0,
}

# Any single transaction above this is flagged regardless of type
GLOBAL_FLAG_CEILING = 10_000_000.0


# ── Exceptions ────────────────────────────────────────────────────────────────

class LedgerConsistencyError(RuntimeError):
    """Raised when balance_before + amount != balance_after."""


class InsufficientFundsError(ValueError):
    """Raised when an expense would take simoleons below -OVERDRAFT_FLOOR."""


# ── Entry dataclass ───────────────────────────────────────────────────────────

@dataclass
class LedgerEntry:
    tx_id:         str
    tick:          int
    wall_ts:       float
    sim_id:        str
    counterpart:   str
    tx_type:       str
    category:      str
    amount:        float      # signed: positive = inflow, negative = outflow
    balance_before: float
    balance_after:  float
    description:   str
    metadata:      dict
    flagged:       bool

    @property
    def is_income(self) -> bool:
        return self.amount > 0

    def to_dict(self) -> dict:
        return {
            "tx_id":          self.tx_id,
            "tick":           self.tick,
            "wall_ts":        self.wall_ts,
            "sim_id":         self.sim_id,
            "counterpart":    self.counterpart,
            "tx_type":        self.tx_type,
            "category":       self.category,
            "amount":         round(self.amount, 4),
            "balance_before": round(self.balance_before, 4),
            "balance_after":  round(self.balance_after, 4),
            "description":    self.description,
            "metadata":       self.metadata,
            "flagged":        self.flagged,
        }


# ── FinancialLedger ───────────────────────────────────────────────────────────

OVERDRAFT_FLOOR = -1_000_000.0   # simoleons floor before transaction is rejected


class FinancialLedger:
    """
    ACID financial ledger.

    record_tx() is the ONLY correct way to mutate sim.simoleons.
    It atomically records the entry and updates sim.simoleons in the same
    SQLite IMMEDIATE transaction.  All callers must use it; direct
    sim.simoleons assignments (+=, -=, =) bypass the audit trail.
    """

    def __init__(self, db_path: str = "sim_ledger.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        # isolation_level=None → autocommit; we manage transactions explicitly
        # with BEGIN IMMEDIATE so we can interleave Python mutations inside them.
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     isolation_level=None)
        # ACID settings
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")      # durability on crash
        self._conn.execute("PRAGMA wal_autocheckpoint=500")
        self._conn.execute("PRAGMA cache_size=-8192")       # 8 MB cache
        self._init_schema()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                tx_id          TEXT    PRIMARY KEY,
                tick           INTEGER NOT NULL,
                wall_ts        REAL    NOT NULL,
                sim_id         TEXT    NOT NULL,
                counterpart    TEXT    NOT NULL DEFAULT '',
                tx_type        TEXT    NOT NULL,
                category       TEXT    NOT NULL,
                amount         REAL    NOT NULL,
                balance_before REAL    NOT NULL,
                balance_after  REAL    NOT NULL,
                description    TEXT    NOT NULL DEFAULT '',
                metadata       TEXT    NOT NULL DEFAULT '{}',
                flagged        INTEGER NOT NULL DEFAULT 0
            );

            -- Fast per-sim history lookups
            CREATE INDEX IF NOT EXISTS idx_le_sim_tick
                ON ledger_entries (sim_id, tick);
            -- Fast type aggregations
            CREATE INDEX IF NOT EXISTS idx_le_type
                ON ledger_entries (tx_type, tick);
            -- Flagged entries feed directly into anomaly reports
            CREATE INDEX IF NOT EXISTS idx_le_flagged
                ON ledger_entries (flagged) WHERE flagged = 1;

            -- Running balance snapshot every N ticks for fast "balance at tick T" queries
            CREATE TABLE IF NOT EXISTS balance_snapshots (
                sim_id  TEXT    NOT NULL,
                tick    INTEGER NOT NULL,
                balance REAL    NOT NULL,
                PRIMARY KEY (sim_id, tick)
            );
        """)
        self._conn.commit()

    # ── Core write (ACID) ─────────────────────────────────────────────────────

    def record_tx(
        self,
        sim: "Sim",
        amount: float,
        tx_type: str,
        tick: int = 0,
        counterpart: str = "",
        description: str = "",
        metadata: dict | None = None,
        allow_overdraft: bool = False,
    ) -> "LedgerEntry":
        """
        Atomically record a financial transaction and update sim.simoleons.

        Steps (all inside one IMMEDIATE transaction):
          1. Compute balance_before, balance_after.
          2. Validate: no overdraft below OVERDRAFT_FLOOR (unless allow_overdraft).
          3. INSERT into ledger_entries.
          4. Mutate sim.simoleons = balance_after.
          5. COMMIT.

        If any step raises, the transaction is rolled back and sim.simoleons
        is left unchanged.  The caller receives the exception.

        Returns the committed LedgerEntry.
        """
        amount = float(amount)
        if amount == 0.0:
            # Zero-amount transactions are a bug — skip silently
            logger.debug("[Ledger] Skipping zero-amount tx (%s) for %s", tx_type, sim.sim_id[:8])
            raise ValueError("Zero-amount transactions are not recorded in the ledger")

        meta_json = json.dumps(metadata or {}, default=str)
        cat       = _category(tx_type)
        tx_id     = uuid.uuid4().hex
        wall_ts   = time.time()

        with self._lock:
            balance_before = float(sim.simoleons)
            balance_after  = balance_before + amount

            # ── Consistency check ─────────────────────────────────────────────
            # Expenses (amount < 0) must not take balance below zero unless
            # allow_overdraft=True (e.g. inheritance distribution, system corrections).
            # Income (amount > 0) is always allowed.
            if amount < 0 and not allow_overdraft and balance_after < 0.0:
                raise InsufficientFundsError(
                    f"{sim.name} cannot afford {abs(amount):.2f} "
                    f"(balance={balance_before:.2f})"
                )
            # Hard floor even with allow_overdraft — prevents catastrophic debt
            if balance_after < OVERDRAFT_FLOOR:
                raise InsufficientFundsError(
                    f"{sim.name} overdraft floor ({OVERDRAFT_FLOOR:.0f}) breached"
                )

            # ── Anomaly detection ─────────────────────────────────────────────
            ceil = ANOMALY_CEILINGS.get(tx_type)
            flagged = (
                abs(amount) > GLOBAL_FLAG_CEILING
                or (ceil is not None and abs(amount) > ceil)
            )
            if flagged:
                logger.warning(
                    "[Ledger] FLAGGED: %s %s %+.2f (bal_before=%.2f) %s",
                    sim.name, tx_type, amount, balance_before, description[:60],
                )

            # ── ACID write ────────────────────────────────────────────────────
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """INSERT INTO ledger_entries
                       (tx_id, tick, wall_ts, sim_id, counterpart, tx_type, category,
                        amount, balance_before, balance_after, description, metadata, flagged)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tx_id, tick, wall_ts, sim.sim_id, counterpart, tx_type, cat,
                     amount, balance_before, balance_after, description, meta_json,
                     int(flagged)),
                )
                # Mutate in-memory state AFTER successful INSERT
                sim.simoleons = balance_after
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise

        entry = LedgerEntry(
            tx_id=tx_id, tick=tick, wall_ts=wall_ts,
            sim_id=sim.sim_id, counterpart=counterpart,
            tx_type=tx_type, category=cat, amount=amount,
            balance_before=balance_before, balance_after=balance_after,
            description=description, metadata=metadata or {}, flagged=flagged,
        )
        logger.debug(
            "[Ledger] %s %s %+.2f → %.2f  [%s]",
            sim.name[:12], tx_type, amount, balance_after, tx_id[:8],
        )
        return entry

    # ── Balance snapshot ──────────────────────────────────────────────────────

    def snapshot_balance(self, sim_id: str, tick: int, balance: float) -> None:
        """Write a balance checkpoint (call every SNAPSHOT_INTERVAL ticks)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT OR REPLACE INTO balance_snapshots VALUES (?,?,?)",
                (sim_id, tick, balance),
            )
            self._conn.execute("COMMIT")

    # ── Queries ───────────────────────────────────────────────────────────────

    def history(
        self,
        sim_id: str,
        limit: int = 50,
        tx_type: str | None = None,
        since_tick: int = 0,
    ) -> list[LedgerEntry]:
        sql    = "SELECT * FROM ledger_entries WHERE sim_id=?"
        params: list = [sim_id]
        if tx_type:
            sql += " AND tx_type=?"
            params.append(tx_type)
        if since_tick:
            sql += " AND tick>=?"
            params.append(since_tick)
        sql += " ORDER BY tick DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_entry(r) for r in self._conn.execute(sql, params).fetchall()]

    def income_breakdown(self, sim_id: str, since_tick: int = 0) -> dict[str, float]:
        """Total income grouped by tx_type since a given tick."""
        rows = self._conn.execute(
            """SELECT tx_type, SUM(amount)
               FROM ledger_entries
               WHERE sim_id=? AND category='income' AND tick>=?
               GROUP BY tx_type ORDER BY SUM(amount) DESC""",
            (sim_id, since_tick),
        ).fetchall()
        return {r[0]: round(r[1], 2) for r in rows}

    def expense_breakdown(self, sim_id: str, since_tick: int = 0) -> dict[str, float]:
        rows = self._conn.execute(
            """SELECT tx_type, SUM(ABS(amount))
               FROM ledger_entries
               WHERE sim_id=? AND category='expense' AND tick>=?
               GROUP BY tx_type ORDER BY SUM(ABS(amount)) DESC""",
            (sim_id, since_tick),
        ).fetchall()
        return {r[0]: round(r[1], 2) for r in rows}

    def balance_at_tick(self, sim_id: str, tick: int) -> float | None:
        """Reconstruct balance at a given tick using nearest snapshot + replay."""
        snap = self._conn.execute(
            "SELECT tick, balance FROM balance_snapshots WHERE sim_id=? AND tick<=? "
            "ORDER BY tick DESC LIMIT 1",
            (sim_id, tick),
        ).fetchone()

        if snap is None:
            # Replay from start
            base_tick, base_bal = 0, 0.0
        else:
            base_tick, base_bal = snap

        delta = self._conn.execute(
            "SELECT SUM(amount) FROM ledger_entries "
            "WHERE sim_id=? AND tick>? AND tick<=?",
            (sim_id, base_tick, tick),
        ).fetchone()[0] or 0.0

        return round(base_bal + delta, 4)

    def net_worth_history(self, sim_id: str, tick_step: int = 10) -> list[dict]:
        """Balance at every tick_step interval — for wealth graph."""
        rows = self._conn.execute(
            "SELECT tick, balance FROM balance_snapshots WHERE sim_id=? ORDER BY tick",
            (sim_id,),
        ).fetchall()
        return [{"tick": r[0], "balance": r[1]} for r in rows]

    def top_earners(self, since_tick: int = 0, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            """SELECT sim_id, SUM(amount) as total
               FROM ledger_entries
               WHERE category='income' AND tick>=?
               GROUP BY sim_id ORDER BY total DESC LIMIT ?""",
            (since_tick, limit),
        ).fetchall()
        return [{"sim_id": r[0], "total_income": round(r[1], 2)} for r in rows]

    def anomalies(self, since_tick: int = 0, limit: int = 100) -> list[LedgerEntry]:
        rows = self._conn.execute(
            "SELECT * FROM ledger_entries WHERE flagged=1 AND tick>=? "
            "ORDER BY ABS(amount) DESC LIMIT ?",
            (since_tick, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def wealth_velocity(self, sim_id: str, window: int = 20) -> float:
        """Average simoleons/tick over the last `window` ticks with entries."""
        rows = self._conn.execute(
            "SELECT tick, SUM(amount) FROM ledger_entries "
            "WHERE sim_id=? GROUP BY tick ORDER BY tick DESC LIMIT ?",
            (sim_id, window),
        ).fetchall()
        if len(rows) < 2:
            return 0.0
        total = sum(r[1] for r in rows)
        span  = rows[0][0] - rows[-1][0]
        return round(total / max(1, span), 4)

    def summary(self, since_tick: int = 0) -> dict:
        total_tx = self._conn.execute(
            "SELECT COUNT(*) FROM ledger_entries WHERE tick>=?", (since_tick,)
        ).fetchone()[0]
        flagged = self._conn.execute(
            "SELECT COUNT(*) FROM ledger_entries WHERE flagged=1 AND tick>=?",
            (since_tick,)
        ).fetchone()[0]
        volume = self._conn.execute(
            "SELECT SUM(ABS(amount)) FROM ledger_entries WHERE tick>=?", (since_tick,)
        ).fetchone()[0] or 0.0
        return {
            "total_transactions": total_tx,
            "flagged":            flagged,
            "total_volume":       round(volume, 2),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_entry(row: tuple) -> LedgerEntry:
        return LedgerEntry(
            tx_id=row[0], tick=row[1], wall_ts=row[2],
            sim_id=row[3], counterpart=row[4], tx_type=row[5], category=row[6],
            amount=row[7], balance_before=row[8], balance_after=row[9],
            description=row[10],
            metadata=json.loads(row[11]) if row[11] else {},
            flagged=bool(row[12]),
        )

    def close(self) -> None:
        self._conn.close()
