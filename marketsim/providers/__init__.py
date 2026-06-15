"""Market-data providers for MarketSim."""

from __future__ import annotations

import os

from .alpaca import AlpacaError, AlpacaProvider
from .base import ProviderError
from .yahoo import YahooError, YahooProvider

__all__ = [
    "ProviderError",
    "YahooProvider", "YahooError",
    "AlpacaProvider", "AlpacaError",
    "make_provider",
]


def make_provider(name: str | None = None):
    """Build the configured data provider.

    Selection order:
      1. explicit ``name`` argument,
      2. ``MARKETSIM_DATA_PROVIDER`` env var (``alpaca`` | ``yahoo``),
      3. ``alpaca`` if Alpaca credentials are present, else ``yahoo``.

    Falls back to Yahoo if Alpaca is requested but can't initialise.
    """
    name = (name or os.environ.get("MARKETSIM_DATA_PROVIDER") or "").strip().lower()
    if not name:
        has_keys = os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY")
        name = "alpaca" if has_keys else "yahoo"
    if name == "alpaca":
        try:
            return AlpacaProvider()
        except ProviderError:
            return YahooProvider()
    return YahooProvider()
