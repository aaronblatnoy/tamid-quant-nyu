"""Shared fixtures for ``packages/prices`` tests.

``_yfinance_download`` is monkeypatched at the module level rather than
``yfinance.download`` itself. The reason: the real import happens inside
``_yfinance_download``, so patching at ``yfinance.download`` alone would
miss the lazy-import path (the function imports ``yfinance`` at call
time). Patching the wrapper keeps every test network-free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

if TYPE_CHECKING:
    from datetime import date


def _business_days(start: date, end: date) -> list[pd.Timestamp]:
    """NASDAQ-ish weekday index, no holiday filtering. Matches how
    yfinance lays out bars in simple smoke cases."""
    idx = pd.bdate_range(start=pd.Timestamp(start), end=pd.Timestamp(end))
    return list(idx)


def make_yfinance_frame(
    start: date,
    end: date,
    *,
    base_close: float = 10.0,
    adjust_ratio: float = 1.0,
    nan_rows: int = 0,
    volume_nan_rows: int = 0,
) -> pd.DataFrame:
    """Build a pandas DataFrame resembling ``yfinance.download`` output.

    Columns: Open, High, Low, Close, Adj Close, Volume (the
    ``auto_adjust=False`` shape we request). ``adjust_ratio`` scales
    ``Adj Close`` against ``Close`` so tests can verify our adjustment
    pass-through. ``nan_rows`` plants NaN values on the first N rows to
    test the NaN-row skipping branch. ``volume_nan_rows`` plants NaN on
    ``Volume`` only (OHLC stays valid) for the Volume-only-NaN branch.
    """
    idx = _business_days(start, end)
    n = len(idx)
    opens = [base_close + i * 0.10 for i in range(n)]
    highs = [o + 0.20 for o in opens]
    lows = [o - 0.20 for o in opens]
    closes = [o + 0.05 for o in opens]
    adj_closes = [c * adjust_ratio for c in closes]
    volumes = [1_000_000 + i * 100 for i in range(n)]
    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Adj Close": adj_closes,
            "Volume": volumes,
        },
        index=pd.DatetimeIndex(idx, name="Date"),
    )
    if nan_rows > 0:
        for i in range(min(nan_rows, len(df))):
            df.iloc[i, 0] = float("nan")  # Open
    if volume_nan_rows > 0:
        for i in range(min(volume_nan_rows, len(df))):
            df.iloc[i, df.columns.get_loc("Volume")] = float("nan")
    return df


@pytest.fixture
def fake_yfinance(monkeypatch: pytest.MonkeyPatch):
    """Replaces ``_yfinance_download`` with a deterministic, offline
    generator. Per-ticker behaviour controlled by the ``configure`` hook:

        configure("FRO", empty=False, adjust_ratio=0.9)
        configure("EURN", empty=True)   # simulate delisting
    """
    behaviours: dict[str, dict[str, object]] = {}

    def configure(
        ticker: str,
        *,
        empty: bool = False,
        adjust_ratio: float = 1.0,
        base_close: float = 10.0,
        raise_exc: Exception | None = None,
        multi_index: bool = False,
        missing_col: str | None = None,
        nan_rows: int = 0,
        volume_nan_rows: int = 0,
    ) -> None:
        behaviours[ticker] = {
            "empty": empty,
            "adjust_ratio": adjust_ratio,
            "base_close": base_close,
            "raise_exc": raise_exc,
            "multi_index": multi_index,
            "missing_col": missing_col,
            "nan_rows": nan_rows,
            "volume_nan_rows": volume_nan_rows,
        }

    def fake_download(ticker: str, start: date, end: date) -> pd.DataFrame:
        cfg = behaviours.get(ticker, {})
        if cfg.get("raise_exc") is not None:
            raise cfg["raise_exc"]  # type: ignore[misc]
        if cfg.get("empty"):
            return pd.DataFrame()
        # Monkeypatched at the ``_yfinance_download`` wrapper — ``end`` here
        # is the caller's inclusive end (the wrapper applies the +1-day
        # exclusive conversion inside its yfinance call), so the fake frame
        # covers [start, end] directly.
        df = make_yfinance_frame(
            start,
            end,
            base_close=float(cfg.get("base_close", 10.0)),
            adjust_ratio=float(cfg.get("adjust_ratio", 1.0)),
            nan_rows=int(cfg.get("nan_rows", 0)),
            volume_nan_rows=int(cfg.get("volume_nan_rows", 0)),
        )
        if cfg.get("missing_col"):
            df = df.drop(columns=[cfg["missing_col"]])  # type: ignore[arg-type]
        if cfg.get("multi_index"):
            df.columns = pd.MultiIndex.from_product([df.columns.tolist(), [ticker]])
        return df

    monkeypatch.setattr(
        "taquantgeo_prices.yfinance_client._yfinance_download",
        fake_download,
    )
    return configure
