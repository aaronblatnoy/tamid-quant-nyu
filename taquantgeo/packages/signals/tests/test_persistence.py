"""Tests for ``taquantgeo_signals.persistence`` and alembic 0002.

The upsert idempotency test runs against sqlite (portable path); the
alembic-upgrade test runs against Postgres under the ``integration``
marker (CI brings up a service container).
"""

from __future__ import annotations

import os
import subprocess
from datetime import date
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from taquantgeo_core.schemas import Base
from taquantgeo_signals import models as _models_register  # noqa: F401  # register Signal on Base
from taquantgeo_signals.models import Signal
from taquantgeo_signals.persistence import upsert_snapshot
from taquantgeo_signals.tightness import TightnessSnapshot

if TYPE_CHECKING:
    from pathlib import Path


def _snapshot(as_of: date, ratio: float, *, route: str = "td3c") -> TightnessSnapshot:
    return TightnessSnapshot(
        as_of=as_of,
        route=route,
        forward_demand_ton_miles=10_000,
        forward_supply_count=5,
        dark_fleet_supply_adjustment=1,
        ratio=ratio,
        z_score_90d=None,
        components={"in_progress_laden_voyages": 3},
    )


@pytest.fixture
def sqlite_session(tmp_path: Path):
    """Fresh sqlite DB with the signals table created via the ORM
    metadata. Both ``Vessel`` and ``Signal`` register on the shared
    ``Base``, so both tables are created here."""
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_upsert_snapshot_inserts_row(sqlite_session: Session) -> None:
    snap = _snapshot(date(2026, 3, 15), 123.0)
    upsert_snapshot(sqlite_session, snap)
    sqlite_session.commit()
    rows = sqlite_session.execute(select(Signal)).scalars().all()
    assert len(rows) == 1
    assert rows[0].tightness == 123.0
    assert rows[0].components == {"in_progress_laden_voyages": 3}


def test_upsert_snapshot_idempotent(sqlite_session: Session) -> None:
    """Running twice on the same (as_of, route) results in one row — the
    second call overwrites the first."""
    snap1 = _snapshot(date(2026, 3, 15), 100.0)
    snap2 = _snapshot(date(2026, 3, 15), 200.0)
    upsert_snapshot(sqlite_session, snap1)
    sqlite_session.commit()
    upsert_snapshot(sqlite_session, snap2)
    sqlite_session.commit()
    rows = sqlite_session.execute(select(Signal)).scalars().all()
    assert len(rows) == 1
    assert rows[0].tightness == 200.0


def test_upsert_snapshot_different_routes_are_distinct(sqlite_session: Session) -> None:
    """Same as_of but different route → two rows."""
    a = _snapshot(date(2026, 3, 15), 100.0, route="td3c")
    b = _snapshot(date(2026, 3, 15), 200.0, route="td3c_ballast")
    upsert_snapshot(sqlite_session, a)
    upsert_snapshot(sqlite_session, b)
    sqlite_session.commit()
    rows = sqlite_session.execute(select(Signal)).scalars().all()
    assert len(rows) == 2


def test_signals_table_has_expected_columns(sqlite_session: Session) -> None:
    """Freeze the column set so migrations can't silently drift."""

    engine = sqlite_session.get_bind()
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("signals")}
    expected = {
        "id",
        "as_of",
        "route",
        "forward_demand_ton_miles",
        "forward_supply_count",
        "dark_fleet_supply_adjustment",
        "tightness",
        "tightness_z",
        "components",
        "created_at",
    }
    assert expected <= cols


@pytest.mark.integration
def test_alembic_upgrade_head_creates_signals_table() -> None:
    """Integration: run ``alembic upgrade head`` against the real
    Postgres container, assert ``signals`` exists with the expected
    unique index. Skipped outside integration runs."""
    # The integration container supplies DATABASE_URL; respect whatever
    # the CI harness set, but fall back to the local docker-compose URL.
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://taq:taq@localhost:5432/taquantgeo",
    )
    # Run alembic; if it fails (e.g. postgres not available) the test
    # should error out loudly rather than silently skip.
    proc = subprocess.run(
        ["alembic", "upgrade", "head"],  # noqa: S607 — alembic on $PATH is the project convention
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"alembic failed: {proc.stderr}"

    engine = create_engine(db_url)
    insp = inspect(engine)
    assert "signals" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("signals")}
    assert "dark_fleet_supply_adjustment" in cols
    assert "components" in cols
    # Unique index exists on (as_of, route)
    idxs = insp.get_indexes("signals")
    uq = [ix for ix in idxs if ix.get("unique")]
    assert any(set(ix["column_names"]) == {"as_of", "route"} for ix in uq), (
        f"no unique index on (as_of, route); found {uq}"
    )


@pytest.mark.integration
def test_upsert_snapshot_postgres_on_conflict_is_idempotent() -> None:
    """Integration: the production upsert path uses Postgres
    ``ON CONFLICT DO UPDATE``. The sqlite path exercises a different
    (delete-then-add) branch; this test pins the real one.

    Two upserts for the same (as_of, route) must yield exactly one row
    with the second call's values; no IntegrityError even though the
    unique index is in place."""
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://taq:taq@localhost:5432/taquantgeo",
    )
    # Ensure 0002 is applied.
    proc = subprocess.run(
        ["alembic", "upgrade", "head"],  # noqa: S607 — alembic on $PATH
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"alembic failed: {proc.stderr}"

    engine = create_engine(db_url)
    # Use a unique route label + far-future date so the test is isolated
    # from any ambient data (including a prior phase-04 smoke run).
    iso_route = "td3c_itest_conflict"
    with Session(engine) as sess:
        # Clean slate in case a prior run left rows.
        sess.execute(Signal.__table__.delete().where(Signal.route == iso_route))
        sess.commit()

        snap1 = TightnessSnapshot(
            as_of=date(2099, 1, 1),
            route=iso_route,
            forward_demand_ton_miles=100,
            forward_supply_count=2,
            dark_fleet_supply_adjustment=1,
            ratio=50.0,
            z_score_90d=None,
            components={"it": 1, "median": 5.5},
        )
        snap2 = TightnessSnapshot(
            as_of=date(2099, 1, 1),
            route=iso_route,
            forward_demand_ton_miles=200,
            forward_supply_count=3,
            dark_fleet_supply_adjustment=0,
            ratio=66.67,
            z_score_90d=1.5,
            components={"it": 2, "median": 6.1},
        )
        upsert_snapshot(sess, snap1)
        upsert_snapshot(sess, snap2)
        sess.commit()

        rows = sess.execute(select(Signal).where(Signal.route == iso_route)).scalars().all()
        assert len(rows) == 1
        assert rows[0].tightness == 66.67
        assert rows[0].tightness_z == 1.5
        assert rows[0].components == {"it": 2, "median": 6.1}

        # Cleanup — keep the integration slice hermetic.
        sess.execute(Signal.__table__.delete().where(Signal.route == iso_route))
        sess.commit()
