"""Fixed Phase 1 universe and semantic-version constants."""

from __future__ import annotations

from hashlib import sha256
from types import MappingProxyType

BENCHMARK_SYMBOL = "SPY"
SECTOR_SYMBOLS = (
    "XLC",
    "XLY",
    "XLP",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLB",
    "XLRE",
    "XLK",
    "XLU",
)
MARKET_INTELLIGENCE_UNIVERSE = (BENCHMARK_SYMBOL, *SECTOR_SYMBOLS)

SECTOR_NAMES = MappingProxyType(
    {
        "XLC": "Communication Services",
        "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples",
        "XLE": "Energy",
        "XLF": "Financials",
        "XLV": "Health Care",
        "XLI": "Industrials",
        "XLB": "Materials",
        "XLRE": "Real Estate",
        "XLK": "Technology",
        "XLU": "Utilities",
    }
)

PIPELINE_NAME = "market_intelligence_sectors_us"
METRIC_VERSION = "market_intelligence_v1"
NORMALIZATION_VERSION = "market_intelligence_adjusted_ohlcv_v2"
PRICE_BASIS = "yahoo_adjusted_ohlc_provider_volume"
METRIC_SEMANTICS = "ohlcv_derived_proxy"
LATEST_POINTER_KEY = "latest_market_intelligence_sectors_us"
UNIVERSE_HASH = sha256(
    "|".join(MARKET_INTELLIGENCE_UNIVERSE).encode("ascii")
).hexdigest()
