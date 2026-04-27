"""Yahoo Finance (yfinance) client for the shipping-equity proxy basket.

Series choice: we store **adjusted close** as ``close_cents`` (and adjust the
OHLC accordingly) because backtests compare *returns*, and adjusted series
already absorb splits and dividends. Unadjusted open/high/low would drift
against the adjusted close on every corporate action and make return math
wrong. ADR 0008 covers the trade-off; the short version is: adjusted
everywhere, splits/dividends handled by yfinance, delistings handled here
by returning an empty frame and a WARN log (not an exception).

Timezones: Yahoo returns naive ``datetime64[ns]`` bars at exchange-local
midnight. We force UTC by attaching ``.date()`` — we only persist the
calendar day, not the intra-day timestamp, so timezone math never
propagates past this module.

Rate limiting / delistings: yfinance occasionally returns an empty frame
for a delisted ticker (EURN → CMB.TECH rollover, for example). The fetch
path logs a WARN and returns ``pl.DataFrame()``; the caller should not
die because one symbol in the basket dropped out.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import polars as pl

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_TICKERS: Final[tuple[str, ...]] = ("FRO", "DHT", "INSW", "EURN", "TNK")
"""Shipping-equity proxy basket. Matches ADR 0002 gap-4 rationale: these
are the five pure-play VLCC/tanker names with the tightest historical
correlation to TD3C spot. EURN rolled into CMB.TECH in 2024; yfinance
will return empty for ``EURN`` past the rollover date — the empty-frame
fallback in ``fetch_ohlcv`` keeps the job alive."""

# OHLC columns yfinance emits when `auto_adjust=False`. Used for schema
# validation and the pandas -> polars conversion.
_REQUIRED_YFINANCE_COLS: Final[tuple[str, ...]] = (
    "Open",
    "High",
    "Low",
    "Close",
    "Adj Close",
    "Volume",
)


def _yfinance_download(
    ticker: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Thin wrapper around ``yfinance.download``.

    Isolated as a module-level function so tests can monkeypatch a single
    symbol. Not part of the public surface.

    Notes:
    - ``auto_adjust=False`` so we receive BOTH ``Close`` and ``Adj Close``
      and can pin the split/dividend adjustment to a single column
      (``Adj Close``) rather than having yfinance silently rewrite every
      OHLC value.
    - ``end`` is **exclusive** in yfinance. The caller's contract treats
      ``end`` as inclusive, so we pass ``end + 1 day`` to yfinance.
    - ``progress=False`` suppresses the terminal progress bar.
    - ``threads=False`` keeps behavior deterministic on single-ticker
      calls and avoids a stray worker on job shutdown.
    """
    import yfinance as yf  # noqa: PLC0415  # lazy import — keeps cold-path CLI imports fast

    return yf.download(
        ticker,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        progress=False,
        threads=False,
    )


def fetch_ohlcv(ticker: str, start: date, end: date) -> pl.DataFrame:
    """Fetch daily OHLCV for ``ticker`` in ``[start, end]`` (inclusive).

    Returns a polars DataFrame with columns:
    ``ticker, as_of, open_cents, high_cents, low_cents, close_cents, volume``.

    All prices are **adjusted**: yfinance's ``Adj Close`` is used as the
    close, and the OHL values are scaled by the same ratio as the close
    adjustment so intra-day bar integrity is preserved after splits. This
    matches how backtests expect the series (return-comparable).

    On empty frame (delisted / rate-limited / unknown symbol): logs a
    WARN and returns an empty polars DataFrame with the canonical schema.
    Does NOT raise — the daily job must not die on one missing ticker.
    """
    try:
        raw = _yfinance_download(ticker, start, end)
    except Exception as exc:
        log.warning(
            "yfinance fetch raised for ticker=%s start=%s end=%s: %s — returning empty frame",
            ticker,
            start,
            end,
            exc,
        )
        return _empty_frame()
    if raw is None or len(raw) == 0:
        log.warning(
            "yfinance returned no rows for ticker=%s start=%s end=%s (delisted / "
            "rate-limited / unknown)",
            ticker,
            start,
            end,
        )
        return _empty_frame()

    # yfinance can return a MultiIndex on columns when called with a list;
    # we only ever call with a single ticker, but guard anyway. Take the
    # first level if present so the rest of the code sees flat names.
    if hasattr(raw.columns, "nlevels") and raw.columns.nlevels > 1:
        raw = raw.droplevel(1, axis=1)

    missing = [c for c in _REQUIRED_YFINANCE_COLS if c not in raw.columns]
    if missing:
        log.warning(
            "yfinance frame for ticker=%s missing expected columns %s; skipping",
            ticker,
            missing,
        )
        return _empty_frame()

    # Apply the adjustment ratio to OHL. Adj Close already absorbs splits
    # + dividends; if we leave OHL unadjusted, return-series math on
    # bars would mismatch after every corporate action.
    close = raw["Close"].astype(float)
    adj_close = raw["Adj Close"].astype(float)
    # Guard against divide-by-zero on bad data. Where close is 0 or NaN,
    # fall back to leaving OHL untouched (ratio = 1).
    ratio = (adj_close / close).where(close > 0, other=1.0)

    open_adj = (raw["Open"].astype(float) * ratio).to_list()
    high_adj = (raw["High"].astype(float) * ratio).to_list()
    low_adj = (raw["Low"].astype(float) * ratio).to_list()
    close_adj = adj_close.to_list()
    # Volume can be NaN on half-day / flagged bars; pandas .astype('int64')
    # raises IntCastingNaNError on NaN, so fill to 0 before cast.
    volume = raw["Volume"].fillna(0).astype("int64").to_list()

    # yfinance's index is naive DatetimeIndex at exchange-local midnight;
    # we only persist the calendar day, so timezone propagation ends here.
    as_of_dates = [d.date() if hasattr(d, "date") else d for d in raw.index.tolist()]

    rows = []
    for i, d in enumerate(as_of_dates):
        # A NaN OHLCV anywhere in the row is a yfinance bar gap (half-day
        # holiday, flagged symbol). Skip the row entirely — the gap is
        # real and filling it would be worse than omitting it.
        values = (open_adj[i], high_adj[i], low_adj[i], close_adj[i])
        if any(_is_nan(v) for v in values):
            continue
        rows.append(
            {
                "ticker": ticker,
                "as_of": d,
                "open_cents": round(open_adj[i] * 100),
                "high_cents": round(high_adj[i] * 100),
                "low_cents": round(low_adj[i] * 100),
                "close_cents": round(close_adj[i] * 100),
                "volume": int(volume[i]),
            }
        )

    if not rows:
        return _empty_frame()

    return pl.DataFrame(rows, schema=_schema())


def _schema() -> dict[str, pl.DataType]:
    return {
        "ticker": pl.Utf8(),
        "as_of": pl.Date(),
        "open_cents": pl.Int64(),
        "high_cents": pl.Int64(),
        "low_cents": pl.Int64(),
        "close_cents": pl.Int64(),
        "volume": pl.Int64(),
    }


def _empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_schema())


def _is_nan(x: object) -> bool:
    return isinstance(x, float) and math.isnan(x)
