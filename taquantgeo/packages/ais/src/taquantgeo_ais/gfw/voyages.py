"""Load GFW voyages CSVs, join to anchorages, filter by route.

Voyages schema (c4_pipe_v3):
  ssvid, vessel_id, trip_id, trip_start, trip_end,
  trip_start_anchorage_id, trip_end_anchorage_id,
  trip_start_visit_id, trip_end_visit_id
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from pathlib import Path

    from taquantgeo_ais.gfw.routes import Route


def load_voyages(path: Path) -> pl.DataFrame:
    """Load a monthly voyages CSV with typed timestamps."""
    return pl.read_csv(
        path, schema_overrides={"trip_start": pl.Utf8, "trip_end": pl.Utf8}
    ).with_columns(
        trip_start=pl.col("trip_start").str.strptime(
            pl.Datetime, "%Y-%m-%d %H:%M:%S UTC", strict=False
        ),
        trip_end=pl.col("trip_end").str.strptime(
            pl.Datetime, "%Y-%m-%d %H:%M:%S UTC", strict=False
        ),
    )


def join_to_anchorages(voyages: pl.DataFrame, anchorages: pl.DataFrame) -> pl.DataFrame:
    """Enrich voyages with origin/destination lat/lon/iso3/label.

    Left join — voyages with an unknown anchorage_id pass through with nulls.
    """
    a = anchorages.select(["s2id", "iso3", "label", "lat", "lon"])

    orig = a.rename(
        {
            "s2id": "trip_start_anchorage_id",
            "iso3": "orig_iso3",
            "label": "orig_label",
            "lat": "orig_lat",
            "lon": "orig_lon",
        }
    )
    dest = a.rename(
        {
            "s2id": "trip_end_anchorage_id",
            "iso3": "dest_iso3",
            "label": "dest_label",
            "lat": "dest_lat",
            "lon": "dest_lon",
        }
    )

    return (
        voyages.join(orig, on="trip_start_anchorage_id", how="left")
        .join(dest, on="trip_end_anchorage_id", how="left")
        .with_columns(duration_hours=(pl.col("trip_end") - pl.col("trip_start")).dt.total_hours())
        .with_columns(duration_days=pl.col("duration_hours") / 24.0)
    )


def filter_by_route(
    enriched: pl.DataFrame,
    route: Route,
    *,
    apply_duration_filter: bool = True,
) -> pl.DataFrame:
    """Return voyages whose origin/destination match the route.

    If `apply_duration_filter`, also narrows to the route's typical transit
    band — useful for heuristic VLCC filtering on TD3C where we don't yet
    have per-vessel ship type (a smaller tanker or bulker moves slower).
    """
    origin = list(route.origin_iso3)
    destination = list(route.destination_iso3)
    df = enriched.filter(pl.col("orig_iso3").is_in(origin) & pl.col("dest_iso3").is_in(destination))
    if apply_duration_filter:
        lo, hi = route.typical_transit_days
        df = df.filter((pl.col("duration_days") >= lo) & (pl.col("duration_days") <= hi))
    return df.with_columns(route=pl.lit(route.name))
