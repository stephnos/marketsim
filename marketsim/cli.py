"""MarketSim terminal client.

A thin HTTP client over the MarketSim API so AI agents (and humans) can practice
trading from a terminal against the same live-ticking simulated market the web
GUI uses. Every command supports ``--json`` for clean machine-readable output.

Examples
--------
    marketsim serve                 # start the API + web GUI
    marketsim quote AAPL
    marketsim search nvidia
    marketsim chart TSLA --range 1M
    marketsim buy AAPL 10
    marketsim sell AAPL 5
    marketsim portfolio
    marketsim orders
    marketsim movers
    marketsim watch SPY
    marketsim quote AAPL --json     # JSON for programmatic agents
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = os.environ.get("MARKETSIM_URL", "http://127.0.0.1:8000")

# ANSI colours (auto-disabled when piped or NO_COLOR is set).
_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


GREEN, RED, DIM, BOLD, CYAN = "32", "31", "2", "1", "36"


def color_num(value: float, text: str) -> str:
    return c(text, GREEN if value >= 0 else RED)


# ---- HTTP -----------------------------------------------------------------

class ApiError(Exception):
    pass


def _request(method: str, base: str, path: str, body: dict | None = None) -> object:
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("detail", str(exc))
        except Exception:
            detail = str(exc)
        raise ApiError(detail) from None
    except urllib.error.URLError as exc:
        raise ApiError(
            f"cannot reach MarketSim at {base} ({exc.reason}). "
            f"Start it with `marketsim serve`."
        ) from None


def get(base, path):
    return _request("GET", base, path)


def post(base, path, body=None):
    return _request("POST", base, path, body or {})


def delete(base, path):
    return _request("DELETE", base, path)


# ---- formatting helpers ---------------------------------------------------

def usd(n: float, dp: int = 2) -> str:
    return f"${n:,.{dp}f}"


def signed(n: float) -> str:
    return f"{'+' if n >= 0 else ''}{n:,.2f}"


def pct(n: float) -> str:
    return f"{'+' if n >= 0 else ''}{n:.2f}%"


def sparkline(values: list[float]) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    return "".join(blocks[min(7, int((v - lo) / span * 7.999))] for v in values)


def emit_json(obj) -> None:
    print(json.dumps(obj, indent=2))


# ---- commands -------------------------------------------------------------

def _maybe_usd(v):
    return usd(v) if isinstance(v, (int, float)) else "—"


def cmd_quote(args):
    q = get(args.base, f"/api/quote/{args.symbol}")
    if args.json:
        return emit_json(q)
    arrow = "▲" if q["change"] >= 0 else "▼"
    line = f"{q['change']:+.2f} ({pct(q['changePercent'])})"
    sector = q.get("sector") or q.get("exchange") or ""
    flags = []
    if q.get("marketState"):
        flags.append(q["marketState"])
    if q.get("stale"):
        flags.append(c("STALE", RED))
    flag_str = ("   " + " · ".join(flags)) if flags else ""
    print(f"\n{c(q['symbol'], BOLD)}  {q.get('name', '')}  {c(sector, DIM)}{flag_str}")
    print(f"{c(usd(q['price']), BOLD)}   {color_num(q['change'], arrow + ' ' + line)}\n")
    rows = [
        ("Open", _maybe_usd(q.get("open")), "Prev Close", _maybe_usd(q.get("prevClose"))),
        ("Day Low", _maybe_usd(q.get("dayLow")), "Day High", _maybe_usd(q.get("dayHigh"))),
        ("52W Low", _maybe_usd(q.get("yearLow")), "52W High", _maybe_usd(q.get("yearHigh"))),
        ("Volume", f"{q.get('volume', 0):,}", "Exchange", q.get("exchange") or "—"),
    ]
    for a, b, cc, d in rows:
        print(f"  {c(a + ':', DIM):<22} {b:<16}  {c(cc + ':', DIM):<22} {d}")
    print()


def cmd_track(args):
    q = post(args.base, "/api/track", {"symbol": args.symbol})
    if args.json:
        return emit_json(q)
    print(c(f"\n✓ Tracking {q['symbol']} — {q.get('name', '')} "
            f"({q.get('exchange', '')})  {usd(q['price'])}\n", GREEN))


def cmd_tracked(args):
    items = get(args.base, "/api/tracked")
    if args.json:
        return emit_json(items)
    print(f"\nTracking {len(items)} symbols:")
    for t in items:
        print(f"  {c(t['symbol'], BOLD):<10} {t.get('name', '')[:40]:<42} {c(t.get('exchange', ''), DIM)}")
    print()


def cmd_monitor(args):
    from dataclasses import asdict

    from .monitor import http_monitor

    mon = http_monitor(args.base, tolerance_pct=args.tolerance, interval=args.interval)
    if args.json and args.once:
        mon.check_once()
        return emit_json(mon.summary())
    if args.once:
        results = [asdict(r) for r in mon.check_once()]
        _print_monitor_results(results)
        _print_monitor_summary(mon.summary())
        return
    print(c(f"Price monitor — verifying {args.base} against fresh scrapes "
            f"(tolerance {args.tolerance:.2f}%, every {args.interval:.0f}s). Ctrl-C to stop.\n", BOLD))
    try:
        while True:
            results = [asdict(r) for r in mon.check_once()]
            _print_monitor_results(results)
            s = mon.summary()
            print(c(f"  → checks {s['counts']['checks']}  ok {s['counts']['ok']}  "
                    f"mismatch {s['counts']['mismatch']}  stale {s['counts']['stale']}  "
                    f"error {s['counts']['error']}  pass-rate {s['passRate']}%\n", DIM))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")


def _print_monitor_results(results):
    icons = {"ok": c("✓", GREEN), "mismatch": c("✗", RED),
             "stale": c("~", "33"), "error": c("!", RED)}
    for r in results:
        served = usd(r["served"]) if r["served"] is not None else "—"
        truth = usd(r["truth"]) if r["truth"] is not None else "—"
        extra = ""
        if r["status"] == "ok" and r["diffPercent"] is not None:
            extra = c(f"Δ{r['diffPercent']:.2f}%", DIM)
        elif r["detail"]:
            extra = r["detail"]
        print(f"  {icons.get(r['status'], '?')} {r['symbol']:<8} "
              f"served {served:<11} truth {truth:<11} {extra}")


def _print_monitor_summary(s):
    print(c(f"\n  pass-rate {s['passRate']}%  "
            f"(ok {s['counts']['ok']} / checks {s['counts']['checks']}, "
            f"mismatch {s['counts']['mismatch']}, stale {s['counts']['stale']}, "
            f"error {s['counts']['error']})\n", DIM))


def cmd_search(args):
    results = get(args.base, f"/api/search?q={urllib.parse.quote(args.query)}")
    if args.json:
        return emit_json(results)
    if not results:
        print("No matches.")
        return
    print()
    for r in results:
        tag = c("  ✓ tracked", GREEN) if r.get("tracked") else ""
        print(f"  {c(r['symbol'], BOLD):<10} {(r.get('name') or '')[:34]:<36} "
              f"{c((r.get('exchange') or ''), DIM):<12}{tag}")
    print()


def cmd_chart(args):
    h = get(args.base, f"/api/history/{args.symbol}?range={args.range}")
    if args.json:
        return emit_json(h)
    closes = [p["c"] for p in h["points"]]
    if not closes:
        print("No data.")
        return
    spark = sparkline(closes)
    col = GREEN if h["changePercent"] >= 0 else RED
    print(f"\n{c(args.symbol.upper(), BOLD)}  {args.range}   "
          f"{color_num(h['change'], signed(h['change']) + ' (' + pct(h['changePercent']) + ')')}")
    print(f"  {c(spark, col)}")
    print(f"  {c('low ' + usd(min(closes)), DIM)}   {c('high ' + usd(max(closes)), DIM)}   "
          f"{c('last ' + usd(closes[-1]), DIM)}\n")


def cmd_movers(args):
    m = get(args.base, f"/api/movers?limit={args.limit}")
    if args.json:
        return emit_json(m)
    titles = {"gainers": "Top Gainers", "losers": "Top Losers", "actives": "Most Active"}
    for key, title in titles.items():
        print(f"\n{c(title, BOLD)}")
        for q in m[key]:
            print(f"  {q['symbol']:<8} {usd(q['price']):>10}  "
                  f"{color_num(q['changePercent'], pct(q['changePercent']))}")
    print()


def cmd_buy(args):
    _trade(args, "buy")


def cmd_sell(args):
    _trade(args, "sell")


def _trade(args, side):
    order = post(args.base, "/api/orders", {
        "symbol": args.symbol, "side": side, "qty": args.qty, "account": args.account,
    })
    if args.json:
        return emit_json(order)
    verb = "Bought" if side == "buy" else "Sold"
    print(c(f"\n✓ {verb} {order['qty']} {order['symbol']} @ {usd(order['price'])}  "
            f"= {usd(order['notional'])}\n", GREEN if side == "buy" else CYAN))


def cmd_portfolio(args):
    p = get(args.base, f"/api/portfolio?account={args.account}")
    if args.json:
        return emit_json(p)
    print(f"\n{c('Portfolio', BOLD)}  ({args.account})")
    print(f"  Equity:   {c(usd(p['equity']), BOLD)}")
    print(f"  Cash:     {usd(p['cash'])}")
    print(f"  Holdings: {usd(p['holdingsValue'])}")
    print(f"  Unreal.:  {color_num(p['totalUnrealizedPL'], signed(p['totalUnrealizedPL']) + ' (' + pct(p['totalUnrealizedPLPercent']) + ')')}\n")
    if not p["positions"]:
        print(c("  No open positions.\n", DIM))
        return
    print(f"  {c('SYMBOL', DIM):<14} {c('QTY', DIM):>6} {c('AVG', DIM):>10} "
          f"{c('LAST', DIM):>10} {c('VALUE', DIM):>12} {c('P/L', DIM):>18}")
    for pos in p["positions"]:
        pl = f"{signed(pos['unrealizedPL'])} ({pct(pos['unrealizedPLPercent'])})"
        print(f"  {pos['symbol']:<6} {pos['qty']:>6g} {usd(pos['avgCost']):>10} "
              f"{usd(pos['price']):>10} {usd(pos['marketValue']):>12} "
              f"{color_num(pos['unrealizedPL'], pl):>26}")
    print()


def cmd_orders(args):
    orders = get(args.base, f"/api/orders?account={args.account}&limit={args.limit}")
    if args.json:
        return emit_json(orders)
    if not orders:
        print("No orders yet.")
        return
    print()
    for o in orders:
        tag = c("BUY ", GREEN) if o["side"] == "buy" else c("SELL", RED)
        print(f"  {tag}  {o['symbol']:<6} {o['qty']:>6g} @ {usd(o['price']):>10}  "
              f"= {usd(o['notional']):>12}")
    print()


def cmd_watch(args):
    wl = post(args.base, "/api/watchlist", {"symbol": args.symbol, "account": args.account})
    if args.json:
        return emit_json(wl)
    print(c(f"Added {args.symbol.upper()} to watchlist.", GREEN))
    _print_watchlist(wl)


def cmd_unwatch(args):
    wl = delete(args.base, f"/api/watchlist/{args.symbol}?account={args.account}")
    if args.json:
        return emit_json(wl)
    print(f"Removed {args.symbol.upper()} from watchlist.")
    _print_watchlist(wl)


def cmd_watchlist(args):
    wl = get(args.base, f"/api/watchlist?account={args.account}")
    if args.json:
        return emit_json(wl)
    _print_watchlist(wl)


def _print_watchlist(wl):
    if not wl:
        print(c("  (empty)\n", DIM))
        return
    print()
    for q in wl:
        print(f"  {q['symbol']:<8} {usd(q['price']):>10}  "
              f"{color_num(q['changePercent'], pct(q['changePercent']))}")
    print()


def cmd_reset(args):
    p = post(args.base, f"/api/reset?account={args.account}")
    if args.json:
        return emit_json(p)
    print(c(f"Account '{args.account}' reset to {usd(p['cash'])}.", CYAN))


def cmd_serve(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("uvicorn is required to serve. Run: pip install -r requirements.txt")
    print(f"MarketSim running at http://{args.host}:{args.port}  (web GUI + API)")
    uvicorn.run("marketsim.server:app", host=args.host, port=args.port, reload=args.reload)


# ---- arg parsing ----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Shared global options, accepted both before and after the subcommand so
    # that `marketsim quote AAPL --json` and `marketsim --json quote AAPL` work.
    # SUPPRESS defaults so the subparser copies don't clobber values that were
    # parsed *before* the subcommand (an argparse + parents quirk). Real
    # defaults are applied in main() after parsing.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", default=argparse.SUPPRESS,
                        help=f"API base URL (default {DEFAULT_BASE})")
    common.add_argument("--account", default=argparse.SUPPRESS,
                        help="account name (default 'default')")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit raw JSON")

    p = argparse.ArgumentParser(
        prog="marketsim", description="MarketSim terminal client.", parents=[common]
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    s = add("quote", help="show a quote")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_quote)

    s = add("search", help="search tickers / companies")
    s.add_argument("query")
    s.set_defaults(func=cmd_search)

    s = add("track", help="track any real ticker from now on")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_track)

    s = add("tracked", help="list tracked symbols")
    s.set_defaults(func=cmd_tracked)

    s = add("monitor", help="continuously verify served prices vs fresh scrapes")
    s.add_argument("--tolerance", type=float, default=1.0, help="match tolerance %% (default 1.0)")
    s.add_argument("--interval", type=float, default=60.0, help="seconds between cycles")
    s.add_argument("--once", action="store_true", help="run a single check cycle and exit")
    s.set_defaults(func=cmd_monitor)

    s = add("chart", help="ascii price chart")
    s.add_argument("symbol")
    s.add_argument("--range", default="1M", choices=["1D", "1W", "1M", "3M", "6M", "1Y"])
    s.set_defaults(func=cmd_chart)

    s = add("movers", help="top gainers / losers / actives")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_movers)

    s = add("buy", help="buy shares (market order)")
    s.add_argument("symbol")
    s.add_argument("qty", type=float)
    s.set_defaults(func=cmd_buy)

    s = add("sell", help="sell shares (market order)")
    s.add_argument("symbol")
    s.add_argument("qty", type=float)
    s.set_defaults(func=cmd_sell)

    s = add("portfolio", help="show holdings & P/L")
    s.set_defaults(func=cmd_portfolio)

    s = add("orders", help="order history")
    s.add_argument("--limit", type=int, default=25)
    s.set_defaults(func=cmd_orders)

    s = add("watch", help="add a symbol to the watchlist")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_watch)

    s = add("unwatch", help="remove a symbol from the watchlist")
    s.add_argument("symbol")
    s.set_defaults(func=cmd_unwatch)

    s = add("watchlist", help="show the watchlist")
    s.set_defaults(func=cmd_watchlist)

    s = add("reset", help="reset the paper account")
    s.set_defaults(func=cmd_reset)

    s = add("serve", help="start the API + web GUI server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Apply real defaults for any suppressed global option not supplied.
    args.base = getattr(args, "base", DEFAULT_BASE)
    args.account = getattr(args, "account", "default")
    args.json = getattr(args, "json", False)
    try:
        args.func(args)
        return 0
    except ApiError as exc:
        if getattr(args, "json", False):
            emit_json({"error": str(exc)})
        else:
            print(c(f"error: {exc}", RED), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
