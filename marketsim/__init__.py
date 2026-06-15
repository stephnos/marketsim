"""MarketSim - a stock market app on live data, with a web GUI and an agent CLI."""

from .config import load_dotenv

# Load .env (Alpaca keys, provider selection, etc.) before anything reads env.
load_dotenv()

__version__ = "0.2.0"
