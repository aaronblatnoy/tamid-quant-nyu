"""CLI tests for ``taq signals ic`` — end-to-end against an in-memory sqlite DB.

The DB is seeded with synthetic random signals and prices that trip the
fail-fast gate, so the CLI's exit-code-10 path and the manual-setup
append are both exercised.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy.engine import Engine

from taquantgeo_cli.main import app
from taquantgeo_core.schemas import Base
from taquantgeo_prices.models import Price
from taquantgeo_signals.compare import basket_return
from taquantgeo_signals.models import Signal


@pytest.fixture
def sqlite_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """Route ``session_scope`` at the call-site inside ``taquantgeo_cli.signals``
    to a tmp sqlite DB seeded with the shared metadata."""
    db_file = tmp_path / "ic_cli.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)

    @contextmanager
    def _scope() -> Generator[Session, None, None]:
        sess = Session(engine, expire_on_commit=False)
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    monkeypatch.setattr("taquantgeo_cli.signals.session_scope", _scope)
    return engine


def _seed_random_signals_and_prices(engine: Engine) -> None:
    """Seed signals from the canonical no-edge parquet fixture and prices
    via the same construction as ``_make_basket_prices`` in
    ``test_compare.py`` (ticker-outer iteration, np.random.default_rng(12345),
    sigma=0.012, base=100). Mirroring it byte-for-byte means this CLI test
    inherits the same deterministic FAIL_NO_EDGE verdict the unit test
    pins."""

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ic_synthetic_signal.parquet"
    sig_df = pl.read_parquet(fixture_path)
    sig_dates = sig_df["as_of"].to_list()
    sig_values = sig_df["signal"].to_list()

    # Build the trading-day calendar by iterating from the first signal date.
    start = sig_dates[0]
    trading_dates: list[date] = []
    d = start
    while len(trading_dates) < 900:
        if d.weekday() < 5:
            trading_dates.append(d)
        d = d + timedelta(days=1)

    rng = np.random.default_rng(12345)
    base = 100.0
    sigma = 0.012
    tickers = ("A", "B", "C")
    # ticker-outer / date-inner — matches _make_basket_prices.
    closes_by_ticker: dict[str, list[float]] = {t: [] for t in tickers}
    for ti, t in enumerate(tickers):
        log_p = math.log(base + ti)
        for _d in trading_dates:
            log_p += sigma * float(rng.standard_normal())
            closes_by_ticker[t].append(math.exp(log_p))

    with Session(engine) as sess:
        for d, v in zip(sig_dates, sig_values, strict=True):
            sess.add(
                Signal(
                    as_of=d,
                    route="td3c",
                    forward_demand_ton_miles=1_000_000,
                    forward_supply_count=5,
                    dark_fleet_supply_adjustment=0,
                    tightness=float(v),
                    tightness_z=None,
                    components={"supply_floor_clamped": 0},
                )
            )
        for ticker, closes in closes_by_ticker.items():
            for d_i, close in zip(trading_dates, closes, strict=True):
                sess.add(
                    Price(
                        ticker=ticker,
                        as_of=d_i,
                        open_cents=round(close * 100),
                        high_cents=round(close * 102),
                        low_cents=round(close * 98),
                        close_cents=round(close * 100),
                        volume=1_000_000,
                    )
                )
        sess.commit()


def _seed_perfect_signals_and_prices(engine: Engine) -> None:
    """Seed the same synthetic prices as ``_seed_random_signals_and_prices`` and
    tightness equal to the 5-day forward equal-weight basket log-return so the
    primary horizon is a near-perfect predictor (IC ≈ 1)."""

    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ic_synthetic_signal.parquet"
    sig_df = pl.read_parquet(fixture_path)
    sig_dates = sig_df["as_of"].to_list()

    start = sig_dates[0]
    trading_dates: list[date] = []
    d = start
    while len(trading_dates) < 900:
        if d.weekday() < 5:
            trading_dates.append(d)
        d = d + timedelta(days=1)

    rng = np.random.default_rng(12345)
    base = 100.0
    sigma = 0.012
    tickers = ("A", "B", "C")
    closes_by_ticker: dict[str, list[float]] = {t: [] for t in tickers}
    for ti, t in enumerate(tickers):
        log_p = math.log(base + ti)
        for _d in trading_dates:
            log_p += sigma * float(rng.standard_normal())
            closes_by_ticker[t].append(math.exp(log_p))

    price_rows: list[dict[str, object]] = []
    for ticker, closes in closes_by_ticker.items():
        for d_i, close in zip(trading_dates, closes, strict=True):
            price_rows.append(
                {
                    "ticker": ticker,
                    "as_of": d_i,
                    "close_cents": round(close * 100),
                }
            )
    prices_df = pl.DataFrame(
        price_rows,
        schema={"ticker": pl.String, "as_of": pl.Date, "close_cents": pl.Int64},
    )
    fwd5 = basket_return(prices_df, tickers=["A", "B", "C"], horizon_days=5)
    fwd_by_date = {
        row["as_of"]: float(row["forward_return"])
        for row in fwd5.drop_nulls("forward_return").iter_rows(named=True)
    }

    with Session(engine) as sess:
        for d in sig_dates:
            v = fwd_by_date.get(d)
            if v is None:
                continue
            sess.add(
                Signal(
                    as_of=d,
                    route="td3c",
                    forward_demand_ton_miles=1_000_000,
                    forward_supply_count=5,
                    dark_fleet_supply_adjustment=0,
                    tightness=v,
                    tightness_z=None,
                    components={"supply_floor_clamped": 0},
                )
            )
        for ticker, closes in closes_by_ticker.items():
            for d_i, close in zip(trading_dates, closes, strict=True):
                sess.add(
                    Price(
                        ticker=ticker,
                        as_of=d_i,
                        open_cents=round(close * 100),
                        high_cents=round(close * 102),
                        low_cents=round(close * 98),
                        close_cents=round(close * 100),
                        volume=1_000_000,
                    )
                )
        sess.commit()


def test_ic_cli_writes_report_and_exits_10_on_no_edge(sqlite_scope: Engine, tmp_path: Path) -> None:
    _seed_random_signals_and_prices(sqlite_scope)
    # Confirm the seed produced the expected baseline counts so test failures
    # localise quickly: 1461 signals (4y daily fixture), 900 trading days.
    with Session(sqlite_scope) as sess:
        sig_count = sess.query(Signal).count()
        prc_count = sess.query(Price).count()
    assert sig_count == 1461
    assert prc_count == 900 * 3
    out = tmp_path / "ic.md"
    manual = tmp_path / "manual_setup_required.md"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "signals",
            "ic",
            "--out",
            str(out),
            "--manual-setup-path",
            str(manual),
            "--min-history-days",
            "180",
            "--step-days",
            "30",
        ],
    )
    # Exit code 10 is the fail-fast / blocked-data signal to the harness.
    assert result.exit_code == 10, result.stdout + (result.stderr or "")
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    # Verdict is deterministic given the seed and price reconstruction; the OR
    # with INSUFFICIENT DATA was masking potential regressions in the
    # edge-vs-blocked branch.
    assert "NO EDGE DETECTED" in body, f"expected fail banner in report; got:\n{body[:500]}"
    assert manual.exists()
    manual_body = manual.read_text(encoding="utf-8")
    assert "Phase 07" in manual_body
    assert str(out) in manual_body


def test_ic_cli_pass_path_exits_zero_and_no_manual_setup_append(
    sqlite_scope: Engine, tmp_path: Path
) -> None:
    """PASS verdict: exit 0, edge banner, no manual-setup append."""
    _seed_perfect_signals_and_prices(sqlite_scope)
    out = tmp_path / "ic_pass.md"
    manual = tmp_path / "manual_setup_required.md"
    assert not manual.exists()
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "signals",
            "ic",
            "--out",
            str(out),
            "--manual-setup-path",
            str(manual),
            "--min-history-days",
            "180",
            "--step-days",
            "30",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert not manual.exists()
    body = out.read_text(encoding="utf-8")
    assert "EDGE DETECTED" in body
    assert "NO EDGE DETECTED" not in body


def test_ic_cli_filters_out_floor_clamped_signals(sqlite_scope: Engine, tmp_path: Path) -> None:
    """Snapshots with components.supply_floor_clamped == 1 are diagnostic
    only and must be dropped before regression — pin that the CLI honors
    that contract while retaining clean snapshots."""
    # Seed 5 floored snapshots plus 4 clean ones on distinct dates; post-filter
    # count must reflect only the clean rows.
    with Session(sqlite_scope) as sess:
        for i in range(5):
            sess.add(
                Signal(
                    as_of=date(2024, 1, 1) + timedelta(days=i),
                    route="td3c",
                    forward_demand_ton_miles=1_000_000,
                    forward_supply_count=0,
                    dark_fleet_supply_adjustment=0,
                    tightness=999.0,
                    tightness_z=None,
                    components={"supply_floor_clamped": 1},
                )
            )
        for j in range(4):
            sess.add(
                Signal(
                    as_of=date(2024, 2, 1) + timedelta(days=j),
                    route="td3c",
                    forward_demand_ton_miles=1_000_000,
                    forward_supply_count=5,
                    dark_fleet_supply_adjustment=0,
                    tightness=float(j),
                    tightness_z=None,
                    components={"supply_floor_clamped": 0},
                )
            )
        sess.commit()
    out = tmp_path / "ic.md"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["signals", "ic", "--out", str(out), "--manual-setup-path", str(tmp_path / "m.md")],
    )
    assert result.exit_code == 10
    assert "signal observations: 4" in result.stdout


def test_ic_cli_help_lists_subcommand() -> None:
    """The new ic subcommand is registered under signals."""
    runner = CliRunner()
    result = runner.invoke(app, ["signals", "--help"])
    assert result.exit_code == 0
    assert "ic" in result.stdout
