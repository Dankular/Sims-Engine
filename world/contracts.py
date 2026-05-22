from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimContract:
    contract_id: str
    contract_type: str
    party_a: str
    party_b: str
    terms: dict
    created_tick: int
    active: bool = True
    settled: bool = False
    breached: bool = False
    last_tick: int = 0
    metadata: dict = field(default_factory=dict)


class ContractEngine:
    def __init__(self) -> None:
        self._contracts: dict[str, SimContract] = {}
        self._nonce = 0
        self._economy_events: list[dict] = []

    def _next_id(self) -> str:
        self._nonce += 1
        return f"c_{self._nonce:06d}"

    def create_loan(
        self,
        lender_id: str,
        borrower_id: str,
        principal: float,
        interest_rate: float,
        duration_ticks: int,
        tick: int,
    ) -> dict:
        cid = self._next_id()
        self._contracts[cid] = SimContract(
            contract_id=cid,
            contract_type="loan",
            party_a=lender_id,
            party_b=borrower_id,
            terms={
                "principal": float(principal),
                "interest_rate": float(interest_rate),
                "duration_ticks": int(duration_ticks),
                "due_tick": int(tick) + int(duration_ticks),
            },
            created_tick=int(tick),
        )
        return {"ok": True, "contract_id": cid}

    def create_employment(
        self,
        employer_id: str,
        employee_id: str,
        wage: float,
        period_ticks: int,
        severance: float,
        tick: int,
    ) -> dict:
        cid = self._next_id()
        self._contracts[cid] = SimContract(
            contract_id=cid,
            contract_type="employment",
            party_a=employer_id,
            party_b=employee_id,
            terms={
                "wage": float(wage),
                "period_ticks": max(1, int(period_ticks)),
                "severance": float(severance),
            },
            created_tick=int(tick),
            last_tick=int(tick),
        )
        return {"ok": True, "contract_id": cid}

    def create_partnership(
        self,
        a_id: str,
        b_id: str,
        revenue_share: float,
        buyout: float,
        tick: int,
    ) -> dict:
        cid = self._next_id()
        self._contracts[cid] = SimContract(
            contract_id=cid,
            contract_type="partnership",
            party_a=a_id,
            party_b=b_id,
            terms={
                "revenue_share": max(0.0, min(0.5, float(revenue_share))),
                "buyout_trigger": float(buyout),
            },
            created_tick=int(tick),
        )
        return {"ok": True, "contract_id": cid}

    def tick(self, engine) -> list[dict]:
        events: list[dict] = []
        t = int(engine.tick_count)
        for c in self._contracts.values():
            if not c.active or c.settled:
                continue
            if c.contract_type == "loan":
                ev = self._tick_loan(engine, c, t)
            elif c.contract_type == "employment":
                ev = self._tick_employment(engine, c, t)
            else:
                ev = self._tick_partnership(engine, c, t)
            if ev:
                events.extend(ev)
        return events

    def _tick_loan(self, engine, c: SimContract, tick: int) -> list[dict]:
        out = []
        lender = engine._sim_lookup.get(c.party_a)
        borrower = engine._sim_lookup.get(c.party_b)
        if lender is None or borrower is None:
            c.active = False
            return out
        if c.metadata.get("funded") is not True:
            principal = float(c.terms.get("principal", 0.0))
            if lender.simoleons >= principal:
                lender.simoleons -= principal
                _eng = getattr(borrower, "_engine_ref", None)
                if _eng:
                    from persistence.ledger import TX_CONTRACT_LOAN_RECV
                    _eng._tx(borrower, principal, TX_CONTRACT_LOAN_RECV,
                             counterpart=lender.sim_id,
                             description=f"loan disbursed: {c.contract_id[:8]}")
                else:
                    borrower.simoleons += principal
                c.metadata["funded"] = True
                out.append({"type": "contract_funded", "contract_id": c.contract_id})
        if tick >= int(c.terms.get("due_tick", tick + 1)):
            due = float(c.terms.get("principal", 0.0)) * (
                1.0 + float(c.terms.get("interest_rate", 0.0))
            )
            if borrower.simoleons >= due:
                borrower.simoleons -= due
                _eng = getattr(lender, '_engine_ref', None)
                if _eng:
                    from persistence.ledger import TX_CONTRACT_SETTLED_RECV
                    _eng._tx(lender, due, TX_CONTRACT_SETTLED_RECV,
                             counterpart=borrower.sim_id, description=f'loan repaid: {c.contract_id[:8]}')
                else:
                    lender.simoleons += due
                c.settled = True
                c.active = False
                out.append(
                    {
                        "type": "contract_settled",
                        "contract_id": c.contract_id,
                        "amount": round(due, 2),
                    }
                )
            else:
                c.breached = True
                c.active = False
                borrower.reputation_score = max(0.0, borrower.reputation_score - 2.5)
                out.append(
                    {
                        "type": "contract_breached",
                        "contract_id": c.contract_id,
                        "reason": "loan_default",
                    }
                )
        return out

    def _tick_employment(self, engine, c: SimContract, tick: int) -> list[dict]:
        out = []
        employer = engine._sim_lookup.get(c.party_a)
        employee = engine._sim_lookup.get(c.party_b)
        if employer is None or employee is None:
            c.active = False
            return out
        period = max(1, int(c.terms.get("period_ticks", 5)))
        if tick - int(c.last_tick) >= period:
            wage = float(c.terms.get("wage", 0.0))
            _eng = getattr(employee, "_engine_ref", None)
            if employer.simoleons >= wage:
                employer.simoleons -= wage
                if _eng:
                    from persistence.ledger import TX_CONTRACT_SALARY_RECV
                    _eng._tx(employee, wage, TX_CONTRACT_SALARY_RECV,
                             counterpart=employer.sim_id, description="employment salary")
                else:
                    employee.simoleons += wage
                c.last_tick = tick
                out.append({
                    "type": "contract_settlement",
                    "contract_id": c.contract_id,
                    "amount": round(wage, 2),
                })
            else:
                c.breached = True
                c.active = False
                sev = float(c.terms.get("severance", 0.0))
                pay = min(sev, max(0.0, employer.simoleons))
                employer.simoleons -= pay
                if _eng and pay > 0:
                    from persistence.ledger import TX_CONTRACT_SALARY_RECV
                    _eng._tx(employee, pay, TX_CONTRACT_SALARY_RECV,
                             counterpart=employer.sim_id, description="severance pay")
                elif pay > 0:
                    employee.simoleons += pay
                out.append(
                    {
                        "type": "contract_breached",
                        "contract_id": c.contract_id,
                        "reason": "missed_wage",
                    }
                )
        return out

    def _tick_partnership(self, engine, c: SimContract, tick: int) -> list[dict]:
        _ = tick
        a = engine._sim_lookup.get(c.party_a)
        b = engine._sim_lookup.get(c.party_b)
        if a is None or b is None:
            c.active = False
            return []
        if max(a.simoleons, b.simoleons) >= float(c.terms.get("buyout_trigger", 1e9)):
            c.settled = True
            c.active = False
            return [
                {
                    "type": "contract_settled",
                    "contract_id": c.contract_id,
                    "reason": "buyout_trigger",
                }
            ]
        return []

    def list_contracts(self, active_only: bool = False) -> list[dict]:
        rows = []
        for c in self._contracts.values():
            if active_only and not c.active:
                continue
            rows.append(
                {
                    "contract_id": c.contract_id,
                    "type": c.contract_type,
                    "party_a": c.party_a,
                    "party_b": c.party_b,
                    "active": c.active,
                    "settled": c.settled,
                    "breached": c.breached,
                    "terms": dict(c.terms),
                }
            )
        return rows

    def stats(self) -> dict:
        vals = list(self._contracts.values())
        return {
            "total": len(vals),
            "active": sum(1 for c in vals if c.active),
            "settled": sum(1 for c in vals if c.settled),
            "breached": sum(1 for c in vals if c.breached),
            "economy_events_seen": len(self._economy_events),
        }

    def obligations_for(self, sim_id: str) -> dict:
        sim_id = str(sim_id)
        obligations = []
        total = 0.0
        for c in self._contracts.values():
            if not c.active:
                continue
            if c.contract_type == "loan":
                principal = float(c.terms.get("principal", 0.0))
                rate = float(c.terms.get("interest_rate", 0.0))
                due = principal * (1.0 + rate)
                if c.party_b == sim_id:
                    obligations.append(
                        {
                            "contract_id": c.contract_id,
                            "type": "loan_debt",
                            "counterparty": c.party_a,
                            "amount": round(due, 2),
                            "due_tick": int(c.terms.get("due_tick", c.created_tick)),
                        }
                    )
                    total += due
                elif c.party_a == sim_id:
                    obligations.append(
                        {
                            "contract_id": c.contract_id,
                            "type": "loan_receivable",
                            "counterparty": c.party_b,
                            "amount": round(due, 2),
                            "due_tick": int(c.terms.get("due_tick", c.created_tick)),
                        }
                    )
            elif c.contract_type == "employment":
                wage = float(c.terms.get("wage", 0.0))
                if c.party_a == sim_id:
                    obligations.append(
                        {
                            "contract_id": c.contract_id,
                            "type": "wage_outflow",
                            "counterparty": c.party_b,
                            "amount": round(wage, 2),
                            "period_ticks": int(c.terms.get("period_ticks", 5)),
                        }
                    )
                    total += wage
        return {
            "count": len(obligations),
            "obligations": obligations,
            "total_outstanding": round(total, 2),
        }

    def observe_economy_event(self, event_name: str, payload: dict) -> None:
        self._economy_events.append(
            {"event": str(event_name), "payload": dict(payload)}
        )
        self._economy_events = self._economy_events[-300:]
