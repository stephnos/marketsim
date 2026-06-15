"""Continuous price-verification monitor.

The monitor periodically compares, for each tracked symbol:

  * the price the MarketSim app *serves*, and
  * an independent *fresh scrape* ("truth") fetched straight from the data
    source, bypassing all caches,

and asserts they agree within a tolerance. Mismatches/staleness/errors are
counted and logged so you can continuously confirm the app's prices line up
with the real market.

Two wirings are provided:

  * :func:`inprocess_monitor` — runs inside the server; served price comes from
    the engine's cache, truth from a fresh provider fetch. Detects stale or
    broken serving and powers the ``/api/health/prices`` endpoint.
  * :func:`http_monitor` — runs standalone (e.g. ``marketsim monitor``); served
    price comes from the running app's HTTP API, truth from its own scraper.
    This exercises the whole pipeline end-to-end.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable

from .providers import make_provider

log = logging.getLogger("marketsim.monitor")


@dataclass
class CheckResult:
    ts: float
    symbol: str
    served: float | None
    truth: float | None
    diff: float | None
    diffPercent: float | None
    tolerancePercent: float
    status: str  # ok | mismatch | stale | error
    detail: str = ""


class PriceMonitor:
    def __init__(
        self,
        symbols_fn: Callable[[], list[str]],
        served_fn: Callable[[str], dict],   # -> {"price": float|None, "stale": bool}
        truth_fn: Callable[[str], float],   # -> float (raises on failure)
        *,
        tolerance_pct: float = 1.0,
        abs_floor: float = 0.05,
        interval: float = 60.0,
        history: int = 500,
        max_per_cycle: int = 0,
    ):
        self._symbols_fn = symbols_fn
        self._served_fn = served_fn
        self._truth_fn = truth_fn
        self.tolerance_pct = tolerance_pct
        self.abs_floor = abs_floor
        self.interval = interval
        self.max_per_cycle = max_per_cycle  # 0 == check all symbols each cycle
        self._cursor = 0
        self._results: deque[CheckResult] = deque(maxlen=history)
        self._by_symbol: dict[str, CheckResult] = {}
        self.counts = {"checks": 0, "ok": 0, "mismatch": 0, "stale": 0, "error": 0}
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.last_run: float | None = None

    # -- a single comparison -------------------------------------------------

    def check_symbol(self, symbol: str) -> CheckResult:
        ts = time.time()
        try:
            served = self._served_fn(symbol)
        except Exception as exc:  # noqa: BLE001 - any failure is an error result
            return CheckResult(ts, symbol, None, None, None, None,
                               self.tolerance_pct, "error", f"served: {exc}")
        try:
            truth = float(self._truth_fn(symbol))
        except Exception as exc:  # noqa: BLE001
            return CheckResult(ts, symbol, served.get("price"), None, None, None,
                               self.tolerance_pct, "error", f"truth: {exc}")

        served_price = served.get("price")
        if served_price is None:
            return CheckResult(ts, symbol, None, truth, None, None,
                               self.tolerance_pct, "error", "served price is None")
        if served.get("stale"):
            return CheckResult(ts, symbol, served_price, truth, None, None,
                               self.tolerance_pct, "stale", "served price is stale")

        diff = abs(served_price - truth)
        diff_pct = (diff / truth * 100) if truth else 0.0
        allowed = max(self.tolerance_pct / 100.0 * truth, self.abs_floor)
        status = "ok" if diff <= allowed else "mismatch"
        detail = "" if status == "ok" else (
            f"served {served_price} vs truth {truth} "
            f"(\u0394 {diff:.2f}, {diff_pct:.2f}% > {self.tolerance_pct:.2f}%)"
        )
        return CheckResult(ts, symbol, round(served_price, 2), round(truth, 2),
                           round(diff, 4), round(diff_pct, 4),
                           self.tolerance_pct, status, detail)

    def _record(self, r: CheckResult) -> None:
        with self._lock:
            self._results.append(r)
            self._by_symbol[r.symbol] = r
            self.counts["checks"] += 1
            self.counts[r.status] = self.counts.get(r.status, 0) + 1
            self.last_run = r.ts
        if r.status == "mismatch":
            log.warning("PRICE MISMATCH %s: %s", r.symbol, r.detail)
        elif r.status == "error":
            log.error("price check error %s: %s", r.symbol, r.detail)
        elif r.status == "stale":
            log.info("price stale %s", r.symbol)
        else:
            log.debug("price ok %s served=%s truth=%s", r.symbol, r.served, r.truth)

    def _next_batch(self) -> list[str]:
        symbols = self._symbols_fn()
        if not self.max_per_cycle or len(symbols) <= self.max_per_cycle:
            return symbols
        # Rotate through the symbol set a few at a time.
        start = self._cursor % len(symbols)
        batch = (symbols + symbols)[start:start + self.max_per_cycle]
        self._cursor = (start + self.max_per_cycle) % len(symbols)
        return batch

    def check_once(self) -> list[CheckResult]:
        out = []
        for symbol in self._next_batch():
            r = self.check_symbol(symbol)
            self._record(r)
            out.append(r)
        return out

    def run(self, stop_event: threading.Event) -> None:
        log.info("price monitor started (tolerance %.2f%%, interval %.0fs)",
                 self.tolerance_pct, self.interval)
        while not stop_event.is_set():
            try:
                self.check_once()
            except Exception:  # noqa: BLE001 - never let the monitor thread die
                log.exception("monitor cycle failed")
            stop_event.wait(self.interval)

    # -- reporting -----------------------------------------------------------

    def summary(self) -> dict:
        with self._lock:
            checks = self.counts["checks"]
            verified = self.counts["ok"]
            pass_rate = round(verified / checks * 100, 2) if checks else None
            recent = [asdict(r) for r in list(self._results)[-25:][::-1]]
            per_symbol = {s: asdict(r) for s, r in self._by_symbol.items()}
            mismatches = [asdict(r) for r in self._results if r.status == "mismatch"][-10:]
            return {
                "healthy": self.counts["mismatch"] == 0 and self.counts["error"] == 0,
                "tolerancePercent": self.tolerance_pct,
                "intervalSeconds": self.interval,
                "uptimeSeconds": round(time.time() - self.started_at, 1),
                "lastRun": self.last_run,
                "counts": dict(self.counts),
                "passRate": pass_rate,
                "perSymbol": per_symbol,
                "recentMismatches": mismatches,
                "recent": recent,
            }


# ---- wirings --------------------------------------------------------------

def inprocess_monitor(engine, *, tolerance_pct: float = 1.0, interval: float = 60.0,
                      max_per_cycle: int = 0) -> PriceMonitor:
    def served(symbol: str) -> dict:
        q = engine.quote(symbol, auto_track=False)
        if not q:
            return {"price": None, "stale": True}
        return {"price": q.get("price"), "stale": q.get("stale", False)}

    def truth(symbol: str) -> float:
        return engine._provider.quote(symbol, no_cache=True)["price"]

    return PriceMonitor(
        symbols_fn=lambda: sorted(engine.tracked),
        served_fn=served,
        truth_fn=truth,
        tolerance_pct=tolerance_pct,
        interval=interval,
        max_per_cycle=max_per_cycle,
    )


def http_monitor(base_url: str, *, tolerance_pct: float = 1.0, interval: float = 60.0) -> PriceMonitor:
    base = base_url.rstrip("/")
    provider = make_provider()  # independent fresh-truth source

    def _get(path: str):
        req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    def symbols() -> list[str]:
        return [t["symbol"] for t in _get("/api/tracked")]

    def served(symbol: str) -> dict:
        q = _get(f"/api/quote/{urllib.parse.quote(symbol)}")
        return {"price": q.get("price"), "stale": q.get("stale", False)}

    def truth(symbol: str) -> float:
        return provider.quote(symbol, no_cache=True)["price"]

    return PriceMonitor(
        symbols_fn=symbols, served_fn=served, truth_fn=truth,
        tolerance_pct=tolerance_pct, interval=interval,
    )
