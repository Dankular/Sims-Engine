"""
core/negotiation.py — Multi-agent bargaining protocol.

Covers: job offers, loans, partnerships, marriage proposals, custody
arrangements, property sales. Each session runs offer→counter→accept/reject
with trust pricing and betrayal risk assessment.

On acceptance: creates a binding SimAgreement on-chain via web3 bridge.
Breach of a negotiated agreement triggers drama + reputation hit.

Engine integration:
  engine.negotiation = NegotiationEngine()
  engine.negotiation.tick(engine)          ← periodic session expiry
  engine.negotiate(sim_a_id, sim_b_id, item_type, initial_terms) ← start
  engine._apply_resolved() can trigger counter-offers from resolved interactions
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sim import Sim
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)


class NegotiationItem(str, Enum):
    JOB_OFFER        = "job_offer"
    LOAN             = "loan"
    PARTNERSHIP      = "partnership"
    MARRIAGE_PROPOSAL= "marriage_proposal"
    CUSTODY_PLAN     = "custody_plan"
    PROPERTY_SALE    = "property_sale"
    DEBT_SETTLEMENT  = "debt_settlement"
    TRUCE            = "truce"


class SessionStatus(str, Enum):
    OPEN      = "open"
    COUNTERED = "countered"
    ACCEPTED  = "accepted"
    REJECTED  = "rejected"
    EXPIRED   = "expired"
    BETRAYED  = "betrayed"


@dataclass
class Offer:
    proposer_id: str
    terms:       dict          # item-specific: {salary, duration, share, price, …}
    timestamp:   float = field(default_factory=time.time)
    accepted:    bool = False
    rejected:    bool = False


@dataclass
class NegotiationSession:
    session_id:  str
    item_type:   NegotiationItem
    party_a_id:  str            # initiator
    party_b_id:  str            # responder
    offers:      list[Offer] = field(default_factory=list)
    status:      SessionStatus = SessionStatus.OPEN
    created_tick: int = 0
    expire_ticks: int = 20      # sessions expire if no response
    ticks_open:  int = 0
    # Trust pricing
    trust_score: float = 0.5    # 0=no trust, 1=full trust
    betrayal_risk: float = 0.1  # probability one party defects post-agreement

    def latest_offer(self) -> Offer | None:
        return self.offers[-1] if self.offers else None

    def round_count(self) -> int:
        return len(self.offers)

    def is_live(self) -> bool:
        return self.status in (SessionStatus.OPEN, SessionStatus.COUNTERED)


class NegotiationEngine:
    """
    Manages all active negotiation sessions.
    Applies trust pricing, generates counter-offers, enforces acceptance.
    """

    MAX_ROUNDS = 5   # after this → auto-reject (deadlock)

    def __init__(self) -> None:
        self._sessions: dict[str, NegotiationSession] = {}

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def open_session(
        self,
        party_a: "Sim", party_b: "Sim",
        item_type: NegotiationItem,
        initial_terms: dict,
        tick: int = 0,
    ) -> NegotiationSession:
        sid = uuid.uuid4().hex[:10]

        # Trust pricing: based on relationship + reputation
        trust = self._compute_trust(party_a, party_b)
        # Betrayal risk: higher when trust is low or reputation is bad
        betrayal_risk = max(0.02, (1 - trust) * 0.25 +
                            max(0.0, -party_b.reputation_score / 200))

        session = NegotiationSession(
            session_id=sid,
            item_type=item_type,
            party_a_id=party_a.sim_id,
            party_b_id=party_b.sim_id,
            created_tick=tick,
            trust_score=trust,
            betrayal_risk=round(betrayal_risk, 3),
        )
        first_offer = Offer(proposer_id=party_a.sim_id, terms=dict(initial_terms))
        session.offers.append(first_offer)
        self._sessions[sid] = session

        logger.info(
            "[Negotiation] %s opened: %s ↔ %s | %s | trust=%.2f",
            sid[:6], party_a.name, party_b.name, item_type, trust,
        )
        return session

    def counter(
        self,
        session_id: str, proposer_id: str, new_terms: dict,
    ) -> bool:
        session = self._sessions.get(session_id)
        if not session or not session.is_live():
            return False
        if session.round_count() >= self.MAX_ROUNDS:
            session.status = SessionStatus.REJECTED
            return False
        session.offers.append(Offer(proposer_id=proposer_id, terms=dict(new_terms)))
        session.status = SessionStatus.COUNTERED
        return True

    def accept(
        self, session_id: str, acceptor_id: str, engine: "SimEngine"
    ) -> dict:
        session = self._sessions.get(session_id)
        if not session or not session.is_live():
            return {"ok": False, "reason": "session_not_found_or_closed"}

        offer = session.latest_offer()
        if not offer:
            return {"ok": False, "reason": "no_offer"}
        if offer.proposer_id == acceptor_id:
            return {"ok": False, "reason": "cannot_accept_own_offer"}

        session.status = SessionStatus.ACCEPTED
        offer.accepted = True

        # Materialise the agreement
        result = self._materialise(session, offer.terms, engine)

        logger.info(
            "[Negotiation] %s ACCEPTED by %s | %s",
            session_id[:6], acceptor_id[:8], session.item_type,
        )
        engine._bus.emit(
            "negotiation_accepted",
            session_id=session_id, item_type=session.item_type.value,
            party_a=session.party_a_id, party_b=session.party_b_id,
            terms=offer.terms,
        )
        return {"ok": True, "session_id": session_id, **result}

    def reject(self, session_id: str, rejecter_id: str, engine: "SimEngine") -> bool:
        session = self._sessions.get(session_id)
        if not session or not session.is_live():
            return False
        session.status = SessionStatus.REJECTED
        engine._bus.emit(
            "negotiation_rejected",
            session_id=session_id, item_type=session.item_type.value,
            rejecter=rejecter_id,
        )
        return True

    # ── Auto counter-offer generation ─────────────────────────────────────────

    def auto_counter(
        self, session: NegotiationSession, responder: "Sim"
    ) -> dict | None:
        """
        Heuristic counter-offer based on personality.
        Returns new_terms or None (if responder decides to reject outright).
        """
        if not session.is_live():
            return None
        offer = session.latest_offer()
        if not offer:
            return None

        agreeableness = responder.ocean.get("agreeableness", 0.5)
        # Low agreeableness → harder counter; high → near-accept
        if random.random() < (1 - agreeableness) * 0.4:
            return None   # outright reject

        terms = dict(offer.terms)
        item = session.item_type

        if item == NegotiationItem.LOAN:
            rate = terms.get("interest_rate", 0.05)
            terms["interest_rate"] = max(0.02, rate * (1 - agreeableness * 0.2))
        elif item == NegotiationItem.JOB_OFFER:
            salary = terms.get("salary", 100)
            terms["salary"] = salary * (1.05 + agreeableness * 0.1)
        elif item == NegotiationItem.PROPERTY_SALE:
            price = terms.get("price", 1000)
            terms["price"] = price * (0.92 + agreeableness * 0.04)
        elif item == NegotiationItem.PARTNERSHIP:
            share = terms.get("share", 0.3)
            terms["share"] = min(0.5, share + 0.05 * (1 - agreeableness))

        return terms

    # ── Materialisation ───────────────────────────────────────────────────────

    def _materialise(
        self,
        session: NegotiationSession,
        terms: dict,
        engine: "SimEngine",
    ) -> dict:
        """Convert accepted negotiation into a game contract + on-chain agreement."""
        result: dict = {"terms": terms}

        try:
            if session.item_type == NegotiationItem.LOAN:
                out = engine.create_contract_loan(
                    session.party_a_id, session.party_b_id,
                    float(terms.get("principal", 100)),
                    float(terms.get("interest_rate", 0.05)),
                    int(terms.get("duration_ticks", 50)),
                )
                result["contract_id"] = out.get("contract_id")

            elif session.item_type == NegotiationItem.JOB_OFFER:
                out = engine.create_contract_employment(
                    session.party_a_id, session.party_b_id,
                    float(terms.get("salary", 50)),
                    int(terms.get("period_ticks", 5)),
                )
                result["contract_id"] = out.get("contract_id")

            elif session.item_type == NegotiationItem.PARTNERSHIP:
                out = engine.create_contract_partnership(
                    session.party_a_id, session.party_b_id,
                    float(terms.get("share", 0.3)),
                    float(terms.get("buyout", 5000)),
                )
                result["contract_id"] = out.get("contract_id")

            elif session.item_type == NegotiationItem.MARRIAGE_PROPOSAL:
                result["marriage_intent"] = True

            elif session.item_type == NegotiationItem.TRUCE:
                # Reduce hostility in relationship
                rel = engine.relationships.get(session.party_a_id, session.party_b_id)
                rel.apply_deltas(10.0, 0.0)
                result["truce_applied"] = True

        except Exception as exc:
            logger.warning("[Negotiation] materialise error: %s", exc)

        return result

    # ── Trust pricing ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_trust(a: "Sim", b: "Sim") -> float:
        """Trust = blend of friendship, reputation, and personality."""
        try:
            from core.compatibility import attraction_score
            compat = (attraction_score(a, b) + 1) / 2  # normalise to 0..1
        except Exception:
            compat = 0.5
        rep_b  = max(0.0, min(1.0, (b.reputation_score + 100) / 200))
        agree  = b.ocean.get("agreeableness", 0.5)
        return round(compat * 0.4 + rep_b * 0.4 + agree * 0.2, 3)

    # ── Periodic tick ─────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        for sid, session in list(self._sessions.items()):
            if not session.is_live():
                continue
            session.ticks_open += 1
            if session.ticks_open >= session.expire_ticks:
                session.status = SessionStatus.EXPIRED
                engine._bus.emit(
                    "negotiation_expired",
                    session_id=sid,
                    item_type=session.item_type.value,
                )

        # Prune terminal sessions older than 100 ticks
        cutoff = engine.tick_count - 100
        self._sessions = {
            sid: s for sid, s in self._sessions.items()
            if s.created_tick > cutoff or s.is_live()
        }

    # ── API ───────────────────────────────────────────────────────────────────

    def get(self, session_id: str) -> NegotiationSession | None:
        return self._sessions.get(session_id)

    def active_for(self, sim_id: str) -> list[NegotiationSession]:
        return [
            s for s in self._sessions.values()
            if s.is_live() and sim_id in (s.party_a_id, s.party_b_id)
        ]

    def stats(self) -> dict:
        by_status: dict[str, int] = {}
        for s in self._sessions.values():
            by_status[s.status] = by_status.get(s.status, 0) + 1
        return {"total_sessions": len(self._sessions), "by_status": by_status}
