"""Sea-route distance with pair-keyed parquet cache.

Shipping tightness is ton-miles forward. "Miles" here must be the actual
sailing distance through navigable water, not great-circle, because on
PG → China routes the great-circle arc passes over land (Arabian Peninsula,
Indian subcontinent) and short-changes true demand by ~8-10%. The bias is
asymmetric with congestion: when Malacca diverts push vessels south through
Sunda, the sea-route adds ~600 NM that great-circle would silently ignore,
understating tightness precisely when we most want the signal.

We use ``searoute-py`` (1.5.x) - an MIT-licensed port of the industry
SeaRoutes waypoint graph. Our ``prefer_malacca=True`` (default for VLCC
PG → China) leaves Malacca open; ``prefer_malacca=False`` adds
``"malacca"`` to restrictions, forcing a Sunda or Lombok routing - the
realistic congestion-diversion scenario.

Quirks observed against searoute-py 1.5.0 (captured 2026-04-21):

- ``sr.searoute`` expects ``[lon, lat]`` order (NOT ``[lat, lon]``) -
  a common footgun that silently produces nonsense on a swap.
- Return is a GeoJSON Feature; distance lives at
  ``feature.properties["length"]`` with units determined by the ``units``
  kwarg. We pin ``units="naut"`` because Worldscale/Baltic quote
  nautical miles and we do not want to carry a conversion factor.
- Landlocked inputs (center of continent, etc.) are SILENTLY snapped to
  the nearest sea node before routing. A "disconnected" pair therefore
  usually does NOT raise - it returns ``length ≤ 0`` for
  non-coincident inputs. We detect that and fall back to great-circle.
- Invalid lat/lon (|lat|>90 or |lon|>180) raises ``ValueError``.
- We pin ``_BASE_RESTRICTIONS = ("northwest",)`` explicitly so every
  caller gets the same graph regardless of what searoute's library
  default is on a given release.

Cache schema (parquet, one row per unique directed
``(origin_s2id, dest_s2id)`` pair; schema locked in ``_CACHE_SCHEMA``):

  ``origin_s2id`` str, ``dest_s2id`` str,
  ``origin_lat``/``origin_lon``/``dest_lat``/``dest_lon`` float64,
  ``nautical_miles`` float64,
  ``is_great_circle_fallback`` bool,
  ``computed_at`` timestamp[us, UTC]

Primary key is the *directed* pair - the same two anchorages in the
opposite direction get their own row; searoute is approximately
symmetric but not bit-identical for the same graph.

On a truly disconnected pair (searoute raises, non-numeric length, or
zero length for non-coincident points) we fall back to great-circle and
log WARN. The row is marked ``is_great_circle_fallback=True`` so
downstream consumers can filter or flag those pairs.

Writes use a tmp-then-``os.replace`` pattern so a Ctrl-C mid-write never
truncates the cache on disk.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import polars as pl
import searoute as sr

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

log = logging.getLogger(__name__)

_EARTH_RADIUS_NM: Final = 3440.065
"""Earth mean radius in nautical miles (6371.0088 km / 1.852 km·NM⁻¹)."""

_BASE_RESTRICTIONS: Final[tuple[str, ...]] = ("northwest",)
"""Passages always excluded from the searoute graph. Pinned explicitly
rather than relying on searoute's library default, which has shifted
between versions."""

_ZERO_LENGTH_EPSILON_NM: Final = 1.0
"""Below this sea-route length (NM) with non-coincident inputs we treat
the searoute output as suspect and fall back to great-circle. 1 NM
(~1.85 km) distinguishes "genuinely same point" from "searoute snapped
both endpoints to the same node and returned zero" on landlocked
inputs."""

_CACHE_COLUMN_ORDER: Final[tuple[str, ...]] = (
    "origin_s2id",
    "dest_s2id",
    "origin_lat",
    "origin_lon",
    "dest_lat",
    "dest_lon",
    "nautical_miles",
    "is_great_circle_fallback",
    "computed_at",
)

_CACHE_SCHEMA: Final = pl.Schema(
    {
        "origin_s2id": pl.String,
        "dest_s2id": pl.String,
        "origin_lat": pl.Float64,
        "origin_lon": pl.Float64,
        "dest_lat": pl.Float64,
        "dest_lon": pl.Float64,
        "nautical_miles": pl.Float64,
        "is_great_circle_fallback": pl.Boolean,
        "computed_at": pl.Datetime(time_unit="us", time_zone="UTC"),
    }
)

_PAIRS_SCHEMA: Final = pl.Schema(
    {
        "origin_s2id": pl.String,
        "dest_s2id": pl.String,
        "origin_lat": pl.Float64,
        "origin_lon": pl.Float64,
        "dest_lat": pl.Float64,
        "dest_lon": pl.Float64,
    }
)


def great_circle_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in nautical miles."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(min(1.0, a)))
    return _EARTH_RADIUS_NM * c


def _compute_with_fallback(
    origin_lat_lon: tuple[float, float],
    dest_lat_lon: tuple[float, float],
    *,
    prefer_malacca: bool,
) -> tuple[float, bool]:
    """Return ``(nautical_miles, is_great_circle_fallback)``."""
    olat, olon = origin_lat_lon
    dlat, dlon = dest_lat_lon
    restrictions = list(_BASE_RESTRICTIONS)
    if not prefer_malacca:
        restrictions.append("malacca")
    try:
        feature = sr.searoute(
            [olon, olat],
            [dlon, dlat],
            units="naut",
            restrictions=restrictions,
        )
    except Exception as exc:  # searoute raises a variety of errors; treat all as "try fallback"
        log.warning(
            "searoute raised for (%.4f,%.4f)->(%.4f,%.4f): %s; falling back to great-circle",
            olat,
            olon,
            dlat,
            dlon,
            exc,
        )
        return great_circle_nm(olat, olon, dlat, dlon), True

    length_raw: object = None
    props = getattr(feature, "properties", None)
    if isinstance(props, dict):
        length_raw = props.get("length")
    try:
        length = float(length_raw) if length_raw is not None else float("nan")
    except (TypeError, ValueError):
        length = float("nan")
    if math.isnan(length):
        log.warning(
            "searoute returned non-numeric length %r for (%.4f,%.4f)->(%.4f,%.4f); "
            "falling back to great-circle",
            length_raw,
            olat,
            olon,
            dlat,
            dlon,
        )
        return great_circle_nm(olat, olon, dlat, dlon), True

    gc = great_circle_nm(olat, olon, dlat, dlon)
    # searoute returned ~0 NM for non-coincident points → graph has no path
    # (landlocked snap-to-same-node) → fall back to great-circle rather than
    # silently emitting zero.
    if length <= _ZERO_LENGTH_EPSILON_NM and gc > _ZERO_LENGTH_EPSILON_NM:
        log.warning(
            "searoute returned %.2f NM for non-coincident (%.4f,%.4f)->(%.4f,%.4f) "
            "(great-circle %.1f NM); falling back to great-circle",
            length,
            olat,
            olon,
            dlat,
            dlon,
            gc,
        )
        return gc, True

    return length, False


def compute_route_distance(
    origin_lat_lon: tuple[float, float],
    dest_lat_lon: tuple[float, float],
    *,
    prefer_malacca: bool = True,
) -> float:
    """Sea-route distance in nautical miles from origin to destination.

    ``prefer_malacca=False`` adds the Malacca Strait to the restriction list,
    forcing the router to go via Sunda/Lombok - the realistic VLCC
    congestion-diversion scenario.
    """
    length, _ = _compute_with_fallback(origin_lat_lon, dest_lat_lon, prefer_malacca=prefer_malacca)
    return length


def _empty_cache() -> pl.DataFrame:
    return pl.DataFrame(schema=_CACHE_SCHEMA)


def _atomic_write_parquet(df: pl.DataFrame, out_path: Path) -> None:
    """Write ``df`` to ``out_path`` via a sibling tmp + ``os.replace``.

    Guarantees that a crash mid-write leaves either the old file intact or
    the new file fully written - never a truncated parquet. Required because
    the next idempotent invocation reads ``out_path``; a corrupt cache would
    poison subsequent runs. The sibling tmp is guaranteed same-filesystem so
    os.replace is POSIX-atomic.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        df.write_parquet(tmp, compression="zstd")
        os.replace(tmp, out_path)
    except BaseException:
        # On any failure (write error, interrupt, replace failure) clean up
        # the partial tmp so it doesn't accumulate on disk between retries.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _make_row(
    origin_s2id: str,
    dest_s2id: str,
    olat: float,
    olon: float,
    dlat: float,
    dlon: float,
    *,
    prefer_malacca: bool,
    now: datetime,
) -> dict[str, object]:
    dist, fallback = _compute_with_fallback(
        (olat, olon), (dlat, dlon), prefer_malacca=prefer_malacca
    )
    return {
        "origin_s2id": origin_s2id,
        "dest_s2id": dest_s2id,
        "origin_lat": float(olat),
        "origin_lon": float(olon),
        "dest_lat": float(dlat),
        "dest_lon": float(dlon),
        "nautical_miles": dist,
        "is_great_circle_fallback": fallback,
        "computed_at": now,
    }


