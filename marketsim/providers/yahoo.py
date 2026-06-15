"""Yahoo Finance data provider.

Uses Yahoo's public (unofficial, key-free) endpoints:

  * search   v1/finance/search        -> resolve a query to real tickers
  * chart    v8/finance/chart/{sym}    -> live quote meta + OHLC candles

Everything is normalised into plain dicts the rest of MarketSim consumes. A
small TTL cache keeps us well under Yahoo's rate limits while still feeling
live, and a ``no_cache`` path exists for the price-verification monitor which
must always fetch an independent, fresh "truth" value.

This module talks to a real data source, so it needs network access.
"""

from __future__ import annotations

import http.cookiejar
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import ProviderError

# query1/query2 are interchangeable; we fail over between them.
_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Yahoo aggressively rate-limits (HTTP 429) clients that don't carry a session
# cookie. We prime a cookie jar once by visiting the homepage, reuse that
# opener for every call, and back off on 429 — the same trick yfinance uses.
_session_lock = threading.Lock()
_opener: urllib.request.OpenerDirector | None = None
_primed_at = 0.0
_PRIME_TTL = 1800.0  # re-prime cookies every 30 min

# When Yahoo rate-limits us, stop making *any* network calls for a while and let
# callers fall back to last-known values. This breaks the 429 feedback loop
# where retries make the throttling worse.
_COOLDOWN_SECONDS = 120.0
_cooldown_until = 0.0


def in_cooldown() -> bool:
    return time.time() < _cooldown_until


def cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - time.time())


def _opener_get() -> urllib.request.OpenerDirector:
    global _opener, _primed_at
    with _session_lock:
        if _opener is not None and (time.time() - _primed_at) < _PRIME_TTL:
            return _opener
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [
            ("User-Agent", _UA),
            ("Accept", "text/html,application/json,*/*"),
            ("Accept-Language", "en-US,en;q=0.9"),
        ]
        for prime_url in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
            try:
                opener.open(prime_url, timeout=10).read(1)
            except Exception:
                pass  # cookies may still have been set; some hosts 404 on purpose
        _opener = opener
        _primed_at = time.time()
        return _opener

# Yahoo range -> (range, interval) for the chart endpoint.
_RANGE_MAP = {
    "1D": ("1d", "1m"),
    "1W": ("5d", "15m"),
    "1M": ("1mo", "1d"),
    "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1d"),
}


class YahooError(ProviderError):
    """Raised when Yahoo cannot be reached or returns no usable data."""


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


