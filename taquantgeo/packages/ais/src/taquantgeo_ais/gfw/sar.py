"""Sentinel-1 SAR vessel detections → dark-fleet candidate cross-reference.

ADR 0002 Gap 2: GFW's C4 voyages systematically exclude dark-fleet VLCCs
(AIS off during loading or transit). GFW publishes monthly Sentinel-1
SAR vessel-detection CSVs that see vessels via satellite radar independently
of AIS. Spatially joining SAR detections to known VLCC loading terminals
and temporally joining to our AIS-reported voyages yields a dark-fleet
supply proxy: every SAR hit at a loading anchorage that has NO
corresponding AIS-reported voyage in a ±3-day window.

See `docs/adrs/0006-sar-dark-fleet-cross-ref.md` for the rationale behind
the defaults (buffer_km=10, time_window_days=3, min_length_m=200).

SAR CSV schema (``sar_vessel_detections_pipev4_YYYYMM.csv``, observed
2026-04-21):

  ``scene_id`` str -- Sentinel-1 scene identifier (multiple detections
    per scene)
  ``timestamp`` str -- ``YYYY-MM-DD HH:MM:SS UTC`` (whitespace-separated)
  ``lat``/``lon`` f64 -- detection center
  ``presence_score`` f64 -- SAR detection-quality score
  ``length_m`` f64 -- SAR-measured vessel length (always populated)
  ``mmsi`` int64 nullable -- matched AIS vessel (NULL == dark / unmatched)
  ``matching_score`` f64 -- SAR↔AIS match quality (high → AIS-reporting)
  ``fishing_score`` f64 -- learned fishing-vessel probability
  ``matched_category`` str -- one of ``{unmatched, other, cargo, fishing,
    noisy_vessel, passenger, seismic_vessel, gear, carrier, bunker}``

Quirks observed on the 2026-03 monthly CSV (107,257 detections globally):

- ``mmsi`` is NULL for ~27% of detections — these are the strongest
  dark-fleet candidates but NULL by itself is not sufficient evidence
  (unmatched small fishing boats also lack MMSI). Pair with
  ``length_m >= 200`` for VLCC/Suezmax-class filtering.
- ``length_m`` distribution is heavily right-skewed: median ~63 m,
  p75 ~146 m, max 422 m. The 200-m cutoff retains ~20% of detections
  globally but the surviving set is dominated by tankers and bulkers.
- ``matched_category`` is populated even for NULL-MMSI rows (derived from
  learned features of the SAR footprint). ``unmatched`` is the modal
  category (~40%). We do NOT filter on ``matched_category`` in this
  phase because a misclassification of a genuine dark VLCC as ``other``
  or ``noisy_vessel`` would silently drop it; length alone is the
  defensible first cut.
- Timestamps are UTC (suffix ``" UTC"``). We strip the suffix and parse
  as naive, then localize to UTC, to produce a ``timestamp[us, UTC]``.
- SAR coverage of mid-ocean is thin — Sentinel-1 is designed for coastal
  and EEZ surveillance, not open ocean. Consequence: dark-fleet
  detection *in transit* is unreliable; detection *at loading terminals*
  (what this module targets) is well-covered. Documented as a negative
  consequence in ADR 0006.

Output schema (parquet, locked in ``_DARK_FLEET_SCHEMA``):

  ``mmsi`` int64 nullable,
  ``detection_timestamp`` timestamp[us, UTC],
  ``lat``/``lon`` f64,
  ``length_m`` f64 nullable,
  ``nearest_anchorage_id`` str,
  ``nearest_anchorage_label`` str,
  ``distance_to_anchorage_km`` f64,
  ``has_matching_voyage`` bool,
  ``matching_voyage_trip_id`` str nullable,
  ``source_csv`` str
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

log = logging.getLogger(__name__)


MIN_VESSEL_LENGTH_M: Final = 200.0
"""Below this SAR-measured length we drop the detection. 200 m is smaller
than every VLCC (>300 m) and Suezmax (~270 m) and excludes containerships,
product tankers, bulkers — the large-vessel tail we care about for crude
freight tightness is well above this line."""

DEFAULT_BUFFER_KM: Final = 10.0
"""Great-circle distance from a major loading terminal's nearest GFW
anchorage below which a SAR detection counts as "at the terminal". 10 km
is generous enough to cover a VLCC sitting at an approach anchorage
rather than at the berth itself, while tight enough to exclude vessels
merely transiting the terminal's iso3."""