def build_distance_cache(
    anchorage_pairs: Iterable[tuple[str, str, float, float, float, float]],
    out_path: Path,
    *,
    prefer_malacca: bool = True,
) -> pl.DataFrame:
    """Compute distance for each pair, write parquet atomically, return the
    DataFrame.

    ``anchorage_pairs`` is an iterable of
    ``(origin_s2id, dest_s2id, origin_lat, origin_lon, dest_lat, dest_lon)``.
    Rows are emitted in input order; ``computed_at`` is set to the current
    UTC wall-clock at the start of the run.
    """
    rows = _compute_rows(anchorage_pairs, prefer_malacca=prefer_malacca)
    df = _empty_cache() if not rows else pl.DataFrame(rows, schema=_CACHE_SCHEMA)
    df = df.select(list(_CACHE_COLUMN_ORDER))
    _atomic_write_parquet(df, out_path)
    return df


def collect_unique_pairs(voyages_dir: Path) -> pl.DataFrame:
    """Scan the route-partitioned voyages parquet tree, return unique
    (origin_s2id, dest_s2id) pairs with their lat/lon.

    Rows where either anchorage_id is null or either lat/lon is null are
    dropped - nothing to compute a distance for. The first lat/lon seen
    per (origin_s2id, dest_s2id) wins; if downstream joins ever produce
    inconsistent lat/lons for the same s2id pair we log WARN with the
    count so the silent-drift case is surfaced.
    """
    parquet_paths = sorted(voyages_dir.rglob("*.parquet"))
    if not parquet_paths:
        return pl.DataFrame(schema=_PAIRS_SCHEMA)
    df = (
        pl.scan_parquet(parquet_paths)
        .select(
            pl.col("trip_start_anchorage_id").alias("origin_s2id"),
            pl.col("trip_end_anchorage_id").alias("dest_s2id"),
            pl.col("orig_lat").alias("origin_lat"),
            pl.col("orig_lon").alias("origin_lon"),
            pl.col("dest_lat").alias("dest_lat"),
            pl.col("dest_lon").alias("dest_lon"),
        )
        .drop_nulls()
        .collect()
    )
    distinct_rows = df.unique().sort(["origin_s2id", "dest_s2id"])
    pairs = distinct_rows.unique(subset=["origin_s2id", "dest_s2id"], keep="first").sort(
        ["origin_s2id", "dest_s2id"]
    )
    drift = distinct_rows.height - pairs.height
    if drift > 0:
        log.warning(
            "collect_unique_pairs: %d s2id pair(s) have inconsistent lat/lon across "
            "voyages; keeping first occurrence. Investigate upstream join stability.",
            drift,
        )
    return pairs


