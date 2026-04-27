"""Tests for ``taquantgeo_jobs.daily_signal`` and the ``taq signals``
CLI wrapper.

We point the job at a tmp directory containing tiny parquet artefacts
mirroring the canonical layout (voyages tree + registry + distance
cache + dark-fleet). No DB; ``persist=False``.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from taquantgeo_cli.main import app
from taquantgeo_jobs.daily_signal import run_once
from taquantgeo_signals.models import Signal

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def fake_data_tree(tmp_path: Path) -> Path:
    """Lay down the canonical ``data/processed`` tree in ``tmp_path`` so
    ``run_once`` can read it with only a ``voyages_dir`` et al. override.
    """
    voyages_dir = tmp_path / "voyages" / "route=td3c" / "year=2026" / "month=03"
    voyages_dir.mkdir(parents=True)
    pl.DataFrame(
        [
            {
                "ssvid": 111111111,
                "vessel_id": "vid-1",
                "trip_id": "t-1",
                "trip_start": datetime(2026, 3, 1, 0, 0, 0),
                "trip_end": None,
                "trip_start_anchorage_id": "s2_rt",
                "trip_end_anchorage_id": "s2_ng",
                "orig_iso3": "SAU",
                "orig_label": "RAS TANURA",
                "orig_lat": 26.70,
                "orig_lon": 50.18,
                "dest_iso3": "CHN",
                "dest_label": "NINGBO",
                "dest_lat": 29.87,
                "dest_lon": 121.55,
                "route": "td3c",
            }
        ]
    ).write_parquet(voyages_dir / "voyages.parquet")

    # Ballast partition (td3c_ballast) so the on-disk ballast load path in
    # run_once is exercised. Ning-bo → Ras Tanura, trip_start on 2026-03-01
    # puts the ETA comfortably inside the 15-day window from as_of=03-15.
    ballast_dir = tmp_path / "voyages" / "route=td3c_ballast" / "year=2026" / "month=03"
    ballast_dir.mkdir(parents=True)
    pl.DataFrame(
        [
            {
                "ssvid": 111111111,
                "vessel_id": "vid-1",
                "trip_id": "b-t-1",
                "trip_start": datetime(2026, 3, 1, 0, 0, 0),
                "trip_end": None,
                "trip_start_anchorage_id": "s2_ng",
                "trip_end_anchorage_id": "s2_rt",
                "orig_iso3": "CHN",
                "orig_label": "NINGBO",
                "orig_lat": 29.87,
                "orig_lon": 121.55,
                "dest_iso3": "SAU",
                "dest_label": "RAS TANURA",
                "dest_lat": 26.70,
                "dest_lon": 50.18,
                "route": "td3c_ballast",
            }
        ]
    ).write_parquet(ballast_dir / "voyages.parquet")

    registry_path = tmp_path / "vessel_registry.parquet"
    pl.DataFrame(
        [
            {"mmsi": 111111111, "is_vlcc_candidate": True},
        ]
    ).write_parquet(registry_path)

    distance_cache_path = tmp_path / "distance_cache.parquet"
    pl.DataFrame(
        [
            {"origin_s2id": "s2_rt", "dest_s2id": "s2_ng", "nautical_miles": 5920.0},
            {"origin_s2id": "s2_ng", "dest_s2id": "s2_rt", "nautical_miles": 5920.0},
        ]
    ).write_parquet(distance_cache_path)

    dark_fleet_path = tmp_path / "dark_fleet_candidates.parquet"
    pl.DataFrame(
        {
            "mmsi": [None],
            "detection_timestamp": [datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC)],
            "nearest_anchorage_id": ["s2_rt"],
            "nearest_anchorage_label": ["RAS TANURA"],
            "has_matching_voyage": [False],
        },
        schema={
            "mmsi": pl.Int64,
            "detection_timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "nearest_anchorage_id": pl.String,
            "nearest_anchorage_label": pl.String,
            "has_matching_voyage": pl.Boolean,
        },
    ).write_parquet(dark_fleet_path)

    return tmp_path


def test_run_once_reads_canonical_layout(fake_data_tree: Path) -> None:
    """End-to-end: reads td3c laden + td3c_ballast partitions, registry,
    distance cache, and dark-fleet parquet from disk. Pins the shape of
    the canonical tree the scheduler/CLI will read in production."""
    snap = run_once(
        date(2026, 3, 15),
        voyages_dir=fake_data_tree / "voyages",
        registry_path=fake_data_tree / "vessel_registry.parquet",
        distance_cache_path=fake_data_tree / "distance_cache.parquet",
        dark_fleet_path=fake_data_tree / "dark_fleet_candidates.parquet",
        ballast_voyages_dir=fake_data_tree / "voyages",
        persist=False,
    )
    assert snap.forward_demand_ton_miles == round(270_000 * 5920.0)
    assert snap.dark_fleet_supply_adjustment == 1
    # Ballast partition, trip_start 2026-03-01 with ~5920 NM / 13 kn
    # = ~19-day ETA, lands inside the 15-day horizon from 03-15, supply = 1.
    assert snap.forward_supply_count == 1
    assert snap.as_of == date(2026, 3, 15)


def test_run_once_defaults_to_today_utc(fake_data_tree: Path) -> None:
    snap = run_once(
        None,
        voyages_dir=fake_data_tree / "voyages",
        registry_path=fake_data_tree / "vessel_registry.parquet",
        distance_cache_path=fake_data_tree / "distance_cache.parquet",
        dark_fleet_path=fake_data_tree / "dark_fleet_candidates.parquet",
        ballast_voyages_dir=fake_data_tree / "voyages",
        persist=False,
    )
    assert snap.as_of == datetime.now(tz=UTC).date()


def test_run_once_tolerates_missing_parquets(tmp_path: Path) -> None:
    """Running against an empty tree must produce a zero-zero snapshot,
    not crash. The CI `alembic upgrade` smoke + phase-04 first-ever-
    invocation both hit this path."""
    snap = run_once(
        date(2026, 3, 15),
        voyages_dir=tmp_path / "does_not_exist",
        registry_path=tmp_path / "nope.parquet",
        distance_cache_path=tmp_path / "nope2.parquet",
        dark_fleet_path=tmp_path / "nope3.parquet",
        ballast_voyages_dir=tmp_path / "nope4",
        persist=False,
    )
    assert snap.forward_demand_ton_miles == 0
    assert snap.ratio == 0.0


def test_cli_compute_tightness_end_to_end(fake_data_tree: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "signals",
            "compute-tightness",
            "--as-of",
            "2026-03-15",
            "--voyages-dir",
            str(fake_data_tree / "voyages"),
            "--registry-path",
            str(fake_data_tree / "vessel_registry.parquet"),
            "--distance-cache-path",
            str(fake_data_tree / "distance_cache.parquet"),
            "--dark-fleet-path",
            str(fake_data_tree / "dark_fleet_candidates.parquet"),
            "--ballast-voyages-dir",
            str(fake_data_tree / "voyages"),
            "--no-persist",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    payload = json.loads(result.stdout)
    assert payload["as_of"] == "2026-03-15"
    assert payload["route"] == "td3c"
    assert payload["dark_fleet_supply_adjustment"] == 1


def test_cli_help_lists_signals() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "signals" in result.stdout.lower()


@pytest.mark.integration
def test_run_once_persist_writes_to_postgres(fake_data_tree: Path) -> None:
    """Integration: ``persist=True`` (a) writes via Postgres ON CONFLICT
    (b) reads prior snapshots through the real DB round-trip (with <30
    prior rows the z-score correctly resolves to None, but the query →
    polars round-trip is still exercised). Hermetic — cleans up the
    route-scoped rows it wrote."""
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

    iso_route = "td3c_itest_persist"
    engine = create_engine(db_url)
    # Clean any rows a prior failed run might have left.
    with Session(engine) as sess:
        sess.execute(Signal.__table__.delete().where(Signal.route == iso_route))
        sess.commit()

    snap = run_once(
        date(2026, 3, 15),
        route=iso_route,
        voyages_dir=fake_data_tree / "voyages",
        registry_path=fake_data_tree / "vessel_registry.parquet",
        distance_cache_path=fake_data_tree / "distance_cache.parquet",
        dark_fleet_path=fake_data_tree / "dark_fleet_candidates.parquet",
        ballast_voyages_dir=fake_data_tree / "voyages",
        persist=True,
    )
    # `iso_route` doesn't match any partition on disk → empty voyages frame →
    # zero demand. That still pins the DB write path (ON CONFLICT), which is
    # what this test cares about.
    assert snap.forward_demand_ton_miles == 0
    assert snap.z_score_90d is None  # <30 prior samples

    with Session(engine) as sess:
        rows = sess.execute(select(Signal).where(Signal.route == iso_route)).scalars().all()
        assert len(rows) == 1
        assert rows[0].as_of == date(2026, 3, 15)
        sess.execute(Signal.__table__.delete().where(Signal.route == iso_route))
        sess.commit()
