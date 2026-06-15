# MarketSim

A paper-trading app on **real, live market data**, with two front-ends over one shared market:

- **A sleek web GUI** — search *any* real ticker and get a live quote, an Apple-Stocks-style chart, key stats, a watchlist, market movers, and one-click paper trading. Clean, editorial finance-site styling (think MarketWatch), no flashy neon.
- **A terminal CLI** — so AI agents (and humans) can *practice* trading from the command line before doing the real thing. Every command supports `--json`.

**Live data** comes from **Alpaca** (real-time IEX feed) or **Yahoo Finance** (no key). Any ticker you look up — NYSE, NASDAQ, ETFs — is **tracked from then on**. Trades are paper trades filled at the real current price — nothing touches a real brokerage, so it's safe to experiment.

A built-in **price-verification monitor** continuously fetches fresh prices and asserts the app's served prices match within a tolerance, so you always know the data is lining up with the real market.

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

### Choosing a data source

MarketSim works out of the box with **Yahoo Finance** (no key). For a more
reliable real-time feed, use **Alpaca**:

1. Create a free account at <https://alpaca.markets>, switch to **Paper
   Trading**, and generate API keys (Account → Manage Accounts → API Keys).
2. Copy the template and fill in your keys:

   ```bash
   cp .env.example .env
   # edit .env: APCA_API_KEY_ID, APCA_API_SECRET_KEY
   ```

`.env` is gitignored. With keys present (or `MARKETSIM_DATA_PROVIDER=alpaca`),
MarketSim uses Alpaca's real-time IEX feed; otherwise it falls back to Yahoo.
The free Alpaca feed is **IEX** (one venue), so the last price can differ
slightly from the full consolidated tape.

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
your agents trade against the **same** live market and account.

### Agent playbook (how to play)

**The rules of the game:**

- Every account starts with **$100,000** in paper cash.
- Orders fill **immediately at the real, live market price** — nothing touches a
  real brokerage, so it's safe to experiment.
- The **market is shared**: live prices, history, movers, and the tracked-symbol
  set are global and identical for every agent.
- Your **portfolio is yours**: cash, positions, order history, and watchlist are
  isolated per `--account`.

**Claim your identity (important):** pass `--account <name>` on **every** command.
There is no account env var, so a command without `--account` uses the shared
`default` account — i.e. you'd be trading out of the same wallet as everyone else.

**The loop — observe → decide → trade → review:**

```bash
# 1. OBSERVE the shared market (all JSON for easy parsing)
marketsim --account agent-1 search "nvidia" --json
marketsim --account agent-1 quote NVDA --json
marketsim --account agent-1 movers --json

# 2. CHECK your own position before acting
marketsim --account agent-1 portfolio --json

# 3. TRADE at the live price
marketsim --account agent-1 buy NVDA 5 --json
marketsim --account agent-1 sell NVDA 2 --json

# 4. REVIEW your fills and updated equity
marketsim --account agent-1 orders --json
marketsim --account agent-1 portfolio --json   # track equity / P&L over time
```

**Getting benchmarked:** agents are compared head-to-head by equity. Poll each
account's `portfolio --json` and rank by `equity` (or `totalUnrealizedPLPercent`)
to build a leaderboard. Use a distinct `--account` per agent so the comparison is
clean. Reset a single competitor any time with `marketsim --account <name> reset`.

### Command reference

```bash
marketsim search "coca cola"    # resolve a query to real tickers
marketsim track KO              # track any ticker from now on
marketsim quote KO
marketsim chart TSLA --range 1M
marketsim movers
marketsim buy KO 10
marketsim sell KO 5
marketsim portfolio
marketsim orders
marketsim watch SPY
marketsim tracked               # list everything being tracked
marketsim reset                 # back to $100,000 paper cash
```

Looking a symbol up (via `quote`, `track`, `watch`, or selecting it in the GUI)
adds it to the tracked set, so it keeps refreshing from then on.

### Continuous price verification

The `monitor` command verifies that the prices the app serves match a fresh,
independent scrape — end-to-end through the HTTP API:

```bash
marketsim monitor                       # loop forever, printing a live table
marketsim monitor --once                # a single check cycle
marketsim monitor --once --json         # machine-readable summary
marketsim monitor --tolerance 0.5 --interval 30
```

The server also runs this check in-process and exposes it:

```bash
curl "http://127.0.0.1:8000/api/health/prices?check=true"
```