def _http_get_json(path: str, params: dict, timeout: float = 10.0, retries: int = 3) -> dict:
    global _opener, _cooldown_until
    # If we're in a rate-limit cooldown, don't touch the network at all.
    if in_cooldown():
        raise YahooError(f"yahoo cooldown active ({cooldown_remaining():.0f}s left)")
    query = urllib.parse.urlencode(params)
    last_err: Exception | None = None
    for attempt in range(retries):
        opener = _opener_get()
        rate_limited = False
        for host in _HOSTS:
            url = f"{host}{path}?{query}"
            try:
                with opener.open(url, timeout=timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code == 429:
                    rate_limited = True
                    break
                continue
            except (urllib.error.URLError, TimeoutError) as exc:
                last_err = exc
                continue
        if rate_limited:
            _opener = None  # drop the session so the next attempt re-primes cookies
            # Enter a cooldown immediately rather than burning all retries.
            _cooldown_until = time.time() + _COOLDOWN_SECONDS
            raise YahooError(f"yahoo rate-limited (429); cooling down {_COOLDOWN_SECONDS:.0f}s")
        # Exponential backoff with jitter before the next attempt.
        time.sleep(min(8.0, 2 ** attempt) + random.random())
    raise YahooError(f"yahoo request failed for {path}: {last_err}")


class YahooProvider:
    source_name = "Yahoo Finance"

    def __init__(self, quote_ttl: float = 30.0, hist_ttl: float = 180.0, search_ttl: float = 600.0):
        self._quote_cache = _TTLCache(quote_ttl)
        self._hist_cache = _TTLCache(hist_ttl)
        self._search_cache = _TTLCache(search_ttl)

    def in_cooldown(self) -> bool:
        return in_cooldown()

    def cooldown_remaining(self) -> float:
        return cooldown_remaining()

    # -- search --------------------------------------------------------------

    def search(self, query: str, limit: int = 12) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        cache_key = f"{query.lower()}|{limit}"
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return cached
        data = _http_get_json(
            "/v1/finance/search",
            {"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0},
        )
        results = []
        for q in data.get("quotes", []):
            sym = q.get("symbol")
            qtype = (q.get("quoteType") or "").upper()
            if not sym or qtype not in ("EQUITY", "ETF"):
                continue
            results.append({
                "symbol": sym,
                "name": q.get("longname") or q.get("shortname") or sym,
                "sector": q.get("sectorDisp") or q.get("sector") or q.get("typeDisp") or "",
                "exchange": q.get("exchDisp") or q.get("exchange") or "",
                "type": qtype,
            })
            if len(results) >= limit:
                break
        self._search_cache.put(cache_key, results)
        return results

    # -- quote ---------------------------------------------------------------

    def quote(self, symbol: str, no_cache: bool = False) -> dict:
        symbol = symbol.upper()
        if not no_cache:
            cached = self._quote_cache.get(symbol)
            if cached is not None:
                return cached
        data = _http_get_json(
            f"/v8/finance/chart/{urllib.parse.quote(symbol)}",
            {"range": "1d", "interval": "1m", "includePrePost": "false"},
        )
        result = (data.get("chart") or {}).get("result")
        if not result:
            err = (data.get("chart") or {}).get("error") or {}
            raise YahooError(err.get("description") or f"no data for '{symbol}'")
        q = self._normalise_quote(result[0])
        self._quote_cache.put(symbol, q)
        return q

    def _normalise_quote(self, result: dict) -> dict:
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None:
            raise YahooError("missing regularMarketPrice")

        # Day open: first non-null open in the intraday series (chart meta has
        # no regularMarketOpen field).
        day_open = prev
        try:
            opens = result["indicators"]["quote"][0]["open"]
            for o in opens:
                if o is not None:
                    day_open = o
                    break
        except (KeyError, IndexError, TypeError):
            pass

        change = (price - prev) if prev is not None else 0.0
        pct = (change / prev * 100) if prev else 0.0
        return {
            "symbol": meta.get("symbol"),
            "name": meta.get("longName") or meta.get("shortName") or meta.get("symbol"),
            "sector": "",  # not in chart meta; filled from search metadata upstream
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
            "currency": meta.get("currency") or "USD",
            "price": round(price, 2),
            "change": round(change, 2),
            "changePercent": round(pct, 2),
            "open": round(day_open, 2) if day_open is not None else None,
            "prevClose": round(prev, 2) if prev is not None else None,
            "dayHigh": _r(meta.get("regularMarketDayHigh")),
            "dayLow": _r(meta.get("regularMarketDayLow")),
            "yearHigh": _r(meta.get("fiftyTwoWeekHigh")),
            "yearLow": _r(meta.get("fiftyTwoWeekLow")),
            "volume": meta.get("regularMarketVolume") or 0,
            "marketState": self._market_state(meta),
            "asOf": meta.get("regularMarketTime") or int(time.time()),
        }

    @staticmethod
    def _market_state(meta: dict) -> str:
        period = meta.get("currentTradingPeriod", {}).get("regular", {})
        start, end = period.get("start"), period.get("end")
        now = time.time()
        if start and end:
            return "OPEN" if start <= now <= end else "CLOSED"
        return "UNKNOWN"

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
        yr, yi = _RANGE_MAP[range_]
        data = _http_get_json(
            f"/v8/finance/chart/{urllib.parse.quote(symbol)}",
            {"range": yr, "interval": yi, "includePrePost": "false"},
        )
        result = (data.get("chart") or {}).get("result")
        if not result:
            raise YahooError(f"no history for '{symbol}'")
        res = result[0]
        ts = res.get("timestamp") or []
        quote = (res.get("indicators", {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        points = []
        for i, t in enumerate(ts):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            points.append({
                "t": t,
                "o": _r(opens[i] if i < len(opens) else c),
                "h": _r(highs[i] if i < len(highs) else c),
                "l": _r(lows[i] if i < len(lows) else c),
                "c": _r(c),
                "v": vols[i] if i < len(vols) and vols[i] is not None else 0,
            })
        base = points[0]["c"] if points else 0.0
        last = points[-1]["c"] if points else 0.0
        out = {
            "symbol": symbol,
            "range": range_,
            "points": points,
            "change": round(last - base, 2),
            "changePercent": round((last - base) / base * 100, 2) if base else 0.0,
        }
        self._hist_cache.put(cache_key, out)
        return out


def _r(v):
    return round(v, 2) if isinstance(v, (int, float)) else v
