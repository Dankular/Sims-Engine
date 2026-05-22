"""
blockchain/contracts/sim_agreement.py — Programmable sim-to-sim contracts.

Types:
  loan        — principal disbursed on-chain; installment repayment with interest;
                default clause triggers drama event + reputation hit
  employment  — periodic salary transfer party_a→party_b; termination conditions
  partnership — revenue share on shop transactions; buy-out trigger
  rental      — weekly rent + deposit escrow; eviction after N missed payments

All clauses execute automatically in tick_agreements() — no manual intervention.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from blockchain.contracts.base import SmartContract

if TYPE_CHECKING:
    from blockchain.chain import SimChain
    from blockchain.transaction import SimTransaction

logger = logging.getLogger(__name__)

SIM_WEI = 10 ** 18


class AgreementType(str, Enum):
    LOAN        = "loan"
    EMPLOYMENT  = "employment"
    PARTNERSHIP = "partnership"
    RENTAL      = "rental"


class AgreementStatus(str, Enum):
    PENDING   = "pending"
    ACTIVE    = "active"
    FULFILLED = "fulfilled"
    BREACHED  = "breached"
    EXPIRED   = "expired"


@dataclass
class SimAgreement:
    agreement_id:   str
    agreement_type: AgreementType
    party_a:        str             # lender / employer / landlord (0x address)
    party_b:        str             # borrower / employee / tenant (0x address)
    amount_wei:     int             # principal / salary / rent per period
    duration_ticks: int             # 0 = indefinite
    terms:          dict            # interest_rate, period_ticks, max_breaches, …
    created_tick:   int
    agreement_hash: str
    status:         AgreementStatus = AgreementStatus.PENDING
    ticks_elapsed:  int             = 0
    breach_count:   int             = 0
    created_at:     float           = field(default_factory=time.time)

    @property
    def amount_sim(self) -> float:
        return self.amount_wei / SIM_WEI


class AgreementEngine(SmartContract):
    contract_id = "sim_agreement"

    def __init__(self) -> None:
        self._agreements: dict[str, SimAgreement]  = {}
        self._by_party:   dict[str, list[str]]     = {}  # 0x addr → agreement IDs

    # ── Creation ──────────────────────────────────────────────────────────────

    def create(
        self,
        agreement_type: AgreementType,
        party_a: str,
        party_b: str,
        amount_wei: int,
        duration_ticks: int,
        terms: dict,
        current_tick: int,
        chain: "SimChain",
    ) -> SimAgreement:
        aid = uuid.uuid4().hex[:12]
        content = json.dumps(
            {"type": str(agreement_type), "a": party_a, "b": party_b,
             "amount": amount_wei, "terms": terms, "tick": current_tick},
            sort_keys=True,
        )
        h = hashlib.sha256(content.encode()).hexdigest()

        ag = SimAgreement(
            agreement_id=aid,
            agreement_type=agreement_type,
            party_a=party_a,
            party_b=party_b,
            amount_wei=amount_wei,
            duration_ticks=duration_ticks,
            terms=terms,
            created_tick=current_tick,
            agreement_hash=h,
        )

        # Type-specific activation logic
        if agreement_type == AgreementType.LOAN:
            if chain.balance_of(party_a) >= amount_wei:
                # Disburse to borrower immediately
                chain._transfer(party_a, party_b, amount_wei)
                ag.status = AgreementStatus.ACTIVE
            else:
                ag.status = AgreementStatus.EXPIRED
        elif agreement_type == AgreementType.RENTAL:
            deposit = terms.get("deposit_wei", amount_wei)
            if chain.balance_of(party_b) >= deposit:
                chain._transfer(party_b, f"escrow:{aid}", deposit)
                ag.status = AgreementStatus.ACTIVE
            else:
                ag.status = AgreementStatus.EXPIRED
        else:
            ag.status = AgreementStatus.ACTIVE

        self._agreements[aid] = ag
        for addr in (party_a, party_b):
            self._by_party.setdefault(addr, []).append(aid)

        logger.info(
            "[Agreement] %s #%s | %s ↔ %s | %.2f $SIM",
            agreement_type, aid[:6], party_a[:10], party_b[:10], ag.amount_sim,
        )
        return ag

    # ── Periodic execution ────────────────────────────────────────────────────

    def tick_agreements(self, current_tick: int, chain: "SimChain") -> list[dict]:
        """
        Process all active agreements.  Returns a list of events for the
        engine to emit (agreement_breach, loan_default, agreement_fulfilled).
        """
        events: list[dict] = []

        for aid, ag in list(self._agreements.items()):
            if ag.status != AgreementStatus.ACTIVE:
                continue
            ag.ticks_elapsed += 1

            # ── Employment: salary per period ─────────────────────────────
            if ag.agreement_type == AgreementType.EMPLOYMENT:
                period = ag.terms.get("period_ticks", 5)
                if ag.ticks_elapsed % period == 0:
                    ok = chain._transfer(ag.party_a, ag.party_b, ag.amount_wei)
                    if not ok:
                        ag.breach_count += 1
                        events.append({
                            "type": "agreement_breach",
                            "agreement_id": aid,
                            "breacher": ag.party_a,
                            "reason": "salary_not_paid",
                        })
                        if ag.breach_count >= ag.terms.get("max_breaches", 2):
                            ag.status = AgreementStatus.BREACHED
                            events.append({
                                "type": "agreement_terminated",
                                "agreement_id": aid,
                                "reason": "too_many_breaches",
                            })

            # ── Rental: rent per period ───────────────────────────────────
            elif ag.agreement_type == AgreementType.RENTAL:
                period = ag.terms.get("period_ticks", 10)
                if ag.ticks_elapsed % period == 0:
                    ok = chain._transfer(ag.party_b, ag.party_a, ag.amount_wei)
                    if not ok:
                        ag.breach_count += 1
                        events.append({
                            "type": "rent_missed",
                            "agreement_id": aid,
                            "tenant": ag.party_b,
                        })
                        if ag.breach_count >= ag.terms.get("max_breaches", 2):
                            # Return deposit to landlord on eviction
                            chain._transfer(f"escrow:{aid}", ag.party_a,
                                            chain.balance_of(f"escrow:{aid}"))
                            ag.status = AgreementStatus.BREACHED
                            events.append({
                                "type": "eviction",
                                "agreement_id": aid,
                                "tenant": ag.party_b,
                            })

            # ── Loan: installment repayment ───────────────────────────────
            elif ag.agreement_type == AgreementType.LOAN:
                repay_period = ag.terms.get("repay_period_ticks", 10)
                if ag.ticks_elapsed % repay_period == 0:
                    rate = ag.terms.get("interest_rate", 0.05)
                    total_due = int(ag.amount_wei * (1 + rate))
                    periods = max(1, ag.duration_ticks // repay_period)
                    installment = total_due // periods
                    ok = chain._transfer(ag.party_b, ag.party_a, installment)
                    if not ok:
                        ag.breach_count += 1
                        events.append({
                            "type": "loan_default",
                            "agreement_id": aid,
                            "debtor": ag.party_b,
                            "breach_count": ag.breach_count,
                        })

            # ── Expiry ────────────────────────────────────────────────────
            if ag.duration_ticks > 0 and ag.ticks_elapsed >= ag.duration_ticks:
                ag.status = AgreementStatus.FULFILLED
                events.append({
                    "type": "agreement_fulfilled",
                    "agreement_id": aid,
                    "parties": [ag.party_a, ag.party_b],
                    "agreement_type": str(ag.agreement_type),
                })

        return events

    # ── On-chain handler ──────────────────────────────────────────────────────

    def on_agreement(self, tx: "SimTransaction", chain: "SimChain") -> None:
        d = tx.data
        self.create(
            agreement_type=AgreementType(d.get("agreement_type", "loan")),
            party_a=tx.from_addr,
            party_b=tx.to_addr,
            amount_wei=tx.amount,
            duration_ticks=d.get("duration_ticks", 50),
            terms=d.get("terms", {}),
            current_tick=d.get("tick", 0),
            chain=chain,
        )

    # ── Queries ───────────────────────────────────────────────────────────────

    def agreements_for(self, address: str) -> list[SimAgreement]:
        ids = self._by_party.get(address, [])
        return [self._agreements[i] for i in ids if i in self._agreements]

    def get(self, aid: str) -> SimAgreement | None:
        return self._agreements.get(aid)

    def active_count(self) -> int:
        return sum(1 for a in self._agreements.values()
                   if a.status == AgreementStatus.ACTIVE)

    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        for a in self._agreements.values():
            by_type[str(a.agreement_type)] = by_type.get(str(a.agreement_type), 0) + 1
        return {
            "total":       len(self._agreements),
            "active":      self.active_count(),
            "by_type":     by_type,
        }
