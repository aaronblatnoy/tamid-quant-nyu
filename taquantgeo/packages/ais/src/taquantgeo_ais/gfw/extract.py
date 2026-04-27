"""End-to-end GFW voyages extractor.

Pipeline: load monthly CSV → join anchorages → filter to route →
write partitioned parquet under data/processed/voyages/.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import polars as pl

from taquantgeo_ais.gfw.anchorages import load_anchorages
from taquantgeo_ais.gfw.voyages import filter_by_route, join_to_anchorages, load_voyages

if TYPE_CHECKING:
    from pathlib import Path

    from taquantgeo_ais.gfw.routes import Route

logger = logging.getLogger(__name__)


def extract_route(
    voyages_csv: Path,
    anchorages_csv: Path,
    route: Route,
    out_dir: Path,
    *,
    apply_duration_filter: bool = True,
) -> pl.DataFrame:
    """Extract one month of a single route's voyages to parquet.

    Output path: <out_dir>/route=<name>/year=YYYY/month=MM/<stem>.parquet
    Returns the filtered DataFrame for reporting.
    """
    logger.info("loading voyages: %s", voyages_csv)
    voyages = load_voyages(voyages_csv)
    logger.info("loaded %d voyages", voyages.shape[0])

    anchorages = load_anchorages(anchorages_csv)
    enriched = join_to_anchorages(voyages, anchorages)
    filtered = filter_by_route(enriched, route, apply_duration_filter=apply_duration_filter)
    logger.info(
        "filtered to %d voyages on route=%s (apply_duration=%s)",
        filtered.shape[0],
        route.name,
        apply_duration_filter,
    )

    if filtered.shape[0] == 0:
        logger.warning("no voyages after filter; skipping write")
        return filtered

    # Partition by year/month of the voyage start timestamp (ignore route NULLs
    # which fall through as unknown).
    sample_ts = filtered.select(pl.col("trip_start").min()).item()
    partition = (
        out_dir / f"route={route.name}" / f"year={sample_ts.year}" / f"month={sample_ts.month:02d}"
    )
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / f"{voyages_csv.stem}.parquet"
    filtered.write_parquet(path, compression="zstd")
    logger.info("wrote %s (%d rows)", path, filtered.shape[0])
    return filtered