def compute_distances_cached(
    voyages_dir: Path,
    out_path: Path,
    *,
    force: bool = False,
    prefer_malacca: bool = True,
) -> pl.DataFrame:
    """Idempotent cache-building orchestrator.

    Collects unique anchorage pairs from voyages parquet; skips pairs
    already present in ``out_path`` unless ``force=True``; computes new
    ones; merges; writes the full cache atomically; returns it.

    Cached rows win on (origin_s2id, dest_s2id) collision - use
    ``force=True`` after a searoute version bump or an anchorage-centroid
    revision to re-baseline.
    """
    pairs = collect_unique_pairs(voyages_dir)
    if pairs.height == 0:
        log.warning("no voyages with non-null anchorage pairs under %s", voyages_dir)
        return build_distance_cache([], out_path, prefer_malacca=prefer_malacca)

    existing = _load_existing_cache(out_path) if not force else None
    if existing is None:
        log.info(
            "computing sea-route distances for %d unique anchorage pairs (force=%s)",
            pairs.height,
            force,
        )
        return build_distance_cache(_rows_to_tuples(pairs), out_path, prefer_malacca=prefer_malacca)

    existing_keys = existing.select(["origin_s2id", "dest_s2id"])
    missing = pairs.join(existing_keys, on=["origin_s2id", "dest_s2id"], how="anti")
    log.info(
        "cache %s exists: %d rows cached, %d new pairs to compute",
        out_path,
        existing.height,
        missing.height,
    )
    if missing.height == 0:
        return existing

    new_rows = _compute_rows(_rows_to_tuples(missing), prefer_malacca=prefer_malacca)
    new_df = pl.DataFrame(new_rows, schema=_CACHE_SCHEMA).select(list(_CACHE_COLUMN_ORDER))
    merged = pl.concat([existing, new_df], how="vertical").select(list(_CACHE_COLUMN_ORDER))
    _atomic_write_parquet(merged, out_path)
    return merged


