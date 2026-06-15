"""End-to-end tests for the HTTP API (marketsim/server.py).

These drive the FastAPI app via TestClient against the offline FakeProvider, so
they exercise the exact request/response path the CLI uses — including the full
buy -> portfolio -> orders -> reset flow and per-account isolation, which is the
core of the multi-agent use case.
"""

from __future__ import annotations


# ---- market data ----------------------------------------------------------

def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fake"
    assert body["rateLimited"] is False


def test_search(client):
    r = client.get("/api/search", params={"q": "AAPL"})
    assert r.status_code == 200
    symbols = [x["symbol"] for x in r.json()]
    assert "AAPL" in symbols


def test_quote_known(client):
    r = client.get("/api/quote/AAPL")
    assert r.status_code == 200
    assert r.json()["price"] == 100.0


def test_quote_unknown_404(client):
    assert client.get("/api/quote/ZZZZ").status_code == 404


def test_history(client):
    r = client.get("/api/history/AAPL", params={"range": "1M"})
    assert r.status_code == 200
    body = r.json()
    assert body["range"] == "1M"
    assert len(body["points"]) == 6


def test_history_unknown_404(client):
    assert client.get("/api/history/ZZZZ").status_code == 404


def test_movers_shape(client):
    r = client.get("/api/movers", params={"limit": 3})
    assert r.status_code == 200
    body = r.json()
    for key in ("gainers", "losers", "actives"):
        assert key in body
        assert len(body[key]) <= 3


# ---- trading flow ---------------------------------------------------------

def _buy(client, symbol, qty, account):
    return client.post("/api/orders", json={
        "symbol": symbol, "side": "buy", "qty": qty, "account": account,
    })


def test_buy_updates_portfolio(client, starting_cash):
    r = _buy(client, "AAPL", 10, "agent-1")
    assert r.status_code == 200
    order = r.json()
    assert order["price"] == 100.0 and order["notional"] == 1000.0

    p = client.get("/api/portfolio", params={"account": "agent-1"}).json()
    assert p["cash"] == starting_cash - 1000.0
    assert p["equity"] == starting_cash  # bought at the current price, so flat
    pos = {x["symbol"]: x for x in p["positions"]}
    assert pos["AAPL"]["qty"] == 10


def test_sell_reduces_position(client, starting_cash):
    _buy(client, "AAPL", 10, "agent-1")
    r = client.post("/api/orders", json={
        "symbol": "AAPL", "side": "sell", "qty": 4, "account": "agent-1",
    })
    assert r.status_code == 200
    p = client.get("/api/portfolio", params={"account": "agent-1"}).json()
    pos = {x["symbol"]: x for x in p["positions"]}
    assert pos["AAPL"]["qty"] == 6
    assert p["cash"] == starting_cash - 1000.0 + 400.0


def test_account_isolation(client, starting_cash):
    _buy(client, "AAPL", 10, "agent-1")

    other = client.get("/api/portfolio", params={"account": "agent-2"}).json()
    assert other["cash"] == starting_cash
    assert other["positions"] == []

    o1 = client.get("/api/orders", params={"account": "agent-1"}).json()
    o2 = client.get("/api/orders", params={"account": "agent-2"}).json()
    assert len(o1) == 1 and len(o2) == 0


def test_insufficient_cash_400(client):
    r = _buy(client, "AAPL", 100000, "agent-1")  # ~$10M order, only $100k cash
    assert r.status_code == 400


def test_insufficient_shares_400(client):
    r = client.post("/api/orders", json={
        "symbol": "AAPL", "side": "sell", "qty": 1, "account": "fresh",
    })
    assert r.status_code == 400


def test_order_unknown_symbol_400(client):
    r = _buy(client, "ZZZZ", 1, "agent-1")
    assert r.status_code == 400


def test_invalid_side_422(client):
    r = client.post("/api/orders", json={
        "symbol": "AAPL", "side": "hold", "qty": 1, "account": "agent-1",
    })
    assert r.status_code == 422


def test_nonpositive_qty_422(client):
    r = client.post("/api/orders", json={
        "symbol": "AAPL", "side": "buy", "qty": 0, "account": "agent-1",
    })
    assert r.status_code == 422


# ---- watchlist ------------------------------------------------------------

def test_watchlist_add_remove(client):
    add = client.post("/api/watchlist", json={"symbol": "KO", "account": "agent-1"})
    assert add.status_code == 200
    assert "KO" in [x["symbol"] for x in add.json()]

    rm = client.delete("/api/watchlist/KO", params={"account": "agent-1"})
    assert rm.status_code == 200
    assert "KO" not in [x["symbol"] for x in rm.json()]


# ---- reset ----------------------------------------------------------------

def test_reset_clears_account(client, starting_cash):
    _buy(client, "AAPL", 5, "agent-1")
    r = client.post("/api/reset", params={"account": "agent-1"})
    assert r.status_code == 200
    p = r.json()
    assert p["cash"] == starting_cash
    assert p["positions"] == []
    assert client.get("/api/orders", params={"account": "agent-1"}).json() == []
