"""FastAPI server exposing the simulator over HTTP and serving the web GUI.

The same API is consumed by the browser front-end and by the terminal CLI, so
humans and AI agents trade against one shared, live market. A background task
keeps the quote cache for tracked symbols warm, and an in-process price monitor
continuously verifies served prices against fresh scrapes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import Engine
from .monitor import inprocess_monitor

# How often to refresh one tracked symbol (seconds). Round-robin, so the full
# set is refreshed every REFRESH_SECONDS * len(tracked). Kept gentle to stay
# under the data source's rate limits.
REFRESH_SECONDS = float(os.environ.get("MARKETSIM_REFRESH", "15"))
# Price-monitor cadence, tolerance, and how many symbols to check per cycle
# (rotating subset, so we never burst the data source).
MONITOR_INTERVAL = float(os.environ.get("MARKETSIM_MONITOR_INTERVAL", "300"))
MONITOR_TOLERANCE = float(os.environ.get("MARKETSIM_MONITOR_TOLERANCE", "1.0"))
MONITOR_MAX_PER_CYCLE = int(os.environ.get("MARKETSIM_MONITOR_BATCH", "3"))
MONITOR_ENABLED = os.environ.get("MARKETSIM_MONITOR", "1") != "0"

engine = Engine()
monitor = inprocess_monitor(
    engine, tolerance_pct=MONITOR_TOLERANCE,
    interval=MONITOR_INTERVAL, max_per_cycle=MONITOR_MAX_PER_CYCLE,
)

app = FastAPI(title="MarketSim", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

WEB_DIR = Path(__file__).parent / "web"


class OrderRequest(BaseModel):
    symbol: str
    side: str = Field(pattern="^(?i)(buy|sell)$")
    qty: float = Field(gt=0)
    account: str = "default"


class SymbolRequest(BaseModel):
    symbol: str
    account: str = "default"


@app.on_event("startup")
async def _startup() -> None:
    app.state._running = True

    async def warmer() -> None:
        # Round-robin refresh so we never burst the data source.
        while app.state._running:
            symbols = sorted(engine.tracked)
            for sym in symbols:
                if not app.state._running:
                    break
                await asyncio.to_thread(engine.refresh_tracked, [sym])
                await asyncio.sleep(REFRESH_SECONDS)
            if not symbols:
                await asyncio.sleep(REFRESH_SECONDS)

    app.state._warmer = asyncio.create_task(warmer())

    if MONITOR_ENABLED:
        app.state._monitor_stop = threading.Event()
        app.state._monitor_thread = threading.Thread(
            target=monitor.run, args=(app.state._monitor_stop,),
            name="price-monitor", daemon=True,
        )
        app.state._monitor_thread.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    app.state._running = False
    task = getattr(app.state, "_warmer", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    stop = getattr(app.state, "_monitor_stop", None)
    if stop:
        stop.set()
    engine.flush()  # save last-known quotes so the next start isn't blank


# ---- market data ----------------------------------------------------------

@app.get("/api/status")
def status():
    return engine.market_status()


@app.get("/api/search")
def search(q: str = Query(""), limit: int = 12):
    return engine.search(q, limit)


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    q = engine.quote(symbol)
    if not q:
        raise HTTPException(404, f"unknown or unavailable symbol '{symbol}'")
    return q


@app.get("/api/quotes")
def quotes(symbols: str = Query(...)):
    return engine.quotes([s for s in symbols.split(",") if s.strip()])


@app.get("/api/history/{symbol}")
def history(symbol: str, range: str = "1M"):
    h = engine.history(symbol, range)
    if not h:
        raise HTTPException(404, f"no history for '{symbol}'")
    return h


@app.get("/api/movers")
def movers(limit: int = 5):
    return engine.movers(limit)


@app.get("/api/tracked")
def tracked():
    return engine.get_tracked()


@app.post("/api/track")
def track(req: SymbolRequest):
    try:
        return engine.track(req.symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"cannot track '{req.symbol}': {exc}")


@app.delete("/api/track/{symbol}")
def untrack(symbol: str):
    engine.untrack(symbol)
    return {"symbol": symbol.upper(), "tracked": False}


# ---- health / verification -------------------------------------------------

@app.get("/api/health/prices")
def health_prices(check: bool = False):
    if check:
        # Run a fresh comparison cycle on demand (synchronous).
        monitor.check_once()
    return monitor.summary()


# ---- accounts / trading ----------------------------------------------------

@app.get("/api/portfolio")
def portfolio(account: str = "default"):
    return engine.portfolio(account)


@app.get("/api/orders")
def orders(account: str | None = None, limit: int = 50):
    return engine.orders(account, limit)


@app.post("/api/orders")
def place_order(req: OrderRequest):
    try:
        return engine.place_order(req.account, req.symbol, req.side, req.qty)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/watchlist")
def watchlist(account: str = "default"):
    return engine.watchlist(account)


@app.post("/api/watchlist")
def add_watch(req: SymbolRequest):
    try:
        return engine.add_watch(req.symbol, req.account)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/watchlist/{symbol}")
def remove_watch(symbol: str, account: str = "default"):
    return engine.remove_watch(symbol, account)


@app.post("/api/reset")
def reset(account: str = "default"):
    return engine.reset_account(account)


# ---- static web GUI --------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(WEB_DIR / "index.html"))
