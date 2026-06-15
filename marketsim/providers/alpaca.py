"""Alpaca market-data provider.

Uses Alpaca's REST API (key + secret, no SDK required) to give MarketSim a
reliable, real-time-ish live feed:

  * search   trading  /v2/assets            -> resolve queries to real tickers
  * quote    data     /v2/stocks/{s}/snapshot -> live price + day/prev-close stats
  * history  data     /v2/stocks/{s}/bars     -> OHLC candles
  * clock    trading  /v2/clock             -> market open/closed

The free data plan serves the IEX feed (one venue), so the last price can differ
slightly from the full consolidated tape — but it's real and not rate-limited
the way Yahoo's unofficial endpoints are.

Credentials come from the environment:
    APCA_API_KEY_ID, APCA_API_SECRET_KEY
    APCA_API_BASE_URL   (default https://paper-api.alpaca.markets)
    APCA_DATA_BASE_URL  (default https://data.alpaca.markets)
    ALPACA_FEED         (default iex)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .base import ProviderError


class AlpacaError(ProviderError):
    """Raised when Alpaca is unreachable, unauthorised, or has no data."""


# range -> (alpaca timeframe, lookback timedelta)
_RANGE_MAP = {
    "1D": ("5Min", timedelta(days=1)),
    "1W": ("1Hour", timedelta(days=7)),
    "1M": ("1Day", timedelta(days=31)),
    "3M": ("1Day", timedelta(days=93)),
    "6M": ("1Day", timedelta(days=186)),
    "1Y": ("1Day", timedelta(days=366)),
}

# Major venues ranked ahead of OTC in search results.
_EXCHANGE_RANK = {"NASDAQ": 0, "NYSE": 0, "ARCA": 1, "BATS": 1, "AMEX": 1, "OTC": 5}


class _TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            hit = self._data.get(key)
            if hit and (time.time() - hit[0]) < self.ttl:
                return hit[1]
        return None

    def put(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)


def _to_epoch(s) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    if not s:
        return int(time.time())
    s = s.strip().replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)  # trim sub-microsecond precision
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return int(time.time())


def _r(v):
    return round(v, 2) if isinstance(v, (int, float)) else v


class AlpacaProvider:
    source_name = "Alpaca (IEX)"

    def __init__(self, quote_ttl: float = 8.0, hist_ttl: float = 60.0,
                 clock_ttl: float = 30.0, assets_ttl: float = 21_600.0):
        self._key = os.environ.get("APCA_API_KEY_ID", "")
        self._secret = os.environ.get("APCA_API_SECRET_KEY", "")
        if not self._key or not self._secret:
            raise AlpacaError(
                "Alpaca credentials missing: set APCA_API_KEY_ID and APCA_API_SECRET_KEY"
            )
        self._trading = os.environ.get(
            "APCA_API_BASE_URL", "https://paper-api.alpaca.markets"
        ).rstrip("/")
        self._data = os.environ.get(
            "APCA_DATA_BASE_URL", "https://data.alpaca.markets"
        ).rstrip("/")
        self._feed = os.environ.get("ALPACA_FEED", "iex")

        self._quote_cache = _TTLCache(quote_ttl)
        self._hist_cache = _TTLCache(hist_ttl)
        self._clock_cache = _TTLCache(clock_ttl)
        self._assets_ttl = assets_ttl
        self._asset_meta: dict[str, dict] = {}   # symbol -> {name, exchange, tradable}
        self._assets_loaded_at = 0.0
        self._assets_lock = threading.Lock()
        self._cooldown_until = 0.0

    # -- cooldown interface (parity with YahooProvider) ----------------------

    def in_cooldown(self) -> bool:
        return time.time() < self._cooldown_until

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.time())

    # -- HTTP ----------------------------------------------------------------

    def _get(self, base: str, path: str, params: dict | None = None, timeout: float = 12.0):
        if self.in_cooldown():
            raise AlpacaError(f"alpaca cooldown active ({self.cooldown_remaining():.0f}s left)")
        url = base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                self._cooldown_until = time.time() + 30.0
                raise AlpacaError("alpaca rate-limited (429); cooling down 30s") from None
            if exc.code in (401, 403):
                raise AlpacaError(f"alpaca auth failed ({exc.code}); check API keys") from None
            try:
                detail = json.loads(exc.read().decode()).get("message", str(exc))
            except Exception:
                detail = str(exc)
            raise AlpacaError(f"alpaca {path}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AlpacaError(f"alpaca request failed for {path}: {exc}") from None

    # -- assets / metadata ---------------------------------------------------

    def _ensure_assets(self) -> None:
        with self._assets_lock:
            if self._asset_meta and (time.time() - self._assets_loaded_at) < self._assets_ttl:
                return
            data = self._get(self._trading, "/v2/assets",
                             {"status": "active", "asset_class": "us_equity"})
            meta = {}
            for a in data:
                sym = a.get("symbol")
                if not sym:
                    continue
                meta[sym] = {
                    "name": a.get("name") or sym,
                    "exchange": a.get("exchange") or "",
                    "tradable": bool(a.get("tradable")),
                }
            self._asset_meta = meta
            self._assets_loaded_at = time.time()

    def _asset(self, symbol: str) -> dict:
        symbol = symbol.upper()
        meta = self._asset_meta.get(symbol)
        if meta:
            return meta
        # Cheap single-asset lookup if the full list isn't loaded yet.
        try:
            a = self._get(self._trading, f"/v2/assets/{urllib.parse.quote(symbol)}")
            meta = {
                "name": a.get("name") or symbol,
                "exchange": a.get("exchange") or "",
                "tradable": bool(a.get("tradable")),
            }
            self._asset_meta[symbol] = meta
            return meta
        except AlpacaError:
            return {"name": symbol, "exchange": "", "tradable": False}

    # -- clock ---------------------------------------------------------------

    def _market_open(self) -> bool | None:
        cached = self._clock_cache.get("clock")
        if cached is None:
            try:
                clock = self._get(self._trading, "/v2/clock")
                cached = bool(clock.get("is_open"))
                self._clock_cache.put("clock", cached)
            except AlpacaError:
                return None
        return cached

    # -- search --------------------------------------------------------------

    def search(self, query: str, limit: int = 12) -> list[dict]:
        query = query.strip().upper()
        if not query:
            return []
        self._ensure_assets()
        # Tokenise so multi-word queries ("coca cola") match hyphenated/spaced
        # company names; symbol matching uses the space-free query.
        tokens = [t for t in query.lower().split() if t]
        sym_query = query.replace(" ", "")
        scored: list[tuple[tuple, dict]] = []
        for sym, meta in self._asset_meta.items():
            name = meta["name"]
            name_l = name.lower()
            if query == sym:
                rank = 0
            elif sym.startswith(sym_query):
                rank = 1
            elif sym_query in sym:
                rank = 2
            elif tokens and all(t in name_l for t in tokens):
                rank = 3
            else:
                continue
            ex_rank = _EXCHANGE_RANK.get(meta["exchange"], 4)
            tradable_rank = 0 if meta["tradable"] else 1
            scored.append(((rank, tradable_rank, ex_rank, sym), {
                "symbol": sym,
                "name": name,
                "sector": "",
                "exchange": meta["exchange"],
                "type": "EQUITY",
            }))
        scored.sort(key=lambda x: x[0])
        return [item for _, item in scored[:limit]]

    # -- quote ---------------------------------------------------------------

    def quote(self, symbol: str, no_cache: bool = False) -> dict:
        symbol = symbol.upper()
        if not no_cache:
            cached = self._quote_cache.get(symbol)
            if cached is not None:
                return cached
        snap = self._get(self._data, f"/v2/stocks/{urllib.parse.quote(symbol)}/snapshot",
                         {"feed": self._feed})
        if not snap or not any(snap.get(k) for k in
                               ("latestTrade", "dailyBar", "minuteBar", "prevDailyBar")):
            raise AlpacaError(f"no data for '{symbol}'")
        q = self._normalise(symbol, snap)
        self._quote_cache.put(symbol, q)
        return q

    def _normalise(self, symbol: str, snap: dict) -> dict:
        latest = snap.get("latestTrade") or {}
        daily = snap.get("dailyBar") or {}
        prev = snap.get("prevDailyBar") or {}
        minute = snap.get("minuteBar") or {}

        price = latest.get("p") or minute.get("c") or daily.get("c")
        if price is None:
            raise AlpacaError(f"no price for '{symbol}'")

        is_open = self._market_open()
        # When the regular session is live, "previous close" is the prior day's
        # close; otherwise the most recently completed session is the day bar.
        if is_open:
            prev_close = prev.get("c") or daily.get("c")
        else:
            prev_close = daily.get("c") or prev.get("c")

        change = (price - prev_close) if prev_close else 0.0
        pct = (change / prev_close * 100) if prev_close else 0.0
        meta = self._asset(symbol)
        return {
            "symbol": symbol,
            "name": meta["name"],
            "sector": "",
            "exchange": meta["exchange"],
            "currency": "USD",
            "price": _r(price),
            "change": _r(change),
            "changePercent": round(pct, 2),
            "open": _r(daily.get("o")),
            "prevClose": _r(prev_close),
            "dayHigh": _r(daily.get("h")),
            "dayLow": _r(daily.get("l")),
            "yearHigh": None,   # not in snapshot; left blank (GUI/CLI show "—")
            "yearLow": None,
            "volume": daily.get("v") or 0,
            "marketState": "OPEN" if is_open else ("CLOSED" if is_open is False else "UNKNOWN"),
            "asOf": _to_epoch(latest.get("t")),
        }

    # -- history -------------------------------------------------------------

    def history(self, symbol: str, range_: str) -> dict:
        symbol = symbol.upper()
        range_ = range_.upper()
        if range_ not in _RANGE_MAP:
            range_ = "1M"
        cache_key = f"{symbol}|{range_}"
        cached = self._hist_cache.get(cache_key)
        if cached is not None:
            return cached

        timeframe, lookback = _RANGE_MAP[range_]
        start = datetime.now(timezone.utc) - lookback
        params = {
            "timeframe": timeframe,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "feed": self._feed,
            "limit": 10000,
            "adjustment": "raw",
        }
        data = self._get(self._data, f"/v2/stocks/{urllib.parse.quote(symbol)}/bars", params)
        bars = data.get("bars") or []
        points = [{
            "t": _to_epoch(b.get("t")),
            "o": _r(b.get("o")), "h": _r(b.get("h")),
            "l": _r(b.get("l")), "c": _r(b.get("c")),
            "v": b.get("v") or 0,
        } for b in bars if b.get("c") is not None]
        if not points:
            raise AlpacaError(f"no history for '{symbol}'")
        base = points[0]["c"]
        last = points[-1]["c"]
        out = {
            "symbol": symbol,
            "range": range_,
            "points": points,
            "change": round(last - base, 2),
            "changePercent": round((last - base) / base * 100, 2) if base else 0.0,
        }
        self._hist_cache.put(cache_key, out)
        return out
