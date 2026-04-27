"""Load and query GFW's named_anchorages CSV.

Schema (v2_pipe_v3):
  s2id, lat, lon, label, sublabel, label_source, iso3,
  distance_from_shore_m, drift_radius, dock
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from pathlib import Path


def load_anchorages(path: Path) -> pl.DataFrame:
    """Load an anchorages CSV into a typed DataFrame.

    Raises on missing columns so we catch schema drift early.
    """
    df = pl.read_csv(path)
    required = {"s2id", "lat", "lon", "label", "iso3"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"anchorages file {path} missing columns: {missing}")
    return df


def filter_by_iso3(
    anchorages: pl.DataFrame, iso3_codes: frozenset[str] | set[str] | list[str]
) -> pl.DataFrame:
    """Return anchorages whose iso3 is in the given set."""
    codes = list(iso3_codes)
    return anchorages.filter(pl.col("iso3").is_in(codes))