DEFAULT_TIME_WINDOW_DAYS: Final = 3
"""±N-day match window between SAR detection time and voyage trip_start
at the same anchorage. Worldscale crude-lifting operations typically
span 24-48 hours at the berth; 3 days accommodates that plus the
Sentinel-1 overpass cadence (~6 days at equator, ~2-3 days at mid-latitudes)
and clock-skew between SAR timestamps and AIS voyage starts."""

_EARTH_RADIUS_KM: Final = 6371.0088
"""Earth mean radius in km (IUGG)."""


_DARK_FLEET_COLUMN_ORDER: Final[tuple[str, ...]] = (
    "mmsi",
    "detection_timestamp",
    "lat",
    "lon",
    "length_m",
    "nearest_anchorage_id",
    "nearest_anchorage_label",
    "distance_to_anchorage_km",
    "has_matching_voyage",
    "matching_voyage_trip_id",
    "source_csv",
)

_DARK_FLEET_SCHEMA: Final = pl.Schema(
    {
        "mmsi": pl.Int64,
        "detection_timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
        "lat": pl.Float64,
        "lon": pl.Float64,
        "length_m": pl.Float64,
        "nearest_anchorage_id": pl.String,
        "nearest_anchorage_label": pl.String,
        "distance_to_anchorage_km": pl.Float64,
        "has_matching_voyage": pl.Boolean,
        "matching_voyage_trip_id": pl.String,
        "source_csv": pl.String,
    }
)

_REQUIRED_SAR_COLUMNS: Final = frozenset(
    {"scene_id", "timestamp", "lat", "lon", "length_m", "mmsi"}
)

# polars cannot infer numeric dtypes from a 0-row CSV (all cols come back as
# String); a header-only partial-month file would then poison a multi-file
# concat. Pin the dtypes we care about on read.
_SAR_CSV_SCHEMA_OVERRIDES: Final[dict[str, pl.DataType]] = {
    "scene_id": pl.String(),
    "timestamp": pl.String(),
    "lat": pl.Float64(),
    "lon": pl.Float64(),
    "presence_score": pl.Float64(),
    "length_m": pl.Float64(),
    "mmsi": pl.Int64(),
    "matching_score": pl.Float64(),
    "fishing_score": pl.Float64(),
    "matched_category": pl.String(),
}


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in km."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(min(1.0, a)))
    return _EARTH_RADIUS_KM * c


def load_sar_csv(path: Path) -> pl.DataFrame:
    """Load one SAR CSV, parse timestamps, return a typed DataFrame.

    Adds a ``source_csv`` column carrying the filename so merged multi-file
    outputs stay attributable. Raises on missing required columns so
    schema drift is caught immediately.
    """
    # Peek at the header first so we can only override dtypes for columns that
    # actually exist (schema_overrides on a missing column raises).
    header_df = pl.read_csv(path, n_rows=0)
    overrides = {k: v for k, v in _SAR_CSV_SCHEMA_OVERRIDES.items() if k in header_df.columns}
    df = pl.read_csv(path, schema_overrides=overrides)
    missing = _REQUIRED_SAR_COLUMNS - set(df.columns)
    if missing:
        msg = f"SAR csv {path} missing required columns: {sorted(missing)}"
        raise ValueError(msg)
    # "2026-03-01 02:59:51 UTC" → naive datetime, then localize UTC.
    df = df.with_columns(
        pl.col("timestamp")
        .str.strip_chars()
        .str.strip_suffix(" UTC")
        .str.strptime(pl.Datetime(time_unit="us"), format="%Y-%m-%d %H:%M:%S", strict=True)
        .dt.replace_time_zone("UTC")
        .alias("detection_timestamp"),
        pl.lit(path.name).alias("source_csv"),
    )
    return df


