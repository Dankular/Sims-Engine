"""
world/bank.py — City Bank: term deposits, checking accounts, interest.

Every sim gets a BankAccount automatically.  Depositing transfers simoleons
from the sim's wallet into the bank (ACID through the ledger) and returns a
TermDeposit with a real Unix timestamp maturity date.  The deposit is locked —
the money cannot be withdrawn until the agreed term has elapsed.

Terms (configured in config.BANK_TERMS):
  1 week   → 1.5% APR
  2 weeks  → 2.5% APR
  1 month  → 4.0% APR
  3 months → 6.0% APR
  1 year   → 10.0% APR

Interest formula (simple interest):
  matured_amount = principal * (1 + apr * (term_seconds / 86400 / 365))

ACID guarantees:
  - Opening a deposit: ledger._tx(sim, -principal, TX_BANK_DEPOSIT) inside a
    SQLite IMMEDIATE transaction that also inserts the deposit row.  If either
    fails the whole operation rolls back.
  - Claiming a matured deposit: ledger._tx(sim, +matured_amount, TX_BANK_INTEREST)
    inside a transaction that marks the deposit withdrawn.

Persistence: `sim_bank.db` (WAL + synchronous=FULL, separate from ledger).
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

from config import BANK_TERMS, BANK_MIN_DEPOSIT, BANK_RESERVE_RATIO

logger = logging.getLogger(__name__)

# Ledger transaction types for bank operations (registered in persistence/ledger.py)
TX_BANK_DEPOSIT  = "bank_deposit"    # money leaves sim → bank (expense)
TX_BANK_INTEREST = "bank_interest"   # principal + interest returns (income)
TX_BANK_TRANSFER = "bank_transfer"   # checking account transfer

_YEAR_SECONDS = 365 * 86400


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class TermDeposit:
    deposit_id:    str
    sim_id:        str
    term_key:      str        # "1_week", "2_weeks", "1_month", "3_months", "1_year"
    principal:     float
    apr:           float
    created_at:    float      # Unix timestamp
    matures_at:    float      # Unix timestamp — locked until this
    matured_amount: float
    status:        str        # "active" | "matured" | "withdrawn"
    withdrawn_at:  float      # 0 until withdrawn

    @property
    def term_seconds(self) -> float:
        return self.matures_at - self.created_at

    @property
    def is_mature(self) -> bool:
        return time.time() >= self.matures_at and self.status == "matured"

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.matures_at - time.time())

    @property
    def days_remaining(self) -> float:
        return self.seconds_remaining / 86400

    @property
    def interest_earned(self) -> float:
        return self.matured_amount - self.principal

    def to_dict(self) -> dict:
        now = time.time()
        return {
            "deposit_id":    self.deposit_id,
            "sim_id":        self.sim_id,
            "term":          BANK_TERMS.get(self.term_key, {}).get("label", self.term_key),
            "term_key":      self.term_key,
            "principal":     round(self.principal, 2),
            "apr_pct":       round(self.apr * 100, 2),
            "matured_amount": round(self.matured_amount, 2),
            "interest":      round(self.interest_earned, 2),
            "created_at":    self.created_at,
            "matures_at":    self.matures_at,
            "seconds_remaining": round(max(0.0, self.matures_at - now), 0),
            "days_remaining": round(max(0.0, (self.matures_at - now) / 86400), 2),
            "status":        self.status,
            "withdrawn_at":  self.withdrawn_at,
        }


@dataclass
class BankAccount:
    sim_id:           str
    checking_balance: float = 0.0    # liquid, no interest
    created_at:       float = 0.0

    def to_dict(self, deposits: list[TermDeposit]) -> dict:
        locked = sum(d.principal for d in deposits if d.status == "active")
        accrued = sum(d.matured_amount for d in deposits if d.status == "matured")
        return {
            "sim_id":           self.sim_id,
            "checking_balance": round(self.checking_balance, 2),
            "locked_in_deposits": round(locked, 2),
            "matured_ready":    round(accrued, 2),
            "total_bank_value": round(self.checking_balance + locked, 2),
            "deposits":         [d.to_dict() for d in deposits],
        }


# ── CityBank ──────────────────────────────────────────────────────────────────

class CityBank:
    """
    The city's central bank.  One instance per server.

    Deposit flow:
      1. Player calls POST /bank/deposit {sim_id, term_key, amount}
      2. bank.open_deposit(sim, term_key, amount, engine) validates + writes ACID
      3. sim.simoleons decreases, deposit row inserted in same transaction
      4. On maturity: bank.check_maturities(engine) marks deposits "matured"
      5. Player calls POST /bank/withdraw {deposit_id}
      6. bank.withdraw(sim, deposit_id, engine) credits principal + interest ACID

    The bank is the only entity that may grant overdraft credit backed by
    collateral — all other overdrafts are rejected.
    """

    def __init__(self, db_path: str = "sim_bank.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()
        self._matured_notified: set[str] = set()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                sim_id           TEXT PRIMARY KEY,
                checking_balance REAL NOT NULL DEFAULT 0.0,
                created_at       REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deposits (
                deposit_id    TEXT PRIMARY KEY,
                sim_id        TEXT NOT NULL,
                term_key      TEXT NOT NULL,
                principal     REAL NOT NULL,
                apr           REAL NOT NULL,
                created_at    REAL NOT NULL,
                matures_at    REAL NOT NULL,
                matured_amount REAL NOT NULL,
                status        TEXT NOT NULL DEFAULT 'active',
                withdrawn_at  REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_dep_sim  ON deposits (sim_id);
            CREATE INDEX IF NOT EXISTS idx_dep_matures ON deposits (matures_at);
            CREATE INDEX IF NOT EXISTS idx_dep_status  ON deposits (status);
        """)

    # ── Account management ────────────────────────────────────────────────────

    def ensure_account(self, sim_id: str) -> BankAccount:
        """Create account if it doesn't exist. Idempotent."""
        with self._lock:
            row = self._conn.execute(
                "SELECT sim_id, checking_balance, created_at FROM accounts WHERE sim_id=?",
                (sim_id,),
            ).fetchone()
            if row:
                return BankAccount(sim_id=row[0], checking_balance=row[1], created_at=row[2])
            now = time.time()
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO accounts (sim_id, checking_balance, created_at) VALUES (?,?,?)",
                (sim_id, 0.0, now),
            )
            self._conn.execute("COMMIT")
            return BankAccount(sim_id=sim_id, checking_balance=0.0, created_at=now)

    def get_account(self, sim_id: str) -> BankAccount | None:
        row = self._conn.execute(
            "SELECT sim_id, checking_balance, created_at FROM accounts WHERE sim_id=?",
            (sim_id,),
        ).fetchone()
        return BankAccount(*row) if row else None

    def full_account(self, sim_id: str) -> dict:
        acct = self.get_account(sim_id)
        if not acct:
            acct = self.ensure_account(sim_id)
        deps = self.deposits_for(sim_id)
        return acct.to_dict(deps)

    # ── Deposits ──────────────────────────────────────────────────────────────

    def open_deposit(
        self,
        sim: "Sim",
        term_key: str,
        amount: float,
        engine: "SimEngine",
    ) -> TermDeposit:
        """
        Open a term deposit. Atomically deducts from sim.simoleons and
        inserts the deposit row. Raises ValueError on bad input.
        """
        term_def = BANK_TERMS.get(term_key)
        if term_def is None:
            raise ValueError(
                f"Unknown term '{term_key}'. Valid: {list(BANK_TERMS.keys())}"
            )
        if amount < BANK_MIN_DEPOSIT:
            raise ValueError(
                f"Minimum deposit is §{BANK_MIN_DEPOSIT:.2f} (got §{amount:.2f})"
            )
        if amount > sim.simoleons:
            raise ValueError(
                f"{sim.name} has §{sim.simoleons:.2f} but wants to deposit §{amount:.2f}"
            )

        self.ensure_account(sim.sim_id)

        apr           = float(term_def["apr"])
        term_seconds  = float(term_def["seconds"])
        now           = time.time()
        matures_at    = now + term_seconds
        interest      = amount * apr * (term_seconds / _YEAR_SECONDS)
        matured_amount = round(amount + interest, 4)
        deposit_id    = uuid.uuid4().hex

        # ACID: deduct simoleons (ledger) + insert deposit row in same transaction
        with self._lock:
            # Step 1: ledger deduction (raises on failure → abort before DB write)
            from persistence.ledger import TX_BANK_DEPOSIT
            engine._tx(
                sim, -amount, TX_BANK_DEPOSIT,
                counterpart="city_bank",
                description=f"{term_def['label']} deposit @ {apr*100:.1f}% APR",
                metadata={"deposit_id": deposit_id, "term_key": term_key, "matures_at": matures_at},
            )
            # Step 2: insert deposit row atomically
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """INSERT INTO deposits
                       (deposit_id, sim_id, term_key, principal, apr,
                        created_at, matures_at, matured_amount, status, withdrawn_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (deposit_id, sim.sim_id, term_key, amount, apr,
                     now, matures_at, matured_amount, "active", 0.0),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                # Reverse the ledger deduction via a correction entry
                from persistence.ledger import TX_CORRECTION
                try:
                    engine._tx(
                        sim, amount, TX_CORRECTION,
                        description=f"bank deposit rollback: {deposit_id[:8]}",
                    )
                except Exception:
                    pass
                raise

        dep = TermDeposit(
            deposit_id=deposit_id,
            sim_id=sim.sim_id,
            term_key=term_key,
            principal=amount,
            apr=apr,
            created_at=now,
            matures_at=matures_at,
            matured_amount=matured_amount,
            status="active",
            withdrawn_at=0.0,
        )
        logger.info(
            "[Bank] %s opened §%.2f %s deposit → matures in %.1f days (§%.2f back)",
            sim.name, amount, term_def["label"],
            term_seconds / 86400, matured_amount,
        )
        return dep

    def withdraw(
        self,
        sim: "Sim",
        deposit_id: str,
        engine: "SimEngine",
    ) -> TermDeposit:
        """
        Withdraw a matured deposit. Credits principal + interest to sim.
        Raises ValueError if deposit is not mature, already withdrawn, or wrong sim.
        """
        dep = self._get_deposit(deposit_id)
        if dep is None:
            raise ValueError(f"Deposit {deposit_id[:8]} not found")
        if dep.sim_id != sim.sim_id:
            raise ValueError("This deposit does not belong to your account")
        if dep.status == "withdrawn":
            raise ValueError("Deposit already withdrawn")
        if dep.status == "active":
            if time.time() < dep.matures_at:
                days_left = (dep.matures_at - time.time()) / 86400
                raise ValueError(
                    f"Deposit matures in {days_left:.1f} days — cannot withdraw early"
                )
            # Should have been marked matured by check_maturities, do it now
            dep.status = "matured"

        with self._lock:
            from persistence.ledger import TX_BANK_INTEREST
            engine._tx(
                sim, dep.matured_amount, TX_BANK_INTEREST,
                counterpart="city_bank",
                description=(
                    f"Deposit matured: §{dep.principal:.2f} + "
                    f"§{dep.interest_earned:.2f} interest ({dep.apr*100:.1f}% APR)"
                ),
                metadata={"deposit_id": deposit_id, "term_key": dep.term_key},
            )
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "UPDATE deposits SET status='withdrawn', withdrawn_at=? WHERE deposit_id=?",
                    (time.time(), deposit_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                # Reverse the interest credit
                from persistence.ledger import TX_CORRECTION
                try:
                    engine._tx(
                        sim, -dep.matured_amount, TX_CORRECTION,
                        description=f"bank withdrawal rollback: {deposit_id[:8]}",
                    )
                except Exception:
                    pass
                raise

        dep.status = "withdrawn"
        dep.withdrawn_at = time.time()
        logger.info(
            "[Bank] %s withdrew §%.2f (§%.2f interest, %s)",
            sim.name, dep.matured_amount, dep.interest_earned, dep.term_key,
        )
        return dep

    # ── Maturity checking (called each heartbeat) ─────────────────────────────

    def check_maturities(self, engine: "SimEngine") -> list[str]:
        """
        Mark deposits whose maturity timestamp has passed.
        Called by the heartbeat loop. Returns list of newly matured deposit IDs.
        """
        now = time.time()
        newly_matured: list[str] = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT deposit_id, sim_id FROM deposits "
                "WHERE status='active' AND matures_at <= ?",
                (now,),
            ).fetchall()
            if rows:
                ids = [r[0] for r in rows]
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.executemany(
                    "UPDATE deposits SET status='matured' WHERE deposit_id=?",
                    [(did,) for did in ids],
                )
                self._conn.execute("COMMIT")
                for deposit_id, sim_id in rows:
                    if deposit_id not in self._matured_notified:
                        self._matured_notified.add(deposit_id)
                        newly_matured.append(deposit_id)
                        engine._bus.emit(
                            "bank_deposit_matured",
                            deposit_id=deposit_id,
                            sim_id=sim_id,
                        )
                        logger.info("[Bank] Deposit %s matured for sim %s",
                                    deposit_id[:8], sim_id[:8])
        return newly_matured

    # ── Checking account transfers ────────────────────────────────────────────

    def deposit_to_checking(
        self, sim: "Sim", amount: float, engine: "SimEngine"
    ) -> float:
        """Move simoleons from sim wallet into checking (liquid, no interest)."""
        if amount <= 0 or amount > sim.simoleons:
            raise ValueError(f"Invalid transfer amount §{amount:.2f}")
        self.ensure_account(sim.sim_id)
        from persistence.ledger import TX_BANK_TRANSFER
        engine._tx(
            sim, -amount, TX_BANK_TRANSFER,
            counterpart="city_bank",
            description="transfer to checking account",
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "UPDATE accounts SET checking_balance = checking_balance + ? WHERE sim_id=?",
                (amount, sim.sim_id),
            )
            self._conn.execute("COMMIT")
        return self.get_account(sim.sim_id).checking_balance

    def withdraw_from_checking(
        self, sim: "Sim", amount: float, engine: "SimEngine"
    ) -> float:
        """Move simoleons from checking back to sim wallet."""
        acct = self.get_account(sim.sim_id)
        if not acct or acct.checking_balance < amount:
            raise ValueError(
                f"Insufficient checking balance "
                f"(have §{acct.checking_balance if acct else 0:.2f})"
            )
        from persistence.ledger import TX_BANK_TRANSFER
        engine._tx(
            sim, amount, TX_BANK_TRANSFER,
            counterpart="city_bank",
            description="withdraw from checking account",
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "UPDATE accounts SET checking_balance = checking_balance - ? WHERE sim_id=?",
                (amount, sim.sim_id),
            )
            self._conn.execute("COMMIT")
        return self.get_account(sim.sim_id).checking_balance

    # ── Queries ───────────────────────────────────────────────────────────────

    def deposits_for(self, sim_id: str, status: str | None = None) -> list[TermDeposit]:
        sql = "SELECT * FROM deposits WHERE sim_id=?"
        params: list = [sim_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        return [self._row_to_deposit(r) for r in
                self._conn.execute(sql, params).fetchall()]

    def matured_ready(self, sim_id: str) -> list[TermDeposit]:
        """Deposits that have matured and are ready to withdraw."""
        return self.deposits_for(sim_id, status="matured")

    def active_deposits(self, sim_id: str) -> list[TermDeposit]:
        return self.deposits_for(sim_id, status="active")

    def total_locked(self, sim_id: str) -> float:
        """Total simoleons locked in active deposits — used for collateral valuation."""
        row = self._conn.execute(
            "SELECT SUM(principal) FROM deposits WHERE sim_id=? AND status='active'",
            (sim_id,),
        ).fetchone()
        return float(row[0] or 0.0)

    def stats(self) -> dict:
        total_accounts = self._conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        total_active   = self._conn.execute(
            "SELECT COUNT(*), SUM(principal) FROM deposits WHERE status='active'"
        ).fetchone()
        return {
            "total_accounts":     total_accounts,
            "active_deposits":    total_active[0] or 0,
            "total_locked_sim":   round(float(total_active[1] or 0), 2),
            "available_terms":    {k: {"apr_pct": v["apr"]*100, "days": v["seconds"]//86400}
                                   for k, v in BANK_TERMS.items()},
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_deposit(self, deposit_id: str) -> TermDeposit | None:
        row = self._conn.execute(
            "SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)
        ).fetchone()
        return self._row_to_deposit(row) if row else None

    @staticmethod
    def _row_to_deposit(row: tuple) -> TermDeposit:
        return TermDeposit(
            deposit_id=row[0], sim_id=row[1], term_key=row[2],
            principal=row[3], apr=row[4], created_at=row[5],
            matures_at=row[6], matured_amount=row[7],
            status=row[8], withdrawn_at=row[9],
        )
