"""Tests for ``taquantgeo_prices.persistence`` and alembic 0003.

The sqlite path exercises delete-then-add branch; the alembic-upgrade
and Postgres ON CONFLICT tests run under the ``integration`` marker
against the real postgres service.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date
from typing import TYPE_CHECKING

import polars as pl
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from taquantgeo_core.schemas import Base
from taquantgeo_prices import models as _models_register  # noqa: F401 — register Price on Base
from taquantgeo_prices.models import Price
from taquantgeo_prices.persistence import upsert_prices

if TYPE_CHECKING:
    from pathlib import Path


def _row(
    ticker: str,
    as_of: date,
    *,
    close_cents: int = 1000,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "as_of": as_of,
        "open_cents": close_cents - 5,
        "high_cents": close_cents + 20,
        "low_cents": close_cents - 25,
        "close_cents": close_cents,
        "volume": 1_000_000,
    }


@pytest.fixture
def sqlite_session(tmp_path: Path):
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_upsert_prices_inserts_rows(sqlite_session: Session) -> None:
    rows = [_row("FRO", date(2026, 3, 10)), _row("FRO", date(2026, 3, 11))]
    n = upsert_prices(sqlite_session, rows)
    sqlite_session.commit()
    assert n == 2
    result = sqlite_session.execute(select(Price).order_by(Price.as_of)).scalars().all()
    assert len(result) == 2
    assert result[0].close_cents == 1000


def test_upsert_idempotent(sqlite_session: Session) -> None:
    """Re-running the same (ticker, as_of) overwrites — no duplicate row."""
    upsert_prices(sqlite_session, [_row("FRO", date(2026, 3, 10), close_cents=1000)])
    sqlite_session.commit()
    upsert_prices(sqlite_session, [_row("FRO", date(2026, 3, 10), close_cents=1500)])
    sqlite_session.commit()
    rows = sqlite_session.execute(select(Price)).scalars().all()
    assert len(rows) == 1
    assert rows[0].close_cents == 1500


def test_upsert_accepts_polars_dataframe(sqlite_session: Session) -> None:
    """``fetch_ohlcv`` returns a polars DataFrame; the upsert path must
    accept it without an explicit ``.iter_rows(named=True)`` at the call
    site."""
    df = pl.DataFrame(
        [
            _row("DHT", date(2026, 3, 10)),
            _row("DHT", date(2026, 3, 11)),
        ]
    )
    n = upsert_prices(sqlite_session, df)
    sqlite_session.commit()
    assert n == 2
    assert sqlite_session.execute(select(Price)).scalars().all()


def test_upsert_empty_rows_is_noop(sqlite_session: Session) -> None:
    assert upsert_prices(sqlite_session, []) == 0
    assert upsert_prices(sqlite_session, pl.DataFrame()) == 0


def test_prices_table_has_expected_columns(sqlite_session: Session) -> None:
    engine = sqlite_session.get_bind()
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("prices")}
    expected = {
        "id",
        "ticker",
        "as_of",
        "open_cents",
        "high_cents",
        "low_cents",
        "close_cents",
        "volume",
        "created_at",
    }
    assert expected <= cols


def test_upsert_different_tickers_coexist(sqlite_session: Session) -> None:
    """Same as_of, different ticker → two rows; the unique index is on
    the pair, not the date alone."""
    upsert_prices(
        sqlite_session,
        [
            _row("FRO", date(2026, 3, 10)),
            _row("DHT", date(2026, 3, 10)),
        ],
    )
    sqlite_session.commit()
    rows = sqlite_session.execute(select(Price)).scalars().all()
    assert {r.ticker for r in rows} == {"FRO", "DHT"}


@pytest.mark.integration
def test_alembic_upgrade_head_creates_prices_table() -> None:
    """Run ``alembic upgrade head`` against real Postgres and assert
    ``prices`` exists with a unique index on (ticker, as_of)."""
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://taq:taq@localhost:5432/taquantgeo",
    )
    proc = subprocess.run(
        ["alembic", "upgrade", "head"],  # noqa: S607 — alembic on $PATH
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"alembic failed: {proc.stderr}"

    engine = create_engine(db_url)
    insp = inspect(engine)
    assert "prices" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("prices")}
    assert {"open_cents", "high_cents", "low_cents", "close_cents", "volume"} <= cols
    idxs = insp.get_indexes("prices")
    uq = [ix for ix in idxs if ix.get("unique")]
    assert any(set(ix["column_names"]) == {"ticker", "as_of"} for ix in uq), (
        f"no unique index on (ticker, as_of); found {uq}"
    )


@pytest.mark.integration
def test_upsert_prices_postgres_on_conflict_is_idempotent() -> None:
    """Two upserts for the same (ticker, as_of) must yield one row with
    the second call's values — no IntegrityError even though the unique
    index is in place."""
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://taq:taq@localhost:5432/taquantgeo",
    )
    proc = subprocess.run(
        ["alembic", "upgrade", "head"],  # noqa: S607
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"alembic failed: {proc.stderr}"

    iso_ticker = "IT_CONFLICT"
    engine = create_engine(db_url)
    with Session(engine) as sess:
        sess.execute(Price.__table__.delete().where(Price.ticker == iso_ticker))
        sess.commit()

        upsert_prices(sess, [_row(iso_ticker, date(2099, 1, 1), close_cents=1000)])
        upsert_prices(sess, [_row(iso_ticker, date(2099, 1, 1), close_cents=2000)])
        sess.commit()

        rows = sess.execute(select(Price).where(Price.ticker == iso_ticker)).scalars().all()
        assert len(rows) == 1
        assert rows[0].close_cents == 2000

        sess.execute(Price.__table__.delete().where(Price.ticker == iso_ticker))
        sess.commit()
