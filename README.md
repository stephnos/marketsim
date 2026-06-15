# MarketSim

A realistic stock-market **simulator** with two front-ends over one shared, live-ticking market:

- **A sleek web GUI** — search a ticker and get a quote, an Apple-Stocks-style chart, key stats, a watchlist, market movers, and one-click paper trading. Clean, editorial finance-site styling (think MarketWatch), no flashy neon.
- **A terminal CLI** — so AI agents (and humans) can *practice* trading from the command line before doing the real thing. Every command supports `--json`.

Prices are **simulated** (geometric Brownian motion). Nothing here touches a real exchange, so it is safe to experiment.

---

## Quick start

```bash
# from the project root
./run.sh                 # creates a venv, installs deps, serves on :8000
```

Then open <http://127.0.0.1:8000> in your browser.

Or do it manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m marketsim.cli serve            # web GUI + API at http://127.0.0.1:8000
```

Optionally install the `marketsim` command on your PATH:

```bash
pip install -e .
marketsim serve
```

---

## Web GUI

| Feature | Description |
| --- | --- |
| Search | Type a ticker or company name; arrow-keys + enter to select. |
| Quote + chart | Live price, day change, range tabs (1D–1Y), hover crosshair. |
| Key stats | Open, prev close, day & 52-week ranges, volume, market cap. |
| Watchlist | Add/remove symbols; scrolling ticker tape up top. |
| Movers | Top gainers, losers, most active. |
| Paper trading | Buy/Sell dialog, live portfolio P/L, order history. |

Everything refreshes live as the simulated market ticks.

---

## Terminal CLI (for AI agents)

The CLI is a thin client over the same HTTP API the GUI uses, so the browser and
your agents trade against the **same** market and account.

```bash
marketsim quote AAPL
marketsim search nvidia
marketsim chart TSLA --range 1M
marketsim movers
marketsim buy AAPL 10
marketsim sell AAPL 5
marketsim portfolio
marketsim orders
marketsim watch SPY
marketsim reset                 # back to $100,000 paper cash
```

### JSON mode (recommended for agents)

Add `--json` to any command for structured output:

```bash
marketsim quote AAPL --json
marketsim portfolio --json
marketsim buy NVDA 3 --json
```

### Multiple agents / accounts

Use `--account NAME` to isolate portfolios (defaults to `default`):

```bash
marketsim --account agent-1 buy AAPL 10
marketsim --account agent-2 portfolio --json
```

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `MARKETSIM_URL` | API base URL the CLI talks to | `http://127.0.0.1:8000` |
| `MARKETSIM_DATA` | Where account/order state is persisted | `~/.marketsim/state.json` |
| `NO_COLOR` | Disable ANSI colour in CLI output | unset |

---

## HTTP API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/status` | Market status |
| GET | `/api/search?q=` | Search symbols |
| GET | `/api/quote/{symbol}` | Single quote |
| GET | `/api/quotes?symbols=A,B` | Batch quotes |
| GET | `/api/history/{symbol}?range=1D` | Price history (1D/1W/1M/3M/6M/1Y) |
| GET | `/api/movers` | Gainers / losers / actives |
| GET | `/api/portfolio?account=` | Holdings & P/L |
| GET / POST | `/api/orders` | List / place orders |
| GET / POST / DELETE | `/api/watchlist` | Manage watchlist |
| POST | `/api/reset?account=` | Reset a paper account |

Interactive API docs are available at <http://127.0.0.1:8000/docs>.

---

## Project layout

```
marketsim/
  instruments.py   seed universe of tickers
  engine.py        simulation engine (prices, trading, accounts)
  storage.py       JSON persistence for user state
  server.py        FastAPI app (REST API + serves the web GUI)
  cli.py           terminal client
  web/             index.html, styles.css, app.js
```