def resolve_terminal_anchorages(
    anchorages: pl.DataFrame,
    terminals: Iterable[tuple[float, float, str, str]],
) -> pl.DataFrame:
    """Map each ``(lat, lon, canonical_name, iso3)`` terminal to its nearest
    GFW anchorage (constrained to the same iso3). Returns a DataFrame with
    columns ``s2id, label, iso3, lat, lon, terminal_name,
    distance_to_terminal_km``. One row per terminal; rows where no
    same-iso3 anchorage exists are dropped with a WARN.

    Restricting the search to same-iso3 prevents pathological cross-border
    matches (e.g. Fujairah-UAE matching the nearest Omani anchorage).
    """
    rows: list[dict[str, object]] = []
    for t_lat, t_lon, t_name, t_iso3 in terminals:
        pool = anchorages.filter(pl.col("iso3") == t_iso3)
        if pool.is_empty():
            log.warning(
                "no GFW anchorages for iso3=%s when resolving terminal %r; skipping",
                t_iso3,
                t_name,
            )
            continue
        dists = [
            great_circle_km(t_lat, t_lon, lat, lon)
            for lat, lon in zip(
                pool.get_column("lat").to_list(),
                pool.get_column("lon").to_list(),
                strict=True,
            )
        ]
        best_idx = min(range(len(dists)), key=dists.__getitem__)
        best = pool.row(best_idx, named=True)
        rows.append(
            {
                "s2id": best["s2id"],
                "label": best["label"],
                "iso3": best["iso3"],
                "lat": float(best["lat"]),
                "lon": float(best["lon"]),
                "terminal_name": t_name,
                "distance_to_terminal_km": float(dists[best_idx]),
            }
        )
    if not rows:
        return pl.DataFrame(
            schema={
                "s2id": pl.String,
                "label": pl.String,
                "iso3": pl.String,
                "lat": pl.Float64,
                "lon": pl.Float64,
                "terminal_name": pl.String,
                "distance_to_terminal_km": pl.Float64,
            }
        )
    return pl.DataFrame(rows)


def filter_near_terminals(
    sar_df: pl.DataFrame,
    anchorages: pl.DataFrame,
    terminals: Iterable[tuple[float, float, str, str]],
    *,
    buffer_km: float = DEFAULT_BUFFER_KM,
) -> pl.DataFrame:
    """Keep SAR rows within ``buffer_km`` of any terminal's nearest anchorage.

    Annotates each surviving row with ``nearest_anchorage_id``,
    ``nearest_anchorage_label``, ``distance_to_anchorage_km``. For each
    SAR row, the nearest terminal-anchorage (across all terminals) wins;
    ties are broken by anchorage s2id sort order for determinism.

    Implementation: build the small set of terminal anchorages
    (len(terminals) rows — O(10)), then for each SAR row compute
    great-circle to each terminal anchorage and keep the minimum. This is
    O(N_sar x N_terminals) which is fine because N_terminals is bounded
    by our hand-curated list; a naive SAR x anchorage cross-join would be
    O(N_sar x N_anchorages_global) ≈ orders of magnitude more work.
    """
    terminal_ancs = resolve_terminal_anchorages(anchorages, terminals)
    if terminal_ancs.is_empty() or sar_df.is_empty():
        return pl.DataFrame(
            schema={
                **sar_df.schema,
                "nearest_anchorage_id": pl.String,
                "nearest_anchorage_label": pl.String,
                "distance_to_anchorage_km": pl.Float64,
            },
        )

    term_lats = terminal_ancs.get_column("lat").to_list()
    term_lons = terminal_ancs.get_column("lon").to_list()
    term_s2ids = terminal_ancs.get_column("s2id").to_list()
    term_labels = terminal_ancs.get_column("label").to_list()

    nearest_s2ids: list[str | None] = []
    nearest_labels: list[str | None] = []
    nearest_dists: list[float | None] = []
    keep_mask: list[bool] = []
    for lat, lon in zip(
        sar_df.get_column("lat").to_list(),
        sar_df.get_column("lon").to_list(),
        strict=True,
    ):
        best_dist = math.inf
        best_i = -1
        for i, (tlat, tlon) in enumerate(zip(term_lats, term_lons, strict=True)):
            d = great_circle_km(lat, lon, tlat, tlon)
            # Stable tiebreak: prefer the lexicographically earlier s2id when
            # distances are equal within float noise — keeps output
            # deterministic across runs regardless of terminal ordering.
            if d < best_dist or (
                d == best_dist and best_i >= 0 and term_s2ids[i] < term_s2ids[best_i]
            ):
                best_dist = d
                best_i = i
        if best_i >= 0 and best_dist <= buffer_km:
            keep_mask.append(True)
            nearest_s2ids.append(str(term_s2ids[best_i]))
            nearest_labels.append(str(term_labels[best_i]))
            nearest_dists.append(float(best_dist))
        else:
            keep_mask.append(False)
            nearest_s2ids.append(None)
            nearest_labels.append(None)
            nearest_dists.append(None)

    annotated = sar_df.with_columns(
        pl.Series("nearest_anchorage_id", nearest_s2ids, dtype=pl.String),
        pl.Series("nearest_anchorage_label", nearest_labels, dtype=pl.String),
        pl.Series("distance_to_anchorage_km", nearest_dists, dtype=pl.Float64),
        pl.Series("__keep", keep_mask, dtype=pl.Boolean),
    )
    return annotated.filter(pl.col("__keep")).drop("__keep")


