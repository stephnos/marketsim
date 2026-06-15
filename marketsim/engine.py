"""The market simulation engine.

This is the single source of truth shared by the web GUI and the terminal CLI
(both talk to it through the HTTP API). It:

  * generates believable historical price paths (geometric Brownian motion),
  * advances a live price on every tick,
  * answers quote / history / search / movers queries,
  * keeps per-account cash, positions, watchlists and an order blotter,
  * persists account & watchlist data to disk so it survives restarts.

Prices here are *simulated*. Nothing in this module touches a real exchange.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .instruments import UNIVERSE, UNIVERSE_BY_SYMBOL, Instrument
from .storage import Storage

# ---- simulation constants -------------------------------------------------

TRADING_DAYS = 252            # ~1 year of daily candles to generate up front
INTRADAY_POINTS = 390         # minutes in a US trading session (6.5h)
SECONDS_PER_YEAR = 252 * INTRADAY_POINTS * 60
STARTING_CASH = 100_000.0     # paper money each new account begins with

RANGE_SPEC = {
    # range -> (number of points, seconds each point represents)
    "1D": ("intraday", INTRADAY_POINTS),
    "1W": ("daily", 5),
    "1M": ("daily", 22),
    "3M": ("daily", 66),
    "6M": ("daily", 132),
    "1Y": ("daily", 252),
}


@dataclass
class Candle:
    t: int          # unix seconds
    o: float
    h: float
    l: float
    c: float
    v: int


@dataclass
class SymbolState:
    inst: Instrument
    prev_close: float
    day_open: float
    price: float
    day_high: float
    day_low: float
    volume: int
    daily: list[Candle] = field(default_factory=list)     # ~1Y of daily candles
    intraday: list[Candle] = field(default_factory=list)   # current-session minutes


@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float


@dataclass
class Order:
    id: int
    ts: float
    account: str
    symbol: str
    side: str        # "buy" | "sell"
    qty: float
    price: float
    notional: float
    status: str = "filled"


@dataclass
class Account:
    name: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    watchlist: list[str] = field(default_factory=list)
    created: float = field(default_factory=lambda: time.time())


class Engine:
    """Thread-safe in-memory market + accounts, with disk persistence for users."""

    def __init__(self, storage: Storage | None = None, seed: int = 1_234):
        self._lock = threading.RLock()
        self._storage = storage or Storage()
        self._seed = seed
        self._order_seq = 0
        self.symbols: dict[str, SymbolState] = {}
        self.accounts: dict[str, Account] = {}
        self.started_at = time.time()
        self.last_tick = self.started_at
        self._build_market()
        self._load_accounts()

    # -- market construction -------------------------------------------------

    def _build_market(self) -> None:
        now = datetime.now(timezone.utc)
        for inst in UNIVERSE:
            rng = random.Random(f"{self._seed}:{inst.symbol}")
            daily = self._gen_daily(inst, rng, now)
            prev_close = daily[-1].c
            intraday, day_open, hi, lo, last, vol = self._gen_intraday(
                inst, rng, prev_close, now
            )
            self.symbols[inst.symbol] = SymbolState(
                inst=inst,
                prev_close=prev_close,
                day_open=day_open,
                price=last,
                day_high=hi,
                day_low=lo,
                volume=vol,
                daily=daily,
                intraday=intraday,
            )

    def _gen_daily(self, inst: Instrument, rng: random.Random, now: datetime) -> list[Candle]:
        dt = 1.0 / 252.0
        mu, sigma = inst.drift, inst.volatility
        # Walk *backwards* from the reference price so today lands near it.
        price = inst.price
        prices = [price]
        for _ in range(TRADING_DAYS):
            z = rng.gauss(0, 1)
            step = math.exp((mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z)
            price = price / step
            prices.append(price)
        prices.reverse()

        candles: list[Candle] = []
        for i in range(1, len(prices)):
            o = prices[i - 1]
            c = prices[i]
            spread = abs(c - o) + o * sigma * 0.015 * abs(rng.gauss(0, 1))
            h = max(o, c) + spread * rng.random()
            l = min(o, c) - spread * rng.random()
            day = now - timedelta(days=(len(prices) - 1 - i))
            day = day.replace(hour=20, minute=0, second=0, microsecond=0)
            vol = int(abs(rng.gauss(1, 0.4)) * self._base_volume(inst))
            candles.append(Candle(int(day.timestamp()), o, h, max(h, l, 0.01), c, vol))
        return candles

    def _gen_intraday(
        self, inst: Instrument, rng: random.Random, prev_close: float, now: datetime
    ):
        dt = 1.0 / SECONDS_PER_YEAR * 60  # one minute
        mu, sigma = inst.drift, inst.volatility
        gap = math.exp(sigma * math.sqrt(1 / 252) * rng.gauss(0, 0.4))
        price = prev_close * gap
        day_open = price
        hi = lo = price
        candles: list[Candle] = []
        session_start = now.replace(hour=13, minute=30, second=0, microsecond=0)
        vol_total = 0
        for m in range(INTRADAY_POINTS):
            z = rng.gauss(0, 1)
            o = price
            step = math.exp((mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z)
            price = o * step
            h = max(o, price) * (1 + abs(rng.gauss(0, 0.0006)))
            l = min(o, price) * (1 - abs(rng.gauss(0, 0.0006)))
            hi, lo = max(hi, h), min(lo, l)
            v = int(abs(rng.gauss(1, 0.5)) * self._base_volume(inst) / INTRADAY_POINTS)
            vol_total += v
            t = int((session_start + timedelta(minutes=m)).timestamp())
            candles.append(Candle(t, o, h, l, price, v))
        return candles, day_open, hi, lo, price, vol_total

    @staticmethod
    def _base_volume(inst: Instrument) -> int:
        # Cheaper, higher-vol names trade more shares; rough heuristic only.
        return int(max(2_000_000, 8_000_000_000 / max(inst.price, 1)))

    # -- live ticking --------------------------------------------------------

    def tick(self) -> None:
        """Advance every symbol by one simulated minute."""
        with self._lock:
            now = time.time()
            dt = 1.0 / SECONDS_PER_YEAR * 60
            for st in self.symbols.values():
                inst = st.inst
                z = random.gauss(0, 1)
                step = math.exp(
                    (inst.drift - 0.5 * inst.volatility**2) * dt
                    + inst.volatility * math.sqrt(dt) * z
                )
                o = st.price
                st.price = round(o * step, 2)
                st.day_high = max(st.day_high, st.price)
                st.day_low = min(st.day_low, st.price)
                v = int(abs(random.gauss(1, 0.5)) * self._base_volume(inst) / INTRADAY_POINTS)
                st.volume += v
                h = max(o, st.price)
                l = min(o, st.price)
                st.intraday.append(Candle(int(now), o, h, l, st.price, v))
                if len(st.intraday) > INTRADAY_POINTS:
                    st.intraday.pop(0)
            self.last_tick = now

    # -- quotes / history / search ------------------------------------------

    def quote(self, symbol: str) -> dict | None:
        st = self.symbols.get(symbol.upper())
        if not st:
            return None
        with self._lock:
            change = st.price - st.prev_close
            pct = (change / st.prev_close * 100) if st.prev_close else 0.0
            return {
                "symbol": st.inst.symbol,
                "name": st.inst.name,
                "sector": st.inst.sector,
                "price": round(st.price, 2),
                "change": round(change, 2),
                "changePercent": round(pct, 2),
                "open": round(st.day_open, 2),
                "prevClose": round(st.prev_close, 2),
                "dayHigh": round(st.day_high, 2),
                "dayLow": round(st.day_low, 2),
                "yearHigh": round(max(c.h for c in st.daily), 2),
                "yearLow": round(min(c.l for c in st.daily), 2),
                "volume": st.volume,
                "marketCap": int(st.price * self._shares_out(st.inst)),
            }

    @staticmethod
    def _shares_out(inst: Instrument) -> int:
        return int(max(1, 2_000_000_000_000 / max(inst.price, 1) / 10))

    def quotes(self, symbols: list[str]) -> list[dict]:
        out = []
        for s in symbols:
            q = self.quote(s)
            if q:
                out.append(q)
        return out

    def history(self, symbol: str, range_: str) -> dict | None:
        st = self.symbols.get(symbol.upper())
        if not st:
            return None
        range_ = range_.upper()
        kind, n = RANGE_SPEC.get(range_, RANGE_SPEC["1M"])
        with self._lock:
            if kind == "intraday":
                candles = st.intraday[-n:]
            else:
                candles = st.daily[-n:]
            points = [
                {"t": c.t, "o": round(c.o, 2), "h": round(c.h, 2),
                 "l": round(c.l, 2), "c": round(c.c, 2), "v": c.v}
                for c in candles
            ]
        base = points[0]["c"] if points else st.prev_close
        last = points[-1]["c"] if points else st.price
        return {
            "symbol": st.inst.symbol,
            "range": range_,
            "points": points,
            "change": round(last - base, 2),
            "changePercent": round((last - base) / base * 100, 2) if base else 0.0,
        }

    def search(self, query: str, limit: int = 12) -> list[dict]:
        q = query.strip().upper()
        if not q:
            return []
        scored: list[tuple[int, Instrument]] = []
        for inst in UNIVERSE:
            score = None
            if inst.symbol == q:
                score = 0
            elif inst.symbol.startswith(q):
                score = 1
            elif q in inst.symbol:
                score = 2
            elif q.lower() in inst.name.lower():
                score = 3
            if score is not None:
                scored.append((score, inst))
        scored.sort(key=lambda x: (x[0], x[1].symbol))
        results = []
        for _, inst in scored[:limit]:
            q_ = self.quote(inst.symbol)
            results.append({
                "symbol": inst.symbol,
                "name": inst.name,
                "sector": inst.sector,
                "price": q_["price"] if q_ else inst.price,
                "changePercent": q_["changePercent"] if q_ else 0.0,
            })
        return results

    def movers(self, limit: int = 5) -> dict:
        quotes = [self.quote(s) for s in self.symbols]
        quotes = [q for q in quotes if q]
        gainers = sorted(quotes, key=lambda q: q["changePercent"], reverse=True)[:limit]
        losers = sorted(quotes, key=lambda q: q["changePercent"])[:limit]
        actives = sorted(quotes, key=lambda q: q["volume"], reverse=True)[:limit]
        return {"gainers": gainers, "losers": losers, "actives": actives}

    def market_status(self) -> dict:
        return {
            "open": True,                 # the sim runs 24/7 for practice
            "asOf": datetime.now(timezone.utc).isoformat(),
            "symbols": len(self.symbols),
            "lastTick": self.last_tick,
        }

    # -- accounts / trading --------------------------------------------------

    def _load_accounts(self) -> None:
        data = self._storage.load()
        self._order_seq = data.get("order_seq", 0)
        for name, a in data.get("accounts", {}).items():
            positions = {
                s: Position(s, p["qty"], p["avg_cost"])
                for s, p in a.get("positions", {}).items()
            }
            self.accounts[name] = Account(
                name=name,
                cash=a.get("cash", STARTING_CASH),
                positions=positions,
                watchlist=a.get("watchlist", []),
                created=a.get("created", time.time()),
            )
        self._orders: list[Order] = [
            Order(**o) for o in data.get("orders", [])
        ]
        if "default" not in self.accounts:
            self.get_account("default")

    def _persist(self) -> None:
        data = {
            "order_seq": self._order_seq,
            "accounts": {
                name: {
                    "cash": a.cash,
                    "positions": {
                        s: {"qty": p.qty, "avg_cost": p.avg_cost}
                        for s, p in a.positions.items()
                    },
                    "watchlist": a.watchlist,
                    "created": a.created,
                }
                for name, a in self.accounts.items()
            },
            "orders": [vars(o) for o in self._orders[-500:]],
        }
        self._storage.save(data)

    def get_account(self, name: str) -> Account:
        with self._lock:
            if name not in self.accounts:
                self.accounts[name] = Account(
                    name=name,
                    cash=STARTING_CASH,
                    watchlist=["AAPL", "MSFT", "NVDA", "SPY", "TSLA"],
                )
                self._persist()
            return self.accounts[name]

    def portfolio(self, account: str = "default") -> dict:
        with self._lock:
            acct = self.get_account(account)
            positions = []
            holdings_value = 0.0
            cost_basis = 0.0
            for p in acct.positions.values():
                q = self.quote(p.symbol)
                price = q["price"] if q else p.avg_cost
                mv = price * p.qty
                holdings_value += mv
                cost_basis += p.avg_cost * p.qty
                pl = (price - p.avg_cost) * p.qty
                positions.append({
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avgCost": round(p.avg_cost, 2),
                    "price": round(price, 2),
                    "marketValue": round(mv, 2),
                    "unrealizedPL": round(pl, 2),
                    "unrealizedPLPercent": round((price - p.avg_cost) / p.avg_cost * 100, 2)
                    if p.avg_cost else 0.0,
                    "dayChangePercent": q["changePercent"] if q else 0.0,
                })
            positions.sort(key=lambda x: x["marketValue"], reverse=True)
            equity = acct.cash + holdings_value
            return {
                "account": account,
                "cash": round(acct.cash, 2),
                "holdingsValue": round(holdings_value, 2),
                "equity": round(equity, 2),
                "costBasis": round(cost_basis, 2),
                "totalUnrealizedPL": round(holdings_value - cost_basis, 2),
                "totalUnrealizedPLPercent": round(
                    (holdings_value - cost_basis) / cost_basis * 100, 2
                ) if cost_basis else 0.0,
                "positions": positions,
            }

    def place_order(self, account: str, symbol: str, side: str, qty: float) -> dict:
        symbol = symbol.upper()
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if qty <= 0:
            raise ValueError("qty must be positive")
        st = self.symbols.get(symbol)
        if not st:
            raise ValueError(f"unknown symbol '{symbol}'")

        with self._lock:
            acct = self.get_account(account)
            price = round(st.price, 2)
            notional = round(price * qty, 2)

            if side == "buy":
                if notional > acct.cash + 1e-6:
                    raise ValueError(
                        f"insufficient cash: need ${notional:,.2f}, have ${acct.cash:,.2f}"
                    )
                acct.cash = round(acct.cash - notional, 2)
                pos = acct.positions.get(symbol)
                if pos:
                    total_qty = pos.qty + qty
                    pos.avg_cost = (pos.avg_cost * pos.qty + price * qty) / total_qty
                    pos.qty = total_qty
                else:
                    acct.positions[symbol] = Position(symbol, qty, price)
            else:  # sell
                pos = acct.positions.get(symbol)
                if not pos or pos.qty < qty - 1e-6:
                    have = pos.qty if pos else 0
                    raise ValueError(f"insufficient shares: have {have}, tried to sell {qty}")
                acct.cash = round(acct.cash + notional, 2)
                pos.qty = round(pos.qty - qty, 6)
                if pos.qty <= 1e-6:
                    del acct.positions[symbol]

            self._order_seq += 1
            order = Order(
                id=self._order_seq,
                ts=time.time(),
                account=account,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                notional=notional,
            )
            self._orders.append(order)
            self._persist()
            return vars(order)

    def orders(self, account: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            items = self._orders
            if account:
                items = [o for o in items if o.account == account]
            return [vars(o) for o in items[-limit:][::-1]]

    # -- watchlist -----------------------------------------------------------

    def watchlist(self, account: str = "default") -> list[dict]:
        with self._lock:
            acct = self.get_account(account)
            return self.quotes(acct.watchlist)

    def add_watch(self, symbol: str, account: str = "default") -> list[dict]:
        symbol = symbol.upper()
        if symbol not in self.symbols:
            raise ValueError(f"unknown symbol '{symbol}'")
        with self._lock:
            acct = self.get_account(account)
            if symbol not in acct.watchlist:
                acct.watchlist.append(symbol)
                self._persist()
        return self.watchlist(account)

    def remove_watch(self, symbol: str, account: str = "default") -> list[dict]:
        symbol = symbol.upper()
        with self._lock:
            acct = self.get_account(account)
            if symbol in acct.watchlist:
                acct.watchlist.remove(symbol)
                self._persist()
        return self.watchlist(account)

    def reset_account(self, account: str = "default") -> dict:
        with self._lock:
            self.accounts[account] = Account(
                name=account,
                cash=STARTING_CASH,
                watchlist=["AAPL", "MSFT", "NVDA", "SPY", "TSLA"],
            )
            self._orders = [o for o in self._orders if o.account != account]
            self._persist()
        return self.portfolio(account)
