"""CLI commands for the equity-price proxy basket."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

import typer
from sqlalchemy import func, select

from taquantgeo_core.config import settings
from taquantgeo_core.db import session_scope
from taquantgeo_prices.models import Price
from taquantgeo_prices.persistence import upsert_prices
from taquantgeo_prices.yfinance_client import DEFAULT_TICKERS, fetch_ohlcv

if TYPE_CHECKING:
    import polars as pl

log = logging.getLogger(__name__)

prices_app = typer.Typer(
    name="prices",
    help="Daily OHLCV for the shipping-equity proxy basket.",
    no_args_is_help=True,
)


def _fetch_one(ticker: str, start: date, end: date) -> pl.DataFrame:
    log.info("fetching %s [%s, %s]", ticker, start, end)
    return fetch_ohlcv(ticker, start, end)


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@prices_app.command("backfill")
def backfill(
    since: Annotated[
        str,
        typer.Option(help="First day to fetch, YYYY-MM-DD. Default: 2017-01-01."),
    ] = "2017-01-01",
    until: Annotated[
        str,
        typer.Option(help="Last day to fetch (inclusive). Default: today UTC."),
    ] = "",
    ticker: Annotated[
        list[str] | None,
        typer.Option(
            "--ticker",
            help="Repeatable; overrides the default basket (FRO DHT INSW EURN TNK).",
        ),
    ] = None,
) -> None:
    """Bulk-fetch historical OHLCV and upsert into Postgres."""
    _configure_logging()
    start = date.fromisoformat(since)
    end = date.fromisoformat(until) if until else datetime.now(tz=UTC).date()
    tickers = list(ticker) if ticker else list(DEFAULT_TICKERS)
    total_written = 0
    with session_scope() as sess:
        for sym in tickers:
            df = _fetch_one(sym, start, end)
            if df.is_empty():
                typer.echo(f"{sym}: 0 rows (skipped)")
                continue
            n = upsert_prices(sess, df)
            total_written += n
            typer.echo(f"{sym}: {n} rows upserted")
    typer.echo(f"done. total upserted: {total_written}")


@prices_app.command("update")
def update(
    ticker: Annotated[
        list[str] | None,
        typer.Option(
            "--ticker",
            help="Repeatable; overrides the default basket.",
        ),
    ] = None,
) -> None:
    """Fetch from the last ``as_of`` per ticker to today and upsert."""
    _configure_logging()
    tickers = list(ticker) if ticker else list(DEFAULT_TICKERS)
    today = datetime.now(tz=UTC).date()
    total_written = 0
    with session_scope() as sess:
        for sym in tickers:
            latest: date | None = sess.execute(
                select(func.max(Price.as_of)).where(Price.ticker == sym)
            ).scalar_one()
            # Resume from the day after the latest persisted close. If the
            # table is empty for this ticker, default to the same 2017
            # anchor as ``backfill`` so a fresh DB can be brought current
            # with one command.
            start = (latest + timedelta(days=1)) if latest is not None else date(2017, 1, 1)
            if start > today:
                typer.echo(f"{sym}: up to date (latest={latest})")
                continue
            df = _fetch_one(sym, start, today)
            if df.is_empty():
                typer.echo(f"{sym}: 0 rows (no new bars)")
                continue
            n = upsert_prices(sess, df)
            total_written += n
            typer.echo(f"{sym}: {n} rows upserted (from {start})")
    typer.echo(f"done. total upserted: {total_written}")


@prices_app.command("show")
def show(
    ticker: Annotated[str, typer.Option(help="Ticker symbol.")] = "FRO",
    tail: Annotated[int, typer.Option(help="Number of most recent rows to print.")] = 10,
) -> None:
    """Print the most recent ``tail`` rows for ``ticker``."""
    _configure_logging()
    with session_scope() as sess:
        rows = (
            sess.execute(
                select(Price).where(Price.ticker == ticker).order_by(Price.as_of.desc()).limit(tail)
            )
            .scalars()
            .all()
        )
    if not rows:
        typer.echo(f"no rows for ticker={ticker}")
        return
    typer.echo(f"{'as_of':<12} {'open':>10} {'high':>10} {'low':>10} {'close':>10} {'volume':>14}")
    for r in rows:
        typer.echo(
            f"{r.as_of.isoformat():<12} "
            f"{r.open_cents / 100:>10.2f} "
            f"{r.high_cents / 100:>10.2f} "
            f"{r.low_cents / 100:>10.2f} "
            f"{r.close_cents / 100:>10.2f} "
            f"{r.volume:>14}"
        )
