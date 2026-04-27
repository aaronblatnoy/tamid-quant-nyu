"""Buffered parquet writer for AIS positions, partitioned by date.

Files land at:
  <root>/positions/year=YYYY/month=MM/day=DD/<unix_ts>_<rand>.parquet

Hive-style partitioning so DuckDB / polars can prune by date.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from pathlib import Path

    from taquantgeo_ais.models import PositionReport

logger = logging.getLogger(__name__)


class ParquetArchiver:
    def __init__(
        self,
        root: Path,
        *,
        max_rows: int = 5_000,
        max_seconds: float = 30.0,
    ) -> None:
        self.root = root
        self.max_rows = max_rows
        self.max_seconds = max_seconds
        self._buf: list[dict[str, object]] = []
        self._last_flush = time.monotonic()

    def add(self, mmsi: int, report: PositionReport, recv_at: datetime) -> None:
        self._buf.append(
            {
                "mmsi": mmsi,
                "ts": recv_at,
                "lat": report.Latitude,
                "lon": report.Longitude,
                "sog": report.Sog,
                "cog": report.Cog,
                "heading": report.TrueHeading,
                "nav_status": report.NavigationalStatus,
            }
        )
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        if (
            len(self._buf) >= self.max_rows
            or (time.monotonic() - self._last_flush) >= self.max_seconds
        ):
            self.flush()

    def flush(self) -> int:
        if not self._buf:
            return 0
        rows = self._buf
        self._buf = []
        self._last_flush = time.monotonic()

        now = datetime.now(UTC)
        partition = self.root / "positions" / f"year={now:%Y}" / f"month={now:%m}" / f"day={now:%d}"
        partition.mkdir(parents=True, exist_ok=True)
        fname = f"{int(now.timestamp())}_{secrets.token_hex(4)}.parquet"
        path = partition / fname

        df = pl.DataFrame(rows)
        df.write_parquet(path, compression="zstd")
        logger.info("flushed %d rows to %s", len(rows), path)
        return len(rows)
