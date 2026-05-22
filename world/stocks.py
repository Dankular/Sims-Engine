from __future__ import annotations

import json
import random
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass
class Stock:
    ticker: str
    name: str
    price: float
    shares_outstanding: int


class StockMarket:
    def __init__(self) -> None:
        self._stocks: dict[str, Stock] = {
            "RET": Stock("RET", "Retail Basket", 100.0, 1_000_000),
            "CAR": Stock("CAR", "Career Index", 120.0, 1_000_000),
            "REI": Stock("REI", "Real Estate Trust", 150.0, 1_000_000),
        }
        self._symbol_map: dict[str, str] = {
            "RET": "WMT",  # retail proxy
            "CAR": "MSFT",  # career/productivity proxy
            "REI": "O",  # REIT proxy
        }
        self._symbol_fallbacks: dict[str, list[str]] = {
            "RET": ["WMT", "COST", "TGT"],
            "CAR": ["MSFT", "AAPL", "IBM"],
            "REI": ["O", "AMT", "PLD"],
        }
        self._holdings: dict[str, dict[str, int]] = {}
        self._last_events: list[dict] = []
        self._history: dict[str, list[dict]] = {"RET": [], "CAR": [], "REI": []}
        self._history_file = Path("datasets") / "stock_history.json"
        self._load_history_file()

    def on_event(self, event_type: str, magnitude: float = 1.0) -> None:
        self._last_events.append({"event": event_type, "magnitude": float(magnitude)})
        self._last_events = self._last_events[-80:]

    def tick(self, engine) -> None:
        self._apply_event_pressure()
        self._apply_noise()
        self._auto_invest(engine)

    def _apply_event_pressure(self) -> None:
        for evt in self._last_events[-12:]:
            et = evt.get("event", "")
            mag = float(evt.get("magnitude", 1.0))
            if "shop" in et or "retail" in et:
                self._move("RET", 0.006 * mag)
            if "fired" in et or "breach" in et:
                self._move("CAR", -0.008 * mag)
            if "property" in et:
                self._move("REI", 0.005 * mag)

    def _apply_noise(self) -> None:
        for ticker in self._stocks.keys():
            self._move(ticker, random.uniform(-0.007, 0.007))

    def _move(self, ticker: str, delta_pct: float) -> None:
        s = self._stocks.get(ticker)
        if not s:
            return
        s.price = max(1.0, round(s.price * (1.0 + delta_pct), 4))

    def _auto_invest(self, engine) -> None:
        if engine.tick_count % 12 != 0:
            return
        for sim in engine.sims:
            open_score = float(sim.profile.get("ocean", {}).get("openness", 0.5))
            cons_score = float(
                sim.profile.get("ocean", {}).get("conscientiousness", 0.5)
            )
            if open_score < 0.62 and cons_score < 0.62:
                continue
            budget = max(0.0, sim.simoleons - 600.0)
            if budget < 50:
                continue
            ticker = random.choice(list(self._stocks.keys()))
            shares = int((budget * 0.03) / max(1.0, self._stocks[ticker].price))
            if shares > 0:
                self.buy(sim.sim_id, ticker, shares, engine)

    def buy(self, sim_id: str, ticker: str, shares: int, engine) -> bool:
        s = self._stocks.get(ticker)
        sim = engine._sim_lookup.get(sim_id)
        if not s or sim is None or shares <= 0:
            return False
        cost = s.price * shares
        if sim.simoleons < cost:
            return False
        sim.simoleons -= cost
        self._holdings.setdefault(sim_id, {})[ticker] = self._holdings.setdefault(
            sim_id, {}
        ).get(ticker, 0) + int(shares)
        return True

    def sell(self, sim_id: str, ticker: str, shares: int, engine) -> bool:
        s = self._stocks.get(ticker)
        sim = engine._sim_lookup.get(sim_id)
        if not s or sim is None or shares <= 0:
            return False
        own = self._holdings.setdefault(sim_id, {}).get(ticker, 0)
        if own < shares:
            return False
        self._holdings[sim_id][ticker] = own - shares
        sim.simoleons += s.price * shares
        return True

    def portfolio(self, sim_id: str) -> dict:
        h = dict(self._holdings.get(sim_id, {}))
        value = sum(
            self._stocks[t].price * qty for t, qty in h.items() if t in self._stocks
        )
        return {"holdings": h, "value": round(value, 2)}

    def state(self) -> dict:
        return {
            "prices": {k: v.price for k, v in self._stocks.items()},
            "last_events": list(self._last_events[-20:]),
            "history_points": {k: len(v) for k, v in self._history.items()},
            "symbol_map": dict(self._symbol_map),
        }

    def history(self, ticker: str, limit: int = 180) -> list[dict]:
        rows = list(self._history.get(str(ticker).upper(), []))
        return rows[-max(1, int(limit)) :]

    def backfill_from_alpha_vantage(
        self,
        api_key: str,
        outputsize: str = "compact",
        endpoint_base: str = "https://www.alphavantage.co/query",
        persist: bool = True,
    ) -> dict:
        if not api_key:
            return {"ok": False, "reason": "missing_api_key"}
        results: dict[str, dict] = {}
        for ticker, symbol in self._symbol_map.items():
            candidates = self._symbol_fallbacks.get(ticker, [symbol])
            ts = {}
            last_reason = ""
            used_symbol = symbol
            for candidate in candidates:
                params = {
                    "function": "TIME_SERIES_DAILY",
                    "symbol": candidate,
                    "outputsize": outputsize,
                    "apikey": api_key,
                }
                url = f"{endpoint_base}?{urlencode(params)}"
                with urlopen(url, timeout=30) as res:
                    payload = json.loads(res.read().decode("utf-8"))
                if "Error Message" in payload:
                    last_reason = str(payload.get("Error Message"))
                    continue
                if "Note" in payload:
                    last_reason = str(payload.get("Note"))
                    continue
                if "Information" in payload:
                    last_reason = str(payload.get("Information"))
                    continue
                ts = payload.get("Time Series (Daily)", {})
                used_symbol = candidate
                if ts:
                    break
            if not ts:
                results[ticker] = {
                    "ok": False,
                    "reason": last_reason or "no_time_series_data",
                }
                continue

            points: list[dict] = []
            for day, row in ts.items():
                try:
                    close_px = float(
                        row.get("4. close", row.get("5. adjusted close", 0.0))
                    )
                except Exception:
                    continue
                points.append({"date": day, "close": close_px})
            points.sort(key=lambda x: x["date"])
            self._history[ticker] = points
            if points:
                self._stocks[ticker].price = float(points[-1]["close"])
                self._symbol_map[ticker] = used_symbol
            results[ticker] = {
                "ok": True,
                "symbol": used_symbol,
                "points": len(points),
                "latest_date": points[-1]["date"] if points else None,
            }

        ok = all(v.get("ok") for v in results.values()) if results else False
        if persist and ok:
            self._save_history_file()
        return {
            "ok": ok,
            "as_of": datetime.utcnow().isoformat() + "Z",
            "outputsize": outputsize,
            "results": results,
        }

    def _load_history_file(self) -> None:
        if not self._history_file.exists():
            return
        try:
            data = json.loads(self._history_file.read_text(encoding="utf-8"))
            hist = data.get("history", {}) if isinstance(data, dict) else {}
            for ticker in self._history.keys():
                rows = hist.get(ticker, []) if isinstance(hist, dict) else []
                if isinstance(rows, list):
                    self._history[ticker] = [r for r in rows if isinstance(r, dict)]
                    if self._history[ticker]:
                        self._stocks[ticker].price = float(
                            self._history[ticker][-1].get(
                                "close", self._stocks[ticker].price
                            )
                        )
        except Exception:
            return

    def _save_history_file(self) -> None:
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "symbols": self._symbol_map,
            "history": self._history,
        }
        self._history_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