def _best_voyage_match(
    anc_id: str | None,
    det_ts: datetime | None,
    voyages_by_anc: Mapping[str, list[tuple[datetime, str]]],
    window: timedelta,
) -> tuple[bool, str | None]:
    """Return ``(has_match, trip_id)`` for one SAR row against its anchorage's voyages; picks the voyage with the smallest |delta| within ``window``."""
    if anc_id is None or det_ts is None:
        return False, None
    candidates = voyages_by_anc.get(str(anc_id), [])
    if not candidates:
        return False, None
    det_aware = det_ts if det_ts.tzinfo is not None else det_ts.replace(tzinfo=UTC)
    best: tuple[timedelta, str] | None = None
    for ts, tid in candidates:
        delta = ts - det_aware
        if delta < timedelta(0):
            delta = -delta
        if delta <= window and (best is None or delta < best[0]):
            best = (delta, tid)
    if best is not None:
        return True, best[1]
    return False, None


def cross_reference_with_voyages(
    sar_df: pl.DataFrame,
    voyages_df: pl.DataFrame,
    *,
    time_window_days: int = DEFAULT_TIME_WINDOW_DAYS,
) -> pl.DataFrame:
    """Annotate SAR rows with the nearest voyage departing from the same anchorage within ``±time_window_days``.

    For each SAR row (expected: already filtered by ``filter_near_terminals``,
    so ``nearest_anchorage_id`` is populated), find a voyage in ``voyages_df``
    whose ``trip_start_anchorage_id`` matches and whose ``trip_start`` is
    within ±``time_window_days`` of ``detection_timestamp``.

    If multiple voyages match, the one with the smallest absolute time
    delta wins (first by trip_start order on ties). Output adds
    ``has_matching_voyage`` (bool) and ``matching_voyage_trip_id``
    (str nullable).

    ``voyages_df`` is expected to have columns
    ``trip_id, trip_start, trip_start_anchorage_id``; ``trip_start`` may
    be tz-naive or UTC-aware (both are normalised to UTC-naive comparison
    internally).
    """
    if sar_df.is_empty():
        return sar_df.with_columns(
            pl.lit(None).cast(pl.Boolean).alias("has_matching_voyage"),
            pl.lit(None).cast(pl.String).alias("matching_voyage_trip_id"),
        )

    required = {"trip_id", "trip_start", "trip_start_anchorage_id"}
    missing = required - set(voyages_df.columns)
    if missing:
        msg = f"voyages_df missing required columns: {sorted(missing)}"
        raise ValueError(msg)

    # Normalise voyages timestamps to UTC for the time-delta comparison.
    ts_dtype = voyages_df.schema.get("trip_start")
    if isinstance(ts_dtype, pl.Datetime) and ts_dtype.time_zone is None:
        voyages_df = voyages_df.with_columns(pl.col("trip_start").dt.replace_time_zone("UTC"))
    elif isinstance(ts_dtype, pl.Datetime):
        voyages_df = voyages_df.with_columns(pl.col("trip_start").dt.convert_time_zone("UTC"))

    window = timedelta(days=time_window_days)

    # Pre-group voyages by anchorage for O(N_sar x avg_voyages_per_anchorage)
    # rather than SAR x voyages cross-join.
    voyages_by_anc: dict[str, list[tuple[datetime, str]]] = {}
    for row in voyages_df.select(["trip_start_anchorage_id", "trip_start", "trip_id"]).iter_rows(
        named=True
    ):
        anc = row["trip_start_anchorage_id"]
        ts = row["trip_start"]
        tid = row["trip_id"]
        if anc is None or ts is None or tid is None:
            continue
        # Ensure tz-aware (UTC) for subtraction.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        voyages_by_anc.setdefault(str(anc), []).append((ts, str(tid)))

    has_match: list[bool] = []
    trip_ids: list[str | None] = []
    for anc_id, det_ts in zip(
        sar_df.get_column("nearest_anchorage_id").to_list(),
        sar_df.get_column("detection_timestamp").to_list(),
        strict=True,
    ):
        hm, tid = _best_voyage_match(anc_id, det_ts, voyages_by_anc, window)
        has_match.append(hm)
        trip_ids.append(tid)

    return sar_df.with_columns(
        pl.Series("has_matching_voyage", has_match, dtype=pl.Boolean),
        pl.Series("matching_voyage_trip_id", trip_ids, dtype=pl.String),
    )


