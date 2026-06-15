"""Default symbols MarketSim tracks on first launch.

These are just a convenient starting set so the GUI isn't empty. Any real
ticker can be looked up and tracked at runtime via search; this list is no
longer the bounded "universe" it was in the simulated version.
"""

from __future__ import annotations

# (symbol, display name) — names are a fallback until live metadata arrives.
DEFAULT_SYMBOLS: list[tuple[str, str]] = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corp."),
    ("NVDA", "NVIDIA Corp."),
    ("AMZN", "Amazon.com Inc."),
    ("GOOGL", "Alphabet Inc."),
    ("META", "Meta Platforms Inc."),
    ("TSLA", "Tesla Inc."),
    ("JPM", "JPMorgan Chase & Co."),
    ("SPY", "SPDR S&P 500 ETF"),
    ("QQQ", "Invesco QQQ Trust"),
]

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "SPY", "TSLA"]