Each result is `ok` (served matches truth within tolerance), `mismatch`,
`stale` (data source briefly unavailable), or `error`.

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
| `MARKETSIM_DATA_PROVIDER` | Data source: `alpaca` or `yahoo` | auto (alpaca if keys present) |
| `APCA_API_KEY_ID` | Alpaca API key id (for the Alpaca provider) | unset |
| `APCA_API_SECRET_KEY` | Alpaca API secret | unset |
| `APCA_API_BASE_URL` | Alpaca trading base URL | `https://paper-api.alpaca.markets` |
| `ALPACA_FEED` | Alpaca data feed (`iex` free, `sip` paid) | `iex` |
| `MARKETSIM_URL` | API base URL the CLI talks to | `http://127.0.0.1:8000` |
| `MARKETSIM_DATA` | Where account/tracked/order state is persisted | `~/.marketsim/state.json` |
| `MARKETSIM_REFRESH` | Seconds between refreshing each tracked symbol (round-robin) | `15` |
| `MARKETSIM_MONITOR` | Set to `0` to disable the in-process price monitor | `1` |
| `MARKETSIM_MONITOR_INTERVAL` | Seconds between monitor cycles | `300` |
| `MARKETSIM_MONITOR_TOLERANCE` | Price-match tolerance, percent | `1.0` |
| `MARKETSIM_MONITOR_BATCH` | Symbols checked per monitor cycle (rotating subset) | `3` |
| `NO_COLOR` | Disable ANSI colour in CLI output | unset |

These can be set in the environment or in a gitignored `.env` file in the
project root (see `.env.example`).

---

## HTTP API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/status` | Market status + data source |
| GET | `/api/search?q=` | Search real tickers |
| GET | `/api/quote/{symbol}` | Single live quote (auto-tracks) |
| GET | `/api/quotes?symbols=A,B` | Batch quotes |
| GET | `/api/history/{symbol}?range=1D` | Price history (1D/1W/1M/3M/6M/1Y) |
| GET | `/api/movers` | Gainers / losers / actives (tracked set) |
| GET | `/api/tracked` | List tracked symbols |
| POST / DELETE | `/api/track` | Track / untrack a symbol |
| GET | `/api/health/prices?check=true` | Price-verification monitor status |
| GET | `/api/portfolio?account=` | Holdings & P/L |
| GET / POST | `/api/orders` | List / place orders |
| GET / POST / DELETE | `/api/watchlist` | Manage watchlist |
| POST | `/api/reset?account=` | Reset a paper account |

Interactive API docs are available at <http://127.0.0.1:8000/docs>.

> **Resilience:** providers cache quotes briefly and, on rate-limiting (HTTP
> 429), enter a short **cooldown** where the app serves the last-known price
> flagged `stale` / `DELAYED` instead of calling the source. Quotes are
> persisted to disk, so even a restart during an outage shows prices instead of
> a blank page, and the web UI shows a "Live data delayed" banner rather than
> going empty. (Yahoo's unofficial endpoints rate-limit aggressively; Alpaca is
> steadier.)

---

## Testing

The test suite runs **fully offline** — a deterministic fake data provider
stands in for Alpaca/Yahoo, so no network or API keys are needed.

```bash
pip install -e ".[dev]"   # installs pytest + httpx
pytest                    # run everything
```

- `tests/test_cli.py` — CLI argument parsing and endpoint dispatch (HTTP mocked):
  verifies each subcommand hits the right endpoint, threads `--account` through
  (defaulting to `default`), supports global flags before or after the
  subcommand, emits JSON in `--json` mode, and exits non-zero on API errors.
- `tests/test_api.py` — end-to-end HTTP tests via FastAPI's `TestClient`: the
  full buy → portfolio → sell → orders → reset flow, **per-account isolation**
  (the multi-agent guarantee), and error cases (insufficient cash/shares,
  unknown symbols, invalid orders).

---

## Project layout

```
marketsim/
  instruments.py     default symbols tracked on first launch
  config.py          loads .env (API keys, provider selection)
  providers/
    base.py          shared provider interface + ProviderError
    alpaca.py        Alpaca client (real-time IEX: search/quote/history/clock)
    yahoo.py         Yahoo Finance client (search/quote/history, cookies, backoff)
    __init__.py      make_provider() factory (alpaca | yahoo)
  engine.py          trading/accounts layer + dynamic tracking over live data
  monitor.py         continuous price-verification monitor
  storage.py         JSON persistence for user + tracked state
  server.py          FastAPI app (REST API, cache warmer, monitor, serves GUI)
  cli.py             terminal client
  web/               index.html, styles.css, app.js
```
