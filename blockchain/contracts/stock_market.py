"""
blockchain/contracts/stock_market.py — Virtual stock exchange on SimChain.

8 sector stocks pre-listed at genesis.  Prices move from sim world events:
  shop_visit_cafe → CAFE +0.1%   |  sim_promoted → TECH +0.3%
  illness_outbreak → MED +0.5%   |  sim_married  → PROP +0.2%
  high_social      → ENT +0.2%   |  property_purchased → PROP +0.3%

Sims with high openness auto-invest idle SimCoin (via web3_bridge).
Shares are held as on-chain balances in per-stock escrow addresses.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from blockchain.contracts.base import SmartContract

if TYPE_CHECKING:
    from blockchain.chain import SimChain
    from blockchain.transaction import SimTransaction

logger = logging.getLogger(__name__)

SIM_WEI = 10 ** 18

# ── Listed stocks ─────────────────────────────────────────────────────────────
_LISTED: dict[str, dict] = {
    "CAFE": {"name": "SimCafe Corp",         "sector": "retail",       "price": 10.0,  "volatility": 0.018},
    "GYM":  {"name": "FitSim Inc",           "sector": "wellness",     "price": 8.0,   "volatility": 0.015},
    "PROP": {"name": "SimRealty Trust",      "sector": "real_estate",  "price": 25.0,  "volatility": 0.012},
    "TECH": {"name": "SimTech Ventures",     "sector": "technology",   "price": 15.0,  "volatility": 0.022},
    "FOOD": {"name": "SimFood Delivery",     "sector": "food",         "price": 6.0,   "volatility": 0.020},
    "ENT":  {"name": "SimEntertainment Co",  "sector": "entertainment", "price": 12.0, "volatility": 0.025},
    "MED":  {"name": "SimHealth Group",      "sector": "health",       "price": 20.0,  "volatility": 0.014},
    "EDU":  {"name": "SimAcademy Ltd",       "sector": "education",    "price": 9.0,   "volatility": 0.010},
}

# World event → ticker impact map
_EVENT_IMPACTS: dict[str, dict[str, float]] = {
    "shop_visit_cafe":    {"CAFE": +0.001, "FOOD": +0.0005},
    "shop_visit_gym":     {"GYM":  +0.001},
    "shop_visit_spa":     {"MED":  +0.0005, "ENT": +0.0003},
    "sim_promoted":       {"TECH": +0.003, "PROP": +0.001},
    "sim_fired":          {"TECH": -0.002},
    "sim_married":        {"PROP": +0.002, "ENT": +0.001},
    "sim_divorced":       {"PROP": -0.001, "MED": +0.001},
    "illness_outbreak":   {"MED":  +0.005, "FOOD": -0.003},
    "high_social":        {"ENT":  +0.002, "CAFE": +0.001},
    "property_purchased": {"PROP": +0.003, "CAFE": +0.001},
    "gig_completed":      {"TECH": +0.001, "FOOD": +0.001},
    "graduation":         {"EDU":  +0.004, "TECH": +0.002},
    "celebrity_rise":     {"ENT":  +0.005, "CAFE": +0.002},
}


@dataclass
class Stock:
    ticker:            str
    name:              str
    sector:            str
    price:             float
    volatility:        float        = 0.020
    shares_outstanding: int         = 1_000_000
    # address → shares held
    holdings:          dict         = field(default_factory=dict)
    price_history:     list[float]  = field(default_factory=list)

    @property
    def market_cap_sim(self) -> float:
        return self.price * self.shares_outstanding

    def apply_delta(self, pct: float) -> None:
        self.price = max(0.01, round(self.price * (1 + pct), 6))
        self.price_history.append(self.price)
        if len(self.price_history) > 500:
            self.price_history = self.price_history[-500:]

    def shares_held_by(self, address: str) -> int:
        return self.holdings.get(address, 0)


class StockMarket(SmartContract):
    contract_id = "stock_market"

    def __init__(self) -> None:
        self._stocks: dict[str, Stock] = {
            ticker: Stock(ticker=ticker, **info)
            for ticker, info in _LISTED.items()
        }
        self._order_log: list[dict] = []
        self._tick = 0

    # ── Price discovery ───────────────────────────────────────────────────────

    def apply_event(self, event_type: str, context: dict | None = None) -> None:
        """Drive stock prices from sim world events. Call from web3_bridge."""
        impacts = _EVENT_IMPACTS.get(event_type, {})
        for ticker, delta in impacts.items():
            if ticker in self._stocks:
                noise = random.gauss(0, self._stocks[ticker].volatility * 0.05)
                self._stocks[ticker].apply_delta(delta + noise)

    def tick_market(self) -> None:
        """Gaussian random-walk noise each tick — organic price drift."""
        self._tick += 1
        for stock in self._stocks.values():
            # Mean-reverting drift + random noise
            drift = (10.0 - stock.price) * 0.0001  # very slow mean reversion to $10
            noise = random.gauss(drift, stock.volatility * 0.008)
            stock.apply_delta(noise)

    # ── Order execution ───────────────────────────────────────────────────────

    def buy(
        self, address: str, ticker: str, shares: int, chain: "SimChain"
    ) -> bool:
        stock = self._stocks.get(ticker)
        if not stock or shares <= 0:
            return False
        cost_wei = int(stock.price * SIM_WEI * shares)
        if chain.balance_of(address) < cost_wei:
            logger.debug(
                "[StockMarket] %s cannot afford %d %s (need %.2f, have %.2f)",
                address[:8], shares, ticker,
                cost_wei / SIM_WEI, chain.balance_sim(address),
            )
            return False
        # SimCoin moves to market escrow address
        chain._transfer(address, f"mkt:{ticker}", cost_wei)
        stock.holdings[address] = stock.shares_held_by(address) + shares
        # Buying pressure → price up
        stock.apply_delta(shares / stock.shares_outstanding * 0.3)
        self._order_log.append({
            "type": "buy", "address": address[:8], "ticker": ticker,
            "shares": shares, "price": stock.price, "ts": time.time(),
        })
        logger.debug("[StockMarket] BUY %d %s @ %.4f | %s", shares, ticker, stock.price, address[:8])
        return True

    def sell(
        self, address: str, ticker: str, shares: int, chain: "SimChain"
    ) -> bool:
        stock = self._stocks.get(ticker)
        if not stock or shares <= 0:
            return False
        if stock.shares_held_by(address) < shares:
            return False
        proceeds_wei = int(stock.price * SIM_WEI * shares)
        chain._transfer(f"mkt:{ticker}", address, proceeds_wei)
        stock.holdings[address] -= shares
        # Selling pressure → price down
        stock.apply_delta(-shares / stock.shares_outstanding * 0.3)
        self._order_log.append({
            "type": "sell", "address": address[:8], "ticker": ticker,
            "shares": shares, "price": stock.price, "ts": time.time(),
        })
        return True

    # ── On-chain handlers ─────────────────────────────────────────────────────

    def on_stock_buy(self, tx: "SimTransaction", chain: "SimChain") -> None:
        d = tx.data
        self.buy(tx.from_addr, d.get("ticker", ""), d.get("shares", 0), chain)

    def on_stock_sell(self, tx: "SimTransaction", chain: "SimChain") -> None:
        d = tx.data
        self.sell(tx.from_addr, d.get("ticker", ""), d.get("shares", 0), chain)

    # ── Queries ───────────────────────────────────────────────────────────────

    def prices(self) -> dict[str, float]:
        return {t: s.price for t, s in self._stocks.items()}

    def portfolio(self, address: str) -> dict:
        return {
            ticker: {
                "shares":    stock.shares_held_by(address),
                "value_sim": stock.shares_held_by(address) * stock.price,
                "price":     stock.price,
                "sector":    stock.sector,
            }
            for ticker, stock in self._stocks.items()
            if stock.shares_held_by(address) > 0
        }

    def portfolio_value(self, address: str) -> float:
        return sum(
            s.shares_held_by(address) * s.price
            for s in self._stocks.values()
        )

    def ticker_info(self, ticker: str) -> dict | None:
        s = self._stocks.get(ticker)
        if not s:
            return None
        return {
            "ticker":      s.ticker,
            "name":        s.name,
            "sector":      s.sector,
            "price":       s.price,
            "market_cap":  s.market_cap_sim,
            "price_history": s.price_history[-50:],
        }

    def stats(self) -> dict:
        return {
            "prices":       self.prices(),
            "total_orders": len(self._order_log),
            "market_caps":  {t: s.market_cap_sim for t, s in self._stocks.items()},
        }
