"""Seed universe of simulated instruments.

Each instrument is given a believable starting price, annualised volatility and
drift so the generated price paths look like real equities/ETFs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    sector: str
    price: float          # seed / reference price in USD
    volatility: float     # annualised volatility (e.g. 0.30 == 30%)
    drift: float          # annualised expected return


# A compact but recognisable universe. Numbers are seeds for the simulator,
# not live market data.
UNIVERSE: list[Instrument] = [
    Instrument("AAPL", "Apple Inc.", "Technology", 212.40, 0.26, 0.10),
    Instrument("MSFT", "Microsoft Corp.", "Technology", 438.10, 0.24, 0.12),
    Instrument("NVDA", "NVIDIA Corp.", "Semiconductors", 121.30, 0.48, 0.22),
    Instrument("AMZN", "Amazon.com Inc.", "Consumer Discretionary", 186.50, 0.31, 0.11),
    Instrument("GOOGL", "Alphabet Inc.", "Communication Services", 178.20, 0.28, 0.10),
    Instrument("META", "Meta Platforms Inc.", "Communication Services", 503.70, 0.36, 0.14),
    Instrument("TSLA", "Tesla Inc.", "Automotive", 247.80, 0.55, 0.08),
    Instrument("BRK.B", "Berkshire Hathaway", "Financials", 441.60, 0.17, 0.08),
    Instrument("JPM", "JPMorgan Chase & Co.", "Financials", 204.90, 0.23, 0.07),
    Instrument("V", "Visa Inc.", "Financials", 274.30, 0.21, 0.09),
    Instrument("WMT", "Walmart Inc.", "Consumer Staples", 67.20, 0.19, 0.08),
    Instrument("JNJ", "Johnson & Johnson", "Healthcare", 148.10, 0.16, 0.05),
    Instrument("UNH", "UnitedHealth Group", "Healthcare", 492.40, 0.27, 0.06),
    Instrument("XOM", "Exxon Mobil Corp.", "Energy", 113.80, 0.25, 0.04),
    Instrument("PG", "Procter & Gamble", "Consumer Staples", 167.50, 0.15, 0.06),
    Instrument("HD", "Home Depot Inc.", "Consumer Discretionary", 352.10, 0.22, 0.07),
    Instrument("KO", "Coca-Cola Co.", "Consumer Staples", 63.40, 0.14, 0.05),
    Instrument("DIS", "Walt Disney Co.", "Communication Services", 101.20, 0.30, 0.06),
    Instrument("NFLX", "Netflix Inc.", "Communication Services", 678.90, 0.39, 0.15),
    Instrument("AMD", "Advanced Micro Devices", "Semiconductors", 162.70, 0.49, 0.16),
    Instrument("INTC", "Intel Corp.", "Semiconductors", 31.20, 0.38, 0.02),
    Instrument("BA", "Boeing Co.", "Industrials", 178.40, 0.34, 0.03),
    Instrument("PFE", "Pfizer Inc.", "Healthcare", 28.60, 0.24, 0.03),
    Instrument("BAC", "Bank of America", "Financials", 39.80, 0.28, 0.06),
    Instrument("COST", "Costco Wholesale", "Consumer Staples", 872.30, 0.20, 0.10),
    Instrument("CVX", "Chevron Corp.", "Energy", 155.90, 0.24, 0.04),
    Instrument("ORCL", "Oracle Corp.", "Technology", 142.10, 0.29, 0.10),
    Instrument("CRM", "Salesforce Inc.", "Technology", 251.60, 0.33, 0.09),
    Instrument("SPY", "SPDR S&P 500 ETF", "Index ETF", 547.30, 0.15, 0.08),
    Instrument("QQQ", "Invesco QQQ Trust", "Index ETF", 472.80, 0.20, 0.11),
]


UNIVERSE_BY_SYMBOL: dict[str, Instrument] = {i.symbol: i for i in UNIVERSE}
