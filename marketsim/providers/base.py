"""Common provider interface for MarketSim market-data sources.

All providers expose the same surface so the engine can stay source-agnostic:

    search(query, limit) -> list[dict]   # {symbol, name, sector, exchange, type}
    quote(symbol, no_cache=False) -> dict # normalised quote (see keys below)
    history(symbol, range_) -> dict        # {symbol, range, points[], change, ...}

Normalised quote keys:
    symbol, name, sector, exchange, currency, price, change, changePercent,
    open, prevClose, dayHigh, dayLow, yearHigh, yearLow, volume,
    marketState ("OPEN"/"CLOSED"/"UNKNOWN"), asOf (epoch seconds)

Providers also expose:
    source_name: str
    in_cooldown() -> bool            # True while backing off after a 429
    cooldown_remaining() -> float
"""

from __future__ import annotations


class ProviderError(Exception):
    """Raised when a data source is unreachable or returns no usable data."""
