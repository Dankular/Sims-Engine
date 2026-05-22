from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class BetRecord:
    bet_id: str
    bettor_type: str
    bettor_id: str
    match_id: str
    selection: str
    stake: float
    odds: float
    placed_tick: int
    settled: bool = False
    won: bool = False
    payout: float = 0.0


class BookieSystem:
    def __init__(self, api_key: str = "", poll_interval_ticks: int = 5) -> None:
        self.api_key = api_key.strip()
        self.poll_interval_ticks = max(1, int(poll_interval_ticks))
        self.matches: dict[str, dict] = {}
        self.bets: dict[str, BetRecord] = {}
        self.player_balances: dict[str, float] = {}
        self._nonce = 0
        self._last_fetch_tick = -1
        self._last_fetch_error = ""

    def tick(self, engine) -> None:
        if engine.tick_count % self.poll_interval_ticks == 0:
            self.refresh_matches()
        self._settle_resolved_matches(engine)

    def refresh_matches(self) -> dict:
        if not self.api_key:
            self._last_fetch_error = "missing_api_key"
            return {"ok": False, "reason": self._last_fetch_error}
        combined = {}
        for endpoint in (
            "/lives",
            "/matches",
            "/matches/running",
            "/matches/past",
        ):
            out = self._fetch_pandascore(endpoint)
            if out.get("ok"):
                for row in out.get("rows", []):
                    m_id = str(row.get("id", ""))
                    if not m_id:
                        continue
                    combined[m_id] = self._to_market_row(row)
        if not combined:
            return {"ok": False, "reason": self._last_fetch_error or "no_matches"}
        self.matches = combined
        self._last_fetch_error = ""
        return {"ok": True, "count": len(self.matches)}

    def _fetch_pandascore(self, endpoint: str) -> dict:
        try:
            qs = urlencode({"token": self.api_key, "per_page": 50})
            url = f"https://api.pandascore.co{endpoint}?{qs}"
            req = Request(url, headers={"accept": "application/json"}, method="GET")
            with urlopen(req, timeout=20) as res:
                payload = json.loads(res.read().decode("utf-8"))
            if not isinstance(payload, list):
                self._last_fetch_error = "unexpected_payload"
                return {"ok": False, "reason": self._last_fetch_error}
            return {"ok": True, "rows": payload}
        except Exception as exc:
            self._last_fetch_error = str(exc)
            return {"ok": False, "reason": self._last_fetch_error}

    def _to_market_row(self, row: dict[str, Any]) -> dict:
        opponents = row.get("opponents", []) or []
        names = []
        for op in opponents[:2]:
            opp = op.get("opponent", {}) if isinstance(op, dict) else {}
            names.append(str(opp.get("name", "TBD")))
        while len(names) < 2:
            names.append("TBD")
        odds_a, odds_b = self._estimate_odds(row)
        return {
            "match_id": str(row.get("id", "")),
            "name": f"{names[0]} vs {names[1]}",
            "status": str(row.get("status", "")),
            "scheduled_at": row.get("scheduled_at"),
            "team_a": names[0],
            "team_b": names[1],
            "odds": {names[0]: odds_a, names[1]: odds_b},
            "winner": self._winner_name(row, names),
            "raw": row,
        }

    def _estimate_odds(self, row: dict[str, Any]) -> tuple[float, float]:
        results = row.get("results", []) or []
        score_a = float(results[0].get("score", 0.0)) if len(results) > 0 else 0.0
        score_b = float(results[1].get("score", 0.0)) if len(results) > 1 else 0.0
        if score_a == score_b:
            return (1.9, 1.9)
        if score_a > score_b:
            return (1.6, 2.3)
        return (2.3, 1.6)

    def _winner_name(self, row: dict[str, Any], names: list[str]) -> str:
        winner = row.get("winner") or {}
        winner_name = str(winner.get("name", "")) if isinstance(winner, dict) else ""
        if winner_name:
            return winner_name
        results = row.get("results", []) or []
        if len(results) >= 2:
            a = float(results[0].get("score", 0.0))
            b = float(results[1].get("score", 0.0))
            if a > b:
                return names[0]
            if b > a:
                return names[1]
        return ""

    def place_bet(
        self,
        bettor_type: str,
        bettor_id: str,
        match_id: str,
        selection: str,
        stake: float,
        tick: int,
    ) -> dict:
        market = self.matches.get(str(match_id))
        if not market:
            return {"ok": False, "reason": "match_not_found"}
        stake = float(stake)
        if stake <= 0:
            return {"ok": False, "reason": "invalid_stake"}
        odds_map = market.get("odds", {})
        if selection not in odds_map:
            return {"ok": False, "reason": "invalid_selection"}
        self._nonce += 1
        bet_id = f"bet_{self._nonce:07d}"
        rec = BetRecord(
            bet_id=bet_id,
            bettor_type=str(bettor_type),
            bettor_id=str(bettor_id),
            match_id=str(match_id),
            selection=str(selection),
            stake=stake,
            odds=float(odds_map.get(selection, 1.0)),
            placed_tick=int(tick),
        )
        self.bets[bet_id] = rec
        return {"ok": True, "bet_id": bet_id, "odds": rec.odds}

    def _settle_resolved_matches(self, engine) -> None:
        for bet in self.bets.values():
            if bet.settled:
                continue
            market = self.matches.get(bet.match_id)
            if not market:
                continue
            status = str(market.get("status", "")).lower()
            if status not in {"finished", "complete", "canceled"}:
                continue
            winner = str(market.get("winner", ""))
            bet.settled = True
            bet.won = winner != "" and bet.selection == winner
            bet.payout = round(bet.stake * bet.odds, 2) if bet.won else 0.0
            if bet.bettor_type == "sim":
                sim = engine._sim_lookup.get(bet.bettor_id)
                if sim is not None:
                    _eng = getattr(sim, '_engine_ref', None)
                    if _eng:
                        from persistence.ledger import TX_BETTING_WIN
                        _eng._tx(sim, bet.payout, TX_BETTING_WIN, description='bet win')
                    else:
                        sim.simoleons += bet.payout
            else:
                self.player_balances[bet.bettor_id] = (
                    self.player_balances.get(bet.bettor_id, 0.0) + bet.payout
                )
            try:
                engine.ledger.record(
                    "bookie_settlement",
                    int(engine.tick_count),
                    {
                        "bet_id": bet.bet_id,
                        "bettor_type": bet.bettor_type,
                        "bettor_id": bet.bettor_id,
                        "match_id": bet.match_id,
                        "selection": bet.selection,
                        "won": bool(bet.won),
                        "payout": round(float(bet.payout), 2),
                    },
                )
                engine._bus.emit(
                    "bookie_settlement",
                    bet_id=bet.bet_id,
                    bettor_type=bet.bettor_type,
                    bettor_id=bet.bettor_id,
                    match_id=bet.match_id,
                    selection=bet.selection,
                    won=bool(bet.won),
                    payout=round(float(bet.payout), 2),
                    tick=int(engine.tick_count),
                )
            except Exception:
                pass

    def state(self) -> dict:
        open_bets = sum(1 for b in self.bets.values() if not b.settled)
        settled_bets = sum(1 for b in self.bets.values() if b.settled)
        return {
            "matches": len(self.matches),
            "open_bets": open_bets,
            "settled_bets": settled_bets,
            "player_wallets": len(self.player_balances),
            "last_fetch_error": self._last_fetch_error,
            "sample_matches": list(self.matches.values())[:10],
            "ts": time.time(),
        }
