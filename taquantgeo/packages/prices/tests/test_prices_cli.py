"""CLI tests for ``taq prices`` — backfill / update / show.

yfinance is mocked via ``fake_yfinance``; the DB is a tmp sqlite (both
``taquantgeo_core.db.session_scope`` *and* the CLI's imported reference
to it are redirected, because Python resolves the name at import time
in ``packages/cli/src/taquantgeo_cli/prices.py``)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from taquantgeo_cli.main import app
from taquantgeo_core.schemas import Base
from taquantgeo_prices import models as _models_register  # noqa: F401 — registers Price
from taquantgeo_prices import yfinance_client as yfc
from taquantgeo_prices.models import Price

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def sqlite_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Route ``session_scope()`` at the call-sites inside
    ``taquantgeo_cli.prices`` to a tmp sqlite DB with schema created via
    the shared metadata. ``prices``-specific tests don't need the real
    postgres container."""
    db_file = tmp_path / "cli_test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)

    @contextmanager
    def _scope() -> Iterator[Session]:
        # expire_on_commit=False matches the production session_scope;
        # without it, ORM attributes read after the session closes raise
        # DetachedInstanceError (the ``show`` CLI iterates rows post-scope).
        sess = Session(engine, expire_on_commit=False)
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    # The CLI does `from taquantgeo_core.db import session_scope`, so the
    # name in taquantgeo_cli.prices is already bound at import time. Patch
    # the import site, not (only) the source module.
    monkeypatch.setattr("taquantgeo_cli.prices.session_scope", _scope)
    return engine


def test_backfill_cli_end_to_end(
    fake_yfinance,
    sqlite_scope,
) -> None:
    """Backfill for two tickers, check that rows land with the right
    ticker label and cents math."""
    fake_yfinance("FRO", base_close=10.0)
    fake_yfinance("DHT", base_close=25.0)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "prices",
            "backfill",
            "--since",
            "2026-01-05",
            "--until",
            "2026-01-09",
            "--ticker",
            "FRO",
            "--ticker",
            "DHT",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")

    with Session(sqlite_scope) as sess:
        fro = sess.execute(select(Price).where(Price.ticker == "FRO")).scalars().all()
        dht = sess.execute(select(Price).where(Price.ticker == "DHT")).scalars().all()
    # 5 weekday bars each.
    assert len(fro) == 5
    assert len(dht) == 5
    # Row-0 close at base_close + 0.05 → (10.05, 25.05) → 1005, 2505 cents.
    fro_sorted = sorted(fro, key=lambda r: r.as_of)
    dht_sorted = sorted(dht, key=lambda r: r.as_of)
    assert fro_sorted[0].close_cents == 1005
    assert dht_sorted[0].close_cents == 2505


def test_update_cli_resumes_from_latest_as_of(
    fake_yfinance,
    sqlite_scope,
) -> None:
    """Seed one row at 2026-01-05; ``update`` should fetch from 01-06 onward
    only (the day after latest)."""
    calls: list[tuple[str, date, date]] = []

    # Wrap the fake downloader so we can assert the start date requested.
    fake_yfinance("FRO")

    original = yfc._yfinance_download

    def tracking(ticker: str, start: date, end: date):  # type: ignore[no-untyped-def]
        calls.append((ticker, start, end))
        return original(ticker, start, end)

    yfc._yfinance_download = tracking  # type: ignore[assignment]

    try:
        # Seed: one existing row for FRO at 2026-01-05.
        with Session(sqlite_scope) as sess:
            sess.add(
                Price(
                    ticker="FRO",
                    as_of=date(2026, 1, 5),
                    open_cents=100,
                    high_cents=110,
                    low_cents=90,
                    close_cents=105,
                    volume=1000,
                )
            )
            sess.commit()

        runner = CliRunner()
        result = runner.invoke(app, ["prices", "update", "--ticker", "FRO"])
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
    finally:
        yfc._yfinance_download = original  # type: ignore[assignment]

    assert calls, "update CLI did not call the downloader"
    assert calls[0][1] == date(2026, 1, 6), (
        f"expected start=2026-01-06 (day after latest); got {calls[0][1]}"
    )

    with Session(sqlite_scope) as sess:
        fro_rows = sess.execute(select(Price).where(Price.ticker == "FRO")).scalars().all()
    # Seed row (2026-01-05) + new weekday rows from 2026-01-06 onward fetched by the update call. Must be > 1 to prove new rows were persisted.
    assert len(fro_rows) > 1, "update CLI did not persist any new rows"


def test_show_cli_prints_tail(
    fake_yfinance,
    sqlite_scope,
) -> None:
    """``show`` prints the most-recent ``tail`` rows in reverse-chrono
    order. Seeding via the backfill path exercises the happy-path glue
    too."""
    fake_yfinance("INSW", base_close=30.0)

    runner = CliRunner()
    backfill_result = runner.invoke(
        app,
        [
            "prices",
            "backfill",
            "--since",
            "2026-01-05",
            "--until",
            "2026-01-09",
            "--ticker",
            "INSW",
        ],
    )
    assert backfill_result.exit_code == 0, backfill_result.stdout

    result = runner.invoke(app, ["prices", "show", "--ticker", "INSW", "--tail", "3"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    # Header + 3 rows.
    body_lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert len(body_lines) == 1 + 3
    data_lines = body_lines[1:]  # skip header
    dates = [line.split()[0] for line in data_lines]
    assert dates == sorted(dates, reverse=True), (
        f"show CLI did not emit rows in reverse-chrono order: {dates}"
    )


def test_backfill_cli_help_lists_prices() -> None:
    """The new subapp is registered on the top-level ``taq`` command."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "prices" in result.stdout.lower()
