"""Shared pytest fixtures for MarketSim.

Everything here is wired to run fully offline: a deterministic in-memory
``FakeProvider`` stands in for the live Alpaca/Yahoo data sources, and the
engine is given a throwaway storage file under ``tmp_path``. We also point the
default storage path at a temp file and disable the background price monitor
*before* importing the app module, so importing the server never touches the
real ``~/.marketsim`` state or spins up network/monitor threads.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

# Must be set before importing marketsim.server (which builds a module-level
# Engine + Storage at import time).
_TMP = tempfile.mkdtemp(prefix="marketsim-test-")
os.environ["MARKETSIM_DATA"] = str(Path(_TMP) / "state.json")
os.environ["MARKETSIM_MONITOR"] = "0"
os.environ.setdefault("MARKETSIM_DATA_PROVIDER", "yahoo")

from marketsim.engine import STARTING_CASH, Engine  # noqa: E402
from marketsim.providers.base import ProviderError  # noqa: E402
from marketsim.storage import Storage  # noqa: E402

# Deterministic prices for the symbols the engine seeds by default, plus a few
# extras used in search/track tests.
DEFAULT_PRICES = {
    "AAPL": 100.0, "MSFT": 200.0, "NVDA": 50.0, "AMZN": 150.0,
    "GOOGL": 120.0, "META": 300.0, "TSLA": 250.0, "JPM": 180.0,
    "SPY": 400.0, "QQQ": 350.0, "KO": 60.0,
}


def _make_quote(symbol: str, price: float) -> dict:
    # changePercent is derived from price so ordering in movers is deterministic.
    return {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "sector": "Technology",
        "exchange": "NASDAQ",
        "currency": "USD",
        "price": price,
        "change": round(price * 0.01, 2),
        "changePercent": round(price / 100.0, 2),
        "open": price,
        "prevClose": round(price - 1, 2),
        "dayHigh": round(price + 1, 2),
        "dayLow": round(price - 1, 2),
        "yearHigh": round(price + 10, 2),
        "yearLow": round(price - 10, 2),
        "volume": int(price * 1000),
        "marketState": "OPEN",
        "asOf": int(time.time()),
    }


class FakeProvider:
    """Offline stand-in for a market-data provider (see providers/base.py)."""

    source_name = "fake"

    def __init__(self, prices: dict | None = None):
        self.prices = dict(prices if prices is not None else DEFAULT_PRICES)
        self.cooldown = False

    def quote(self, symbol: str, no_cache: bool = False) -> dict:
        sym = symbol.upper()
        if sym not in self.prices:
            raise ProviderError(f"unknown symbol '{sym}'")
        return _make_quote(sym, self.prices[sym])

    def history(self, symbol: str, range_: str) -> dict:
        sym = symbol.upper()
        if sym not in self.prices:
            raise ProviderError(f"no history for '{sym}'")
        base = self.prices[sym]
        points = [
            {"t": int(time.time()) - (5 - i) * 60, "c": round(base + i, 2)}
            for i in range(6)
        ]
        first, last = points[0]["c"], points[-1]["c"]
        return {
            "symbol": sym,
            "range": range_,
            "points": points,
            "change": round(last - first, 2),
            "changePercent": round((last - first) / first * 100, 2) if first else 0.0,
        }

    def search(self, query: str, limit: int = 12) -> list[dict]:
        ql = query.strip().lower()
        out = []
        for sym in self.prices:
            if ql in sym.lower() or ql in f"{sym} inc.".lower():
                out.append({
                    "symbol": sym, "name": f"{sym} Inc.",
                    "sector": "Technology", "exchange": "NASDAQ", "type": "EQUITY",
                })
        return out[:limit]

    def in_cooldown(self) -> bool:
        return self.cooldown

    def cooldown_remaining(self) -> float:
        return 0.0


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def engine(tmp_path, fake_provider) -> Engine:
    """A fresh engine with isolated storage and the offline provider."""
    storage = Storage(tmp_path / "state.json")
    return Engine(storage=storage, provider=fake_provider)


@pytest.fixture
def client(engine, monkeypatch):
    """A FastAPI TestClient whose endpoints use the test engine.

    We deliberately do not enter the TestClient as a context manager, so the
    app's startup events (cache warmer + price monitor) never run.
    """
    from fastapi.testclient import TestClient

    from marketsim import server

    monkeypatch.setattr(server, "engine", engine)
    return TestClient(server.app)


@pytest.fixture
def starting_cash() -> float:
    return STARTING_CASH
