"""Tests for the terminal CLI (marketsim/cli.py).

The CLI is a thin HTTP client, so these tests mock the get/post/delete helpers
and assert that each subcommand:
  * parses its arguments (including global --account/--base/--json, before or
    after the subcommand),
  * hits the right endpoint with the right payload,
  * threads the account through, defaulting to "default",
  * emits JSON in --json mode and a non-zero exit on API errors.
No network or server is involved.
"""

from __future__ import annotations

import json

import pytest

from marketsim import cli
from marketsim.cli import DEFAULT_BASE, ApiError, build_parser, main


class HttpRecorder:
    """Records (method, base, path, body) and returns canned responses."""

    def __init__(self):
        self.calls = []
        self.response = {}
        self.raise_exc = None

    def get(self, base, path):
        self.calls.append(("GET", base, path, None))
        return self._return()

    def post(self, base, path, body=None):
        self.calls.append(("POST", base, path, body))
        return self._return()

    def delete(self, base, path):
        self.calls.append(("DELETE", base, path, None))
        return self._return()

    def _return(self):
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def http(monkeypatch):
    rec = HttpRecorder()
    monkeypatch.setattr(cli, "get", rec.get)
    monkeypatch.setattr(cli, "post", rec.post)
    monkeypatch.setattr(cli, "delete", rec.delete)
    return rec


# ---- argument parsing -----------------------------------------------------

@pytest.mark.parametrize(
    "argv,expected_func",
    [
        (["quote", "AAPL"], "cmd_quote"),
        (["search", "apple"], "cmd_search"),
        (["track", "KO"], "cmd_track"),
        (["tracked"], "cmd_tracked"),
        (["chart", "TSLA"], "cmd_chart"),
        (["movers"], "cmd_movers"),
        (["buy", "AAPL", "10"], "cmd_buy"),
        (["sell", "AAPL", "5"], "cmd_sell"),
        (["portfolio"], "cmd_portfolio"),
        (["orders"], "cmd_orders"),
        (["watch", "SPY"], "cmd_watch"),
        (["unwatch", "SPY"], "cmd_unwatch"),
        (["watchlist"], "cmd_watchlist"),
        (["reset"], "cmd_reset"),
        (["serve"], "cmd_serve"),
    ],
)
def test_subcommand_dispatch(argv, expected_func):
    args = build_parser().parse_args(argv)
    assert args.func.__name__ == expected_func


def test_missing_command_errors():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_buy_requires_qty():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["buy", "AAPL"])


# ---- global options & account threading -----------------------------------

def test_defaults_applied(http):
    assert main(["portfolio", "--json"]) == 0
    method, base, path, _ = http.last
    assert method == "GET"
    assert base == DEFAULT_BASE
    assert "account=default" in path


def test_account_before_subcommand(http):
    main(["--account", "agent-1", "portfolio", "--json"])
    assert "account=agent-1" in http.last[2]


def test_account_after_subcommand(http):
    main(["portfolio", "--account", "agent-2", "--json"])
    assert "account=agent-2" in http.last[2]


def test_base_override(http):
    main(["--base", "http://example.com:9000", "portfolio", "--json"])
    assert http.last[1] == "http://example.com:9000"


# ---- per-command endpoint contracts ---------------------------------------

def test_quote_endpoint(http, capsys):
    http.response = {"symbol": "AAPL", "price": 100.0}
    assert main(["quote", "AAPL", "--json"]) == 0
    assert http.last[:3] == ("GET", DEFAULT_BASE, "/api/quote/AAPL")
    assert json.loads(capsys.readouterr().out) == {"symbol": "AAPL", "price": 100.0}


def test_search_url_encodes_query(http):
    http.response = []
    main(["search", "coca cola", "--json"])
    assert http.last[2] == "/api/search?q=coca%20cola"


def test_chart_passes_range(http):
    http.response = {"points": [], "change": 0, "changePercent": 0}
    main(["chart", "TSLA", "--range", "1W", "--json"])
    assert http.last[2] == "/api/history/TSLA?range=1W"


def test_movers_passes_limit(http):
    http.response = {"gainers": [], "losers": [], "actives": []}
    main(["movers", "--limit", "3", "--json"])
    assert http.last[2] == "/api/movers?limit=3"


def test_buy_posts_order(http, capsys):
    http.response = {"symbol": "AAPL", "qty": 10.0, "price": 100.0, "notional": 1000.0}
    assert main(["--account", "agent-1", "buy", "AAPL", "10", "--json"]) == 0
    method, _, path, body = http.last
    assert (method, path) == ("POST", "/api/orders")
    assert body == {"symbol": "AAPL", "side": "buy", "qty": 10.0, "account": "agent-1"}


def test_sell_posts_order(http):
    http.response = {"symbol": "AAPL", "qty": 5.0, "price": 100.0, "notional": 500.0}
    main(["sell", "AAPL", "5"])
    _, _, path, body = http.last
    assert path == "/api/orders"
    assert body["side"] == "sell" and body["qty"] == 5.0


def test_track_posts_symbol(http):
    http.response = {"symbol": "KO", "name": "Coca-Cola", "exchange": "NYSE", "price": 60.0}
    main(["track", "KO"])
    assert http.last[:3] == ("POST", DEFAULT_BASE, "/api/track")
    assert http.last[3] == {"symbol": "KO"}


def test_watch_post_and_account(http):
    http.response = []
    main(["--account", "a", "watch", "SPY", "--json"])
    method, _, path, body = http.last
    assert (method, path) == ("POST", "/api/watchlist")
    assert body == {"symbol": "SPY", "account": "a"}


def test_unwatch_delete(http):
    http.response = []
    main(["--account", "a", "unwatch", "SPY", "--json"])
    assert http.last[0] == "DELETE"
    assert http.last[2] == "/api/watchlist/SPY?account=a"


def test_reset_posts(http):
    http.response = {"cash": 100000.0}
    main(["--account", "a", "reset", "--json"])
    assert http.last[:3] == ("POST", DEFAULT_BASE, "/api/reset?account=a")


# ---- output & error handling ----------------------------------------------

def test_quote_text_output(http, capsys):
    http.response = {
        "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ",
        "price": 100.0, "change": 1.0, "changePercent": 1.0,
        "open": 99.0, "prevClose": 99.0, "dayHigh": 101.0, "dayLow": 98.0,
        "yearHigh": 120.0, "yearLow": 80.0, "volume": 1000, "marketState": "OPEN",
    }
    assert main(["quote", "AAPL"]) == 0
    out = capsys.readouterr().out
    assert "AAPL" in out and "$100.00" in out


def test_api_error_json(http, capsys):
    http.raise_exc = ApiError("boom")
    assert main(["quote", "AAPL", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "boom"}


def test_api_error_text(http, capsys):
    http.raise_exc = ApiError("boom")
    assert main(["quote", "AAPL"]) == 1
    err = capsys.readouterr().err
    assert "error: boom" in err
