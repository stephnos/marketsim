"""FastAPI server exposing the simulator over HTTP and serving the web GUI.

The same API is consumed by the browser front-end and by the terminal CLI, so
humans and AI agents trade against one shared, live-ticking market.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import Engine

TICK_SECONDS = 1.5  # how often the live market advances one simulated minute

engine = Engine()
app = FastAPI(title="MarketSim", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).parent / "web"


class OrderRequest(BaseModel):
    symbol: str
    side: str = Field(pattern="^(?i)(buy|sell)$")
    qty: float = Field(gt=0)
    account: str = "default"


class WatchRequest(BaseModel):
    symbol: str
    account: str = "default"


@app.on_event("startup")
async def _start_ticker() -> None:
    app.state._ticking = True

    async def loop() -> None:
        while app.state._ticking:
            engine.tick()
            await asyncio.sleep(TICK_SECONDS)

    app.state._ticker = asyncio.create_task(loop())


@app.on_event("shutdown")
async def _stop_ticker() -> None:
    app.state._ticking = False
    task = getattr(app.state, "_ticker", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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
        raise HTTPException(404, f"unknown symbol '{symbol}'")
    return q


@app.get("/api/quotes")
def quotes(symbols: str = Query(...)):
    return engine.quotes([s for s in symbols.split(",") if s.strip()])


@app.get("/api/history/{symbol}")
def history(symbol: str, range: str = "1M"):
    h = engine.history(symbol, range)
    if not h:
        raise HTTPException(404, f"unknown symbol '{symbol}'")
    return h


@app.get("/api/movers")
def movers(limit: int = 5):
    return engine.movers(limit)


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
def add_watch(req: WatchRequest):
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