def _apply_length_filter(sar_df: pl.DataFrame, min_length_m: float) -> pl.DataFrame:
    """Drop SAR rows whose ``length_m`` is null or below ``min_length_m``."""
    return sar_df.filter(pl.col("length_m").is_not_null() & (pl.col("length_m") >= min_length_m))


def _finalise_output(annotated: pl.DataFrame) -> pl.DataFrame:
    """Cast to the locked dark-fleet schema and drop intermediate columns."""
    return annotated.select(
        pl.col("mmsi").cast(pl.Int64, strict=False).alias("mmsi"),
        pl.col("detection_timestamp").cast(
            pl.Datetime(time_unit="us", time_zone="UTC"), strict=False
        ),
        pl.col("lat").cast(pl.Float64),
        pl.col("lon").cast(pl.Float64),
        pl.col("length_m").cast(pl.Float64, strict=False),
        pl.col("nearest_anchorage_id").cast(pl.String),
        pl.col("nearest_anchorage_label").cast(pl.String),
        pl.col("distance_to_anchorage_km").cast(pl.Float64),
        pl.col("has_matching_voyage").cast(pl.Boolean),
        pl.col("matching_voyage_trip_id").cast(pl.String),
        pl.col("source_csv").cast(pl.String),
    )


def build_dark_fleet_candidates(
    sar_csvs: Iterable[Path],
    anchorages: pl.DataFrame,
    voyages_df: pl.DataFrame,
    terminals: Iterable[tuple[float, float, str, str]],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    min_length_m: float = MIN_VESSEL_LENGTH_M,
    buffer_km: float = DEFAULT_BUFFER_KM,
    time_window_days: int = DEFAULT_TIME_WINDOW_DAYS,
) -> pl.DataFrame:
    """Orchestrate: load → date-window → length-filter → terminal-proximity
    → voyage cross-reference → cast to output schema.

    Returns the full annotated DataFrame (both matched and unmatched rows)
    so callers can report coverage. Filter on ``has_matching_voyage ==
    False`` to get dark-fleet candidates proper.
    """
    frames: list[pl.DataFrame] = []
    for path in sar_csvs:
        raw = load_sar_csv(path)
        frames.append(raw)
    if not frames:
        return pl.DataFrame(schema=_DARK_FLEET_SCHEMA)

    sar = pl.concat(frames, how="vertical_relaxed")
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        sar = sar.filter(pl.col("detection_timestamp") >= since)
    if until is not None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        sar = sar.filter(pl.col("detection_timestamp") <= until)

    sar = _apply_length_filter(sar, min_length_m)
    if sar.is_empty():
        return pl.DataFrame(schema=_DARK_FLEET_SCHEMA)

    near = filter_near_terminals(sar, anchorages, terminals, buffer_km=buffer_km)
    if near.is_empty():
        return pl.DataFrame(schema=_DARK_FLEET_SCHEMA)

    xref = cross_reference_with_voyages(near, voyages_df, time_window_days=time_window_days)
    return _finalise_output(xref).select(list(_DARK_FLEET_COLUMN_ORDER))


