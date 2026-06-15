"""The MarketSim engine.

In the real-data version this is a thin trading/account layer on top of a live
market-data provider (Yahoo Finance). It:

  * resolves searches to real tickers and tracks any symbol you look up,
  * serves live quotes / history (with a stale-cache fallback when the data
    source is briefly unavailable or rate-limited),
  * computes movers across the set of tracked symbols,
  * runs paper trading (cash, positions, orders, watchlists) that fills at the
    real current price,
  * persists user + tracked-symbol state to disk.

The GUI and the terminal CLI both talk to this engine through the HTTP API, so
humans and AI agents trade the same live market.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .instruments import DEFAULT_SYMBOLS, DEFAULT_WATCHLIST
from .providers import ProviderError, make_provider
from .storage import Storage

STARTING_CASH = 100_000.0
QUOTE_SAVE_INTERVAL = 20.0  # throttle disk writes of the warmed quote cache


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
    side: str
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
    def __init__(self, storage: Storage | None = None, provider=None):
        self._lock = threading.RLock()
        self._storage = storage or Storage()
        self._provider = provider or make_provider()
        self._order_seq = 0
        self.tracked: set[str] = set()
        self.meta: dict[str, dict] = {}          # symbol -> {name, sector, exchange}
        self._last_quote: dict[str, dict] = {}    # last good quote for stale fallback
        self._last_quote_save = 0.0
        self._warmed_once = False
        self.accounts: dict[str, Account] = {}
        self._orders: list[Order] = []
        self.started_at = time.time()
        self._load()
        self._seed_defaults()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        data = self._storage.load()
        self._order_seq = data.get("order_seq", 0)
        self.tracked = set(data.get("tracked", []))
        self.meta = data.get("meta", {})
        # Restore last-known quotes so a restart during an outage isn't blank.
        self._last_quote = data.get("quotes", {})
        for name, a in data.get("accounts", {}).items():
            self.accounts[name] = Account(
                name=name,
                cash=a.get("cash", STARTING_CASH),
                positions={
                    s: Position(s, p["qty"], p["avg_cost"])
                    for s, p in a.get("positions", {}).items()
                },
                watchlist=a.get("watchlist", []),
                created=a.get("created", time.time()),
            )
        self._orders = [Order(**o) for o in data.get("orders", [])]

    def _persist(self) -> None:
        self._last_quote_save = time.time()
        self._storage.save({
            "order_seq": self._order_seq,
            "tracked": sorted(self.tracked),
            "meta": self.meta,
            "quotes": self._last_quote,
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
        })

    def _persist_quotes(self) -> None:
        """Throttled persistence of just the warmed quote cache."""
        if time.time() - self._last_quote_save >= QUOTE_SAVE_INTERVAL:
            with self._lock:
                self._persist()

    def _seed_defaults(self) -> None:
        changed = False
        if not self.tracked:
            for sym, name in DEFAULT_SYMBOLS:
                self.tracked.add(sym)
                self.meta.setdefault(sym, {"name": name, "sector": "", "exchange": ""})
            changed = True
        if "default" not in self.accounts:
            self.accounts["default"] = Account(
                name="default", cash=STARTING_CASH, watchlist=list(DEFAULT_WATCHLIST)
            )
            changed = True
        if changed:
            self._persist()

    # -- metadata / tracking -------------------------------------------------

    def _remember_meta(self, symbol: str, *, name=None, sector=None, exchange=None) -> None:
        m = self.meta.setdefault(symbol, {"name": symbol, "sector": "", "exchange": ""})
        if name:
            m["name"] = name
        if sector:
            m["sector"] = sector
        if exchange:
            m["exchange"] = exchange

    def track(self, symbol: str) -> dict:
        """Validate a symbol against the data source and track it from now on."""
        symbol = symbol.upper()
        q = self._provider.quote(symbol)  # raises ProviderError if unknown/unreachable
        with self._lock:
            newly = symbol not in self.tracked
            self.tracked.add(symbol)
            self._remember_meta(symbol, name=q.get("name"), exchange=q.get("exchange"))
            self._cache_quote(symbol, q)
            if newly:
                self._persist()
        return self._decorate(symbol, q, stale=False)

    def untrack(self, symbol: str) -> None:
        symbol = symbol.upper()
        with self._lock:
            if symbol in self.tracked:
                self.tracked.discard(symbol)
                self._persist()

    def get_tracked(self) -> list[dict]:
        with self._lock:
            return [
                {"symbol": s, **self.meta.get(s, {"name": s, "sector": "", "exchange": ""})}
                for s in sorted(self.tracked)
            ]

    # -- quotes --------------------------------------------------------------

    def _cache_quote(self, symbol: str, q: dict) -> None:
        self._last_quote[symbol] = {**q, "_cachedAt": time.time()}

    def _decorate(self, symbol: str, q: dict, *, stale: bool) -> dict:
        m = self.meta.get(symbol, {})
        out = dict(q)
        if not out.get("sector") and m.get("sector"):
            out["sector"] = m["sector"]
        if not out.get("name") and m.get("name"):
            out["name"] = m["name"]
        out["tracked"] = symbol in self.tracked
        out["stale"] = stale
        return out

    def quote(self, symbol: str, *, no_cache: bool = False, auto_track: bool = True) -> dict | None:
        symbol = symbol.upper()
        try:
            q = self._provider.quote(symbol, no_cache=no_cache)
        except ProviderError:
            last = self._last_quote.get(symbol)
            if last is not None:
                return self._decorate(symbol, last, stale=True)
            return None
        with self._lock:
            self._remember_meta(symbol, name=q.get("name"), exchange=q.get("exchange"))
            self._cache_quote(symbol, q)
            if auto_track and symbol not in self.tracked:
                # Looking a symbol up tracks it from then on.
                self.tracked.add(symbol)
                self._persist()
        return self._decorate(symbol, q, stale=False)

    def quotes(self, symbols: list[str]) -> list[dict]:
        out = []
        for s in symbols:
            q = self.quote(s, auto_track=False)
            if q:
                out.append(q)
        return out

    def history(self, symbol: str, range_: str) -> dict | None:
        try:
            return self._provider.history(symbol, range_)
        except ProviderError:
            return None

    def search(self, query: str, limit: int = 12) -> list[dict]:
        try:
            results = self._provider.search(query, limit)
        except ProviderError:
            return []
        with self._lock:
            for r in results:
                self._remember_meta(
                    r["symbol"], name=r.get("name"),
                    sector=r.get("sector"), exchange=r.get("exchange"),
                )
                r["tracked"] = r["symbol"] in self.tracked
        return results

    def movers(self, limit: int = 5) -> dict:
        quotes = self.quotes(sorted(self.tracked))
        quotes = [q for q in quotes if q.get("changePercent") is not None]
        gainers = sorted(quotes, key=lambda q: q["changePercent"], reverse=True)[:limit]
        losers = sorted(quotes, key=lambda q: q["changePercent"])[:limit]
        actives = sorted(quotes, key=lambda q: q.get("volume") or 0, reverse=True)[:limit]
        return {"gainers": gainers, "losers": losers, "actives": actives}

    def refresh_tracked(self, batch: list[str] | None = None) -> int:
        """Warm the quote cache for tracked symbols (used by the server ticker)."""
        symbols = batch if batch is not None else sorted(self.tracked)
        ok = 0
        for s in symbols:
            try:
                q = self._provider.quote(s)
                with self._lock:
                    self._cache_quote(s, q)
                    self._remember_meta(s, name=q.get("name"), exchange=q.get("exchange"))
                ok += 1
            except ProviderError:
                continue
        if ok:
            # Persist immediately the first time we have live quotes (so a quick
            # restart still has prices), then throttle subsequent writes.
            if not self._warmed_once:
                self._warmed_once = True
                with self._lock:
                    self._persist()
            else:
                self._persist_quotes()
        return ok

    def flush(self) -> None:
        """Persist current state (e.g. on graceful shutdown)."""
        with self._lock:
            self._persist()

    def market_status(self) -> dict:
        state = "UNKNOWN"
        ref = self._last_quote.get("SPY") or next(iter(self._last_quote.values()), None)
        if ref:
            state = ref.get("marketState", "UNKNOWN")
        return {
            "source": getattr(self._provider, "source_name", "unknown"),
            "marketState": state,
            "tracked": len(self.tracked),
            "asOf": time.time(),
            "rateLimited": self._provider.in_cooldown(),
            "cooldownSeconds": round(self._provider.cooldown_remaining(), 0),
        }

    # -- accounts / trading --------------------------------------------------

    def get_account(self, name: str) -> Account:
        with self._lock:
            if name not in self.accounts:
                self.accounts[name] = Account(
                    name=name, cash=STARTING_CASH, watchlist=list(DEFAULT_WATCHLIST)
                )
                self._persist()
            return self.accounts[name]

    def _price_for(self, symbol: str) -> float:
        q = self.quote(symbol, auto_track=False)
        if not q or q.get("price") is None:
            raise ValueError(f"no price available for '{symbol}'")
        return float(q["price"])

    def portfolio(self, account: str = "default") -> dict:
        with self._lock:
            acct = self.get_account(account)
            positions = []
            holdings_value = 0.0
            cost_basis = 0.0
            for p in acct.positions.values():
                q = self.quote(p.symbol, auto_track=False)
                price = q["price"] if q and q.get("price") is not None else p.avg_cost
                mv = price * p.qty
                holdings_value += mv
                cost_basis += p.avg_cost * p.qty
                positions.append({
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avgCost": round(p.avg_cost, 2),
                    "price": round(price, 2),
                    "marketValue": round(mv, 2),
                    "unrealizedPL": round((price - p.avg_cost) * p.qty, 2),
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

        price = round(self._price_for(symbol), 2)
        notional = round(price * qty, 2)
        with self._lock:
            acct = self.get_account(account)
            if side == "buy":
                if notional > acct.cash + 1e-6:
                    raise ValueError(
                        f"insufficient cash: need ${notional:,.2f}, have ${acct.cash:,.2f}"
                    )
                acct.cash = round(acct.cash - notional, 2)
                pos = acct.positions.get(symbol)
                if pos:
                    total = pos.qty + qty
                    pos.avg_cost = (pos.avg_cost * pos.qty + price * qty) / total
                    pos.qty = total
                else:
                    acct.positions[symbol] = Position(symbol, qty, price)
                self.tracked.add(symbol)
            else:
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
                id=self._order_seq, ts=time.time(), account=account, symbol=symbol,
                side=side, qty=qty, price=price, notional=notional,
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
            symbols = list(acct.watchlist)
        return self.quotes(symbols)

    def add_watch(self, symbol: str, account: str = "default") -> list[dict]:
        symbol = symbol.upper()
        q = self.quote(symbol)  # validates + tracks
        if not q:
            raise ValueError(f"unknown or unavailable symbol '{symbol}'")
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
                name=account, cash=STARTING_CASH, watchlist=list(DEFAULT_WATCHLIST)
            )
            self._orders = [o for o in self._orders if o.account != account]
            self._persist()
        return self.portfolio(account)
