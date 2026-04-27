"""taquantgeo_prices — historical equity prices for the shipping-equity proxy basket.

Daily OHLCV from yfinance (Yahoo Finance) stored in Postgres with integer-cent
columns per the ``no-floats-for-money`` convention in CLAUDE.md. Rationale and
source-selection trade-offs are in ``docs/adrs/0008-equity-price-source.md``.

Public surface:
- ``fetch_ohlcv(ticker, start, end) -> polars.DataFrame``
- ``DEFAULT_TICKERS`` — the proxy basket: FRO, DHT, INSW, EURN, TNK
- ``upsert_prices(session, rows) -> int``
- ``Price`` ORM model
"""

from taquantgeo_prices.models import Price
from taquantgeo_prices.persistence import upsert_prices
from taquantgeo_prices.yfinance_client import DEFAULT_TICKERS, fetch_ohlcv

__version__ = "0.0.1"

__all__ = [
    "DEFAULT_TICKERS",
    "Price",
    "fetch_ohlcv",
    "upsert_prices",
]