def _atomic_write_parquet(df: pl.DataFrame, out_path: Path) -> None:
    """Write ``df`` to ``out_path`` via a sibling tmp + ``os.replace``.

    Guarantees that a crash mid-write leaves either the old file intact or
    the new file fully written — never a truncated parquet. Matches the
    pattern in ``distance._atomic_write_parquet`` so a Ctrl-C during a
    scheduled ingest cannot corrupt the cache downstream jobs read.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        df.write_parquet(tmp, compression="zstd")
        os.replace(tmp, out_path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def ingest_sar(
    sar_dir: Path,
    anchorages: pl.DataFrame,
    voyages_df: pl.DataFrame,
    terminals: Iterable[tuple[float, float, str, str]],
    out_path: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    min_length_m: float = MIN_VESSEL_LENGTH_M,
    buffer_km: float = DEFAULT_BUFFER_KM,
    time_window_days: int = DEFAULT_TIME_WINDOW_DAYS,
) -> pl.DataFrame:
    """End-to-end: discover all CSVs under ``sar_dir``, orchestrate
    ``build_dark_fleet_candidates``, write parquet, return DataFrame.

    ``sar_dir`` is globbed for ``*.csv``; the date window is applied to
    row-level ``detection_timestamp`` (not filename parsing) so partial
    month files and daily drops both work without special cases.
    """
    csvs = sorted(p for p in sar_dir.glob("*.csv") if p.is_file())
    if not csvs:
        log.warning("no SAR CSVs under %s; writing empty parquet", sar_dir)
        empty = pl.DataFrame(schema=_DARK_FLEET_SCHEMA)
        _atomic_write_parquet(empty, out_path)
        return empty

    df = build_dark_fleet_candidates(
        csvs,
        anchorages,
        voyages_df,
        terminals,
        since=since,
        until=until,
        min_length_m=min_length_m,
        buffer_km=buffer_km,
        time_window_days=time_window_days,
    )

    _atomic_write_parquet(df, out_path)
    log.info(
        "wrote %d SAR candidate rows to %s (%d dark / no-voyage-match)",
        df.height,
        out_path,
        df.filter(~pl.col("has_matching_voyage")).height if df.height else 0,
    )
    return df


def load_voyages_for_crossref(voyages_dir: Path) -> pl.DataFrame:
    """Scan the route-partitioned voyages parquet tree, return the columns
    needed for cross-reference (``trip_id, trip_start,
    trip_start_anchorage_id``). Returns an empty DataFrame when no
    parquet files are present."""
    paths = sorted(voyages_dir.rglob("*.parquet"))
    if not paths:
        return pl.DataFrame(
            schema={
                "trip_id": pl.String,
                "trip_start": pl.Datetime(time_unit="us", time_zone=None),
                "trip_start_anchorage_id": pl.String,
            }
        )
    return (
        pl.scan_parquet(paths)
        .select(["trip_id", "trip_start", "trip_start_anchorage_id"])
        .collect()
    )
