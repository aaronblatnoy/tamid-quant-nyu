"""Unit tests for the yfinance client wrapper.

All tests are offline: ``conftest.fake_yfinance`` monkeypatches
``_yfinance_download`` with a deterministic generator so CI never hits
Yahoo. The production code path is the same in and out of tests — only
the download fn is swapped.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    import pytest

from taquantgeo_prices.yfinance_client import DEFAULT_TICKERS, fetch_ohlcv


def test_fetch_ohlcv_parses_yfinance_frame(fake_yfinance) -> None:
    """Happy path: a mocked 5-day frame round-trips into our canonical
    polars schema with integer-cent prices and an Int64 volume."""
    fake_yfinance("FRO", base_close=10.0, adjust_ratio=1.0)
    df = fetch_ohlcv("FRO", date(2026, 1, 5), date(2026, 1, 9))
    assert df.schema == {
        "ticker": pl.Utf8(),
        "as_of": pl.Date(),
        "open_cents": pl.Int64(),
        "high_cents": pl.Int64(),
        "low_cents": pl.Int64(),
        "close_cents": pl.Int64(),
        "volume": pl.Int64(),
    }
    # pd.bdate_range 2026-01-05..2026-01-09 = Mon..Fri → 5 bars
    assert df.height == 5
    assert (df["ticker"] == "FRO").all()
    # Base close 10.00 → 1000 cents on row 0; close increments by ~0.10 open +
    # 0.05 offset per row deterministically.
    assert df["close_cents"][0] == 1005  # (10.00 + 0.05) * 100
    # All cents are positive integers (no float drift, no negatives).
    for col in ("open_cents", "high_cents", "low_cents", "close_cents"):
        assert (df[col] > 0).all()


def test_fetch_ohlcv_empty_on_delisting_logs_warn(
    fake_yfinance,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A delisted / rate-limited ticker produces an empty frame and a
    WARN — but never an exception (the daily job must survive)."""
    fake_yfinance("EURN", empty=True)
    with caplog.at_level(logging.WARNING, logger="taquantgeo_prices.yfinance_client"):
        df = fetch_ohlcv("EURN", date(2026, 1, 5), date(2026, 1, 9))
    assert df.is_empty()
    assert df.schema["as_of"] == pl.Date()
    assert any("no rows" in rec.message for rec in caplog.records)


def test_ohlcv_converted_to_integer_cents(fake_yfinance) -> None:
    """Adj-Close adjustment propagates to OHL (return-series integrity)
    and every money column is an integer — the ``no-floats-for-money``
    invariant from CLAUDE.md."""
    # 2:1 split halves every adjusted price vs unadjusted Close.
    fake_yfinance("DHT", base_close=20.0, adjust_ratio=0.5)
    df = fetch_ohlcv("DHT", date(2026, 1, 5), date(2026, 1, 6))
    # Row 0: raw Open 20.00 → adj Open 20.00 * 0.5 = 10.00 → 1000 cents.
    assert df["open_cents"][0] == 1000
    # Row 0: raw Close 20.05 → Adj Close 10.025 → 1003 cents (rounded from .5).
    assert df["close_cents"][0] in {1002, 1003}  # banker's rounding tolerance
    # Every cents column is polars Int64 (no float leakage through the mapper).
    for col in ("open_cents", "high_cents", "low_cents", "close_cents", "volume"):
        assert df.schema[col] == pl.Int64()
        # Row values are Python int, not float.
        assert isinstance(df[col][0], int)


def test_fetch_ohlcv_missing_column_returns_empty_with_warn(
    fake_yfinance,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """yfinance sometimes returns a non-standard frame (schema drift,
    partial failure). Missing any of the required OHLCV columns produces
    an empty frame + WARN, not a KeyError."""
    fake_yfinance("TNK", missing_col="Adj Close")
    with caplog.at_level(logging.WARNING, logger="taquantgeo_prices.yfinance_client"):
        df = fetch_ohlcv("TNK", date(2026, 1, 5), date(2026, 1, 9))
    assert df.is_empty()
    assert any("missing expected columns" in rec.message for rec in caplog.records)


def test_fetch_ohlcv_skips_nan_bars(fake_yfinance) -> None:
    """A bar with NaN in Open/High/Low/Close is dropped (half-day holiday
    / flagged symbol). Volume-only NaN coerces to 0 but does not drop."""
    fake_yfinance("INSW", nan_rows=2)
    df = fetch_ohlcv("INSW", date(2026, 1, 5), date(2026, 1, 9))
    # 5 weekday bars, first 2 are NaN → 3 rows survive.
    assert df.height == 3


def test_fetch_ohlcv_volume_only_nan_coerces_to_zero(fake_yfinance) -> None:
    """Volume-only NaN coerces to 0 but does not drop — all weekday bars
    survive when OHLC is valid."""
    fake_yfinance("FRO", volume_nan_rows=2)
    df = fetch_ohlcv("FRO", date(2026, 1, 5), date(2026, 1, 9))
    assert df.height == 5
    assert df["volume"][0] == 0
    assert df["volume"][1] == 0
    assert df["volume"][2] > 0
    assert df["close_cents"][0] > 0


def test_fetch_ohlcv_handles_multi_index_columns(fake_yfinance) -> None:
    """yfinance returns a MultiIndex on the columns when called with a
    list of tickers. We only ever call with a single ticker, but the
    defensive droplevel keeps the path covered."""
    fake_yfinance("FRO", multi_index=True)
    df = fetch_ohlcv("FRO", date(2026, 1, 5), date(2026, 1, 9))
    assert df.height == 5


def test_all_tickers_default_list_matches_docs() -> None:
    """The default basket is load-bearing — it's named in ADR 0002
    (gap 4), ADR 0008, and the CLAUDE.md commands block. Pin the tuple
    so a casual rename upstream fails loudly in CI."""
    assert DEFAULT_TICKERS == ("FRO", "DHT", "INSW", "EURN", "TNK")