def _load_existing_cache(out_path: Path) -> pl.DataFrame | None:
    """Read the existing cache if present and well-formed; else None.

    A corrupt or schema-drifted cache (e.g., left over from an older version
    missing ``is_great_circle_fallback``) is treated as "no cache" so the
    next invocation recomputes from scratch rather than crashing.
    """
    if not out_path.exists():
        return None
    try:
        df = pl.read_parquet(out_path)
    except (OSError, pl.exceptions.PolarsError):
        log.warning("existing cache at %s is unreadable; ignoring and recomputing", out_path)
        return None
    missing = [c for c in _CACHE_COLUMN_ORDER if c not in df.columns]
    if missing:
        log.warning(
            "existing cache at %s is missing columns %s; ignoring and recomputing",
            out_path,
            missing,
        )
        return None
    dtype_mismatches = [
        (c, df.schema[c], _CACHE_SCHEMA[c])
        for c in _CACHE_COLUMN_ORDER
        if df.schema[c] != _CACHE_SCHEMA[c]
    ]
    if dtype_mismatches:
        log.warning(
            "existing cache at %s has drifted dtypes %s; ignoring and recomputing",
            out_path,
            dtype_mismatches,
        )
        return None
    return df.select(list(_CACHE_COLUMN_ORDER))


def _compute_rows(
    anchorage_pairs: Iterable[tuple[str, str, float, float, float, float]],
    *,
    prefer_malacca: bool,
) -> list[dict[str, object]]:
    """Pure compute (no IO). Used by both ``build_distance_cache`` and the
    partial-cache orchestrator path so row construction is defined once."""
    now = datetime.now(tz=UTC)
    return [
        _make_row(osid, dsid, olat, olon, dlat, dlon, prefer_malacca=prefer_malacca, now=now)
        for osid, dsid, olat, olon, dlat, dlon in anchorage_pairs
    ]


def _rows_to_tuples(
    df: pl.DataFrame,
) -> list[tuple[str, str, float, float, float, float]]:
    return [
        (
            str(r["origin_s2id"]),
            str(r["dest_s2id"]),
            float(r["origin_lat"]),
            float(r["origin_lon"]),
            float(r["dest_lat"]),
            float(r["dest_lon"]),
        )
        for r in df.iter_rows(named=True)
    ]
