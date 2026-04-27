"""ParquetArchiver tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

from taquantgeo_ais.archiver import ParquetArchiver
from taquantgeo_ais.models import PositionReport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def report() -> PositionReport:
    return PositionReport(
        UserID=538003170,
        Latitude=25.5,
        Longitude=55.2,
        Sog=12.3,
        Cog=90.0,
        TrueHeading=91,
        NavigationalStatus=0,
    )


def test_flush_writes_parquet(tmp_path: Path, report: PositionReport) -> None:
    arch = ParquetArchiver(tmp_path, max_rows=10, max_seconds=3600)
    now = datetime.now(UTC)
    arch.add(report.UserID, report, now)
    arch.add(report.UserID, report, now)
    n = arch.flush()
    assert n == 2

    files = list(tmp_path.rglob("*.parquet"))
    assert len(files) == 1
    df = pl.read_parquet(files[0])
    assert df.height == 2
    assert df["mmsi"].to_list() == [report.UserID, report.UserID]
    assert df["lat"].to_list() == [report.Latitude, report.Latitude]


def test_size_threshold_triggers_flush(tmp_path: Path, report: PositionReport) -> None:
    arch = ParquetArchiver(tmp_path, max_rows=2, max_seconds=3600)
    now = datetime.now(UTC)
    arch.add(report.UserID, report, now)
    assert list(tmp_path.rglob("*.parquet")) == []
    arch.add(report.UserID, report, now)  # max_rows reached → auto-flush
    assert len(list(tmp_path.rglob("*.parquet"))) == 1


def test_flush_empty_buffer_is_noop(tmp_path: Path) -> None:
    arch = ParquetArchiver(tmp_path)
    assert arch.flush() == 0
    assert list(tmp_path.rglob("*.parquet")) == []
