"""TD3C freight-tightness signal core.

This is the founding math of the product. Changing any equation in this
module without a new ADR silently invalidates every backtest, every IC
study, every position we hold. Full rationale and alternatives are in
``docs/adrs/0007-tightness-signal-definition.md``; this module docstring
is the executable summary.

Signal definition
-----------------

The tightness signal on TD3C is a ratio of *forward cargo demand* to
*forward ballast supply*, standardised against a trailing 90-day
baseline.

**Forward demand (ton-miles):**

    forward_demand_ton_miles(as_of) =
        Σ { cargo_tons(v) * remaining_distance_nm(v, as_of)
            : v ∈ in_progress_laden_TD3C_voyages(as_of) }

- ``in_progress_laden_TD3C_voyages(as_of)`` = voyages with
  ``route=td3c``, ``trip_start ≤ as_of``, ``trip_end`` null or
  ``> as_of``, whose vessel is ``is_vlcc_candidate=True`` in the
  registry. The C4 voyages manifest is laden PG→China by construction.
- ``cargo_tons(v)`` = ``vessel_registry.dwt`` if present, else the
  route nominal 270 000 dwt (``ROUTE_NOMINAL_DWT``). Registry rarely
  fills ``dwt`` today; the field exists so later IMO-registry
  enrichment drops in without a schema change.
- ``remaining_distance_nm(v, as_of)`` = sea-route distance cache lookup
  on ``(trip_start_anchorage_id, trip_end_anchorage_id)``. Cache miss
  → great-circle fallback, counted in
  ``components["great_circle_fallbacks"]``. Live-AIS-position
  interpolation is phase 05+; here we use the static anchorage pair.

**Forward supply (count):**

    forward_supply_count(as_of) =
        |{ v : v ∈ ballast_VLCCs(as_of)
             AND estimated_arrival_pg(v, as_of)
                 ≤ as_of + supply_horizon_days }|

with ``supply_horizon_days = DEFAULT_SUPPLY_HORIZON_DAYS`` = 15 (half
a typical TD3C transit). Pre-phase-05 we proxy ballast VLCCs via
``route=td3c_ballast`` voyages in the C4 manifest (supplied as
``ballast_voyages_df``). ``estimated_arrival_pg`` uses a 13-knot
ballast nominal when AIS SOG history is not available (logged in
``components["avg_sog_fallback_used"]``).

**Dark-fleet supply adjustment:**

    dark_fleet_supply_adjustment(as_of) =
        |{ d ∈ dark_fleet_df
            : d.has_matching_voyage = False
              AND (as_of - 7d) ≤ d.detection_timestamp ≤ as_of }|

Phase 03 already filters SAR detections to major PG loading terminals;
this function applies only the time-window filter.

**Effective supply & ratio:**

    effective_supply = max(forward_supply_count - dark_fleet_supply_adjustment, 1)
    tightness = forward_demand_ton_miles / effective_supply

Floor of 1 avoids inf/NaN propagation and triggers
``components["supply_floor_clamped"] = 1`` so downstream IC / backtest
code can filter anomalous days.

**Z-score (strictly lookahead-free):**

    window   = { ratio(d) : d ∈ prior lookback_days, d < as_of }
    mean     = mean(window)
    std      = stdev(window, sample, ddof=1)
    z_score  = (ratio - mean) / std   if |window| >= MIN_Z_SCORE_SAMPLE
             = None                   otherwise

``as_of`` is strictly excluded from its own baseline. Default
``lookback_days = 90``. A window smaller than 30 returns None and the
current snapshot's z is null; std==0 also yields None with a WARN.

Reproducibility
---------------

The function is deterministic for fixed inputs (no random sampling).
Every fallback and clamp is recorded in ``components`` so a snapshot
can be replayed and any anomaly traced to its inputs. See the ADR
for the full consequences section.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import polars as pl

from taquantgeo_ais.gfw.distance import great_circle_nm

if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger(__name__)

ROUTE_NOMINAL_DWT: Final[int] = 270_000
"""Industry-standard VLCC DWT used as the fallback when the vessel
registry does not fill ``dwt``. A real VLCC fleet spans ~240k-320k
dwt; 270 k is the commonly quoted round number and the Worldscale
reference size for TD3 flat rates."""

BALLAST_NOMINAL_SOG_KNOTS: Final[float] = 13.0
"""Ballast VLCC speed-over-ground nominal used when live-AIS SOG
history is not available. Phase 05 will replace this with a 7-day
rolling mean per vessel; the nominal is here so pre-phase-05 backtests
do not crash on missing SOG."""

DEFAULT_SUPPLY_HORIZON_DAYS: Final[int] = 15
"""Half a typical TD3C transit (36 days round-trip → 18 each way). A
ballast vessel arriving in PG within this window is credibly
committable to a TD3C lift. Tighter (<10 d) under-counts; looser
(>20 d) over-counts (vessels that deep ballast can divert to another
region)."""

DARK_FLEET_WINDOW_DAYS: Final[int] = 7
"""Trailing window for the dark-fleet supply adjustment. Long enough to
catch the typical loading + voyage-visibility-latency pipeline; short
enough that a one-off SAR hit does not inflate the adjustment for a
fortnight."""

DEFAULT_LOOKBACK_DAYS: Final[int] = 90
"""Default trailing window for the tightness z-score baseline."""

MIN_Z_SCORE_SAMPLE: Final[int] = 30
"""Minimum number of prior snapshots required before emitting a z-score.
Below this we return None and ``z_score_90d`` is null in the persisted
row; prevents a 3-day warmup from producing a z-score."""


@dataclass(frozen=True)
class TightnessSnapshot:
    """Frozen snapshot of the tightness signal on ``as_of`` for ``route``.

    ``components`` is an audit-trail dict recording every fallback and
    clamp that fired during the computation; ``tightness.py``'s module
    docstring enumerates the keys written in the happy path and every
    fallback branch.
    """

    as_of: date
    route: str
    forward_demand_ton_miles: int
    forward_supply_count: int
    dark_fleet_supply_adjustment: int
    ratio: float
    z_score_90d: float | None
    components: dict[str, int | float] = field(default_factory=dict)


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _as_of_to_ts_utc(as_of: date) -> datetime:
    """Convert a calendar date to an inclusive end-of-day UTC timestamp.

    Phase 03 uses the same convention for its ``--until`` flag; we mirror
    it so a snapshot for ``2026-03-15`` sees every voyage / detection
    that occurred on that calendar day in UTC."""
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, 999999, tzinfo=UTC)


def _normalise_datetime_col(df: pl.DataFrame, col: str) -> pl.DataFrame:
    """Ensure a datetime column is tz-aware UTC for consistent comparisons.

    Accepts tz-naive (localised as UTC) or any tz (converted to UTC).
    Columns with non-Datetime dtype are passed through unchanged.
    """
    dtype = df.schema.get(col)
    if not isinstance(dtype, pl.Datetime):
        return df
    if dtype.time_zone is None:
        return df.with_columns(pl.col(col).dt.replace_time_zone("UTC"))
    if dtype.time_zone != "UTC":
        return df.with_columns(pl.col(col).dt.convert_time_zone("UTC"))
    return df


def _vlcc_mmsi_set(registry_df: pl.DataFrame) -> set[int]:
    """Set of mmsi values for rows with ``is_vlcc_candidate = true``.

    Join key is ``ssvid`` on the voyages side, ``mmsi`` on the registry
    side — both are the same integer MMSI. Phase 01 outputs the
    registry with ``mmsi`` as ``Int64`` nullable.
    """
    if registry_df.is_empty() or "is_vlcc_candidate" not in registry_df.columns:
        return set()
    vlccs = registry_df.filter(
        pl.col("is_vlcc_candidate") & pl.col("mmsi").is_not_null()
    ).get_column("mmsi")
    return {int(v) for v in vlccs.to_list() if v is not None}


def _cargo_tons_lookup(registry_df: pl.DataFrame) -> Mapping[int, int]:
    """Return ``{mmsi: dwt}`` for rows where dwt is present. Empty
    mapping if the registry has no ``dwt`` column — callers then use the
    route nominal for every voyage."""
    if registry_df.is_empty() or "dwt" not in registry_df.columns:
        return {}
    sub = registry_df.filter(pl.col("mmsi").is_not_null() & pl.col("dwt").is_not_null())
    return {int(r["mmsi"]): int(r["dwt"]) for r in sub.iter_rows(named=True)}


def _build_distance_lookup(
    distance_cache_df: pl.DataFrame,
) -> dict[tuple[str, str], float]:
    """Build an (origin_s2id, dest_s2id) -> nautical_miles lookup from the
    phase-02 distance cache parquet. Defensively ignores a cache missing
    any of the required columns; callers will then hit the great-circle
    fallback for every pair."""
    required = {"origin_s2id", "dest_s2id", "nautical_miles"}
    if distance_cache_df.is_empty() or not required.issubset(distance_cache_df.columns):
        return {}
    return {
        (str(r["origin_s2id"]), str(r["dest_s2id"])): float(r["nautical_miles"])
        for r in distance_cache_df.iter_rows(named=True)
    }


def _distance_for_pair(
    origin_s2id: str | None,
    dest_s2id: str | None,
    distance_by_pair: Mapping[tuple[str, str], float],
    gc_fallback: Mapping[tuple[str, str], tuple[float, float, float, float]],
) -> tuple[float, bool]:
    """Look up (origin, dest) in the distance cache; fall back to
    great-circle when missing. Returns ``(nm, used_great_circle_fallback)``.
    """
    if origin_s2id is None or dest_s2id is None:
        return 0.0, True
    key = (origin_s2id, dest_s2id)
    cached = distance_by_pair.get(key)
    if cached is not None:
        return cached, False
    coords = gc_fallback.get(key)
    if coords is None:
        return 0.0, True
    olat, olon, dlat, dlon = coords
    return great_circle_nm(olat, olon, dlat, dlon), True


def _compute_forward_demand(
    voyages_df: pl.DataFrame,
    vessel_registry_df: pl.DataFrame,
    distance_cache_df: pl.DataFrame,
    *,
    as_of: date,
    route: str,
    components: dict[str, int | float],
) -> int:
    """Sum cargo_tons * remaining_distance_nm over in-progress laden
    TD3C voyages. Writes diagnostic counters into ``components``."""
    vlcc_mmsis = _vlcc_mmsi_set(vessel_registry_df)
    components["vlcc_vessels_considered"] = len(vlcc_mmsis)
    required = {"route", "trip_start", "trip_end"}
    if voyages_df.is_empty() or not required.issubset(voyages_df.columns):
        components["in_progress_laden_voyages"] = 0
        components["cargo_tons_fallback_used"] = 0
        components["great_circle_fallbacks"] = 0
        components["route_total_distance_nm"] = 0.0
        return 0
    as_of_ts = _as_of_to_ts_utc(as_of)
    voyages = _normalise_datetime_col(voyages_df, "trip_start")
    voyages = _normalise_datetime_col(voyages, "trip_end")
    in_progress = voyages.filter(
        (pl.col("route") == route)
        & (pl.col("trip_start") <= as_of_ts)
        & (pl.col("trip_end").is_null() | (pl.col("trip_end") > as_of_ts))
    )
    if "ssvid" in in_progress.columns:
        in_progress = in_progress.filter(pl.col("ssvid").is_in(list(vlcc_mmsis)))
    components["in_progress_laden_voyages"] = in_progress.height
    if in_progress.is_empty():
        components["cargo_tons_fallback_used"] = 0
        components["great_circle_fallbacks"] = 0
        components["route_total_distance_nm"] = 0.0
        return 0

    distance_by_pair = _build_distance_lookup(distance_cache_df)
    gc_coords: dict[tuple[str, str], tuple[float, float, float, float]] = {}
    for r in in_progress.iter_rows(named=True):
        key = (str(r["trip_start_anchorage_id"]), str(r["trip_end_anchorage_id"]))
        gc_coords[key] = (
            float(r["orig_lat"]),
            float(r["orig_lon"]),
            float(r["dest_lat"]),
            float(r["dest_lon"]),
        )

    cargo_by_mmsi = _cargo_tons_lookup(vessel_registry_df)

    total_ton_miles = 0.0
    cargo_fallbacks = 0
    gc_fallbacks = 0
    distances: list[float] = []
    # Aggregate the dwt-fallback MMSIs and emit one summary WARN at the end
    # rather than one line per voyage. Log hygiene: on a 100-VLCC fleet the
    # per-row pattern floods a single daily run.
    fallback_mmsis: list[int] = []
    for row in in_progress.iter_rows(named=True):
        mmsi = int(row["ssvid"]) if row.get("ssvid") is not None else None
        dwt = cargo_by_mmsi.get(mmsi) if mmsi is not None else None
        # Treat dwt <= 0 as a sentinel for "absent" — prevents a registry row
        # with `dwt = 0` from silently zeroing out a voyage's ton-miles.
        if dwt is None or dwt <= 0:
            cargo_fallbacks += 1
            if mmsi is not None:
                fallback_mmsis.append(mmsi)
            dwt = ROUTE_NOMINAL_DWT
        nm, gc = _distance_for_pair(
            row.get("trip_start_anchorage_id"),
            row.get("trip_end_anchorage_id"),
            distance_by_pair,
            gc_coords,
        )
        if gc:
            gc_fallbacks += 1
        distances.append(nm)
        total_ton_miles += float(dwt) * nm

    if fallback_mmsis:
        log.warning(
            "cargo_tons fallback to %d dwt for %d vessel(s): mmsis=%s",
            ROUTE_NOMINAL_DWT,
            len(fallback_mmsis),
            sorted(fallback_mmsis),
        )
    components["cargo_tons_fallback_used"] = cargo_fallbacks
    components["great_circle_fallbacks"] = gc_fallbacks
    components["route_total_distance_nm"] = (
        float(statistics.median(distances)) if distances else 0.0
    )
    return round(total_ton_miles)


def _compute_forward_supply(
    ballast_voyages_df: pl.DataFrame,
    vessel_registry_df: pl.DataFrame,
    distance_cache_df: pl.DataFrame,
    *,
    as_of: date,
    supply_horizon_days: int,
    components: dict[str, int | float],
) -> int:
    """Count ballast VLCCs whose estimated PG arrival is within
    ``supply_horizon_days`` of ``as_of``. Writes diagnostic counters into
    ``components``."""
    required = {"route", "trip_start", "trip_end"}
    if ballast_voyages_df.is_empty() or not required.issubset(ballast_voyages_df.columns):
        components["ballast_in_progress"] = 0
        components["avg_sog_fallback_used"] = 0
        return 0
    as_of_ts = _as_of_to_ts_utc(as_of)
    ballast = _normalise_datetime_col(ballast_voyages_df, "trip_start")
    ballast = _normalise_datetime_col(ballast, "trip_end")
    in_progress = ballast.filter(
        (pl.col("trip_start") <= as_of_ts)
        & (pl.col("trip_end").is_null() | (pl.col("trip_end") > as_of_ts))
    )
    vlcc_mmsis = _vlcc_mmsi_set(vessel_registry_df)
    # Apply the VLCC filter even when the registry is empty (yields an empty
    # set → is_in([]) excludes every row). Matching the laden branch: ADR 0007
    # defines supply strictly as `ballast_VLCCs`, so an empty registry MUST
    # yield zero supply, not an unfiltered count.
    if "ssvid" in in_progress.columns:
        in_progress = in_progress.filter(pl.col("ssvid").is_in(list(vlcc_mmsis)))
    components["ballast_in_progress"] = in_progress.height
    if in_progress.is_empty():
        components["avg_sog_fallback_used"] = 0
        return 0

    distance_by_pair = _build_distance_lookup(distance_cache_df)

    cutoff = as_of_ts + timedelta(days=supply_horizon_days)
    arriving = 0
    sog_fallbacks = 0
    for r in in_progress.iter_rows(named=True):
        trip_start = r.get("trip_start")
        if trip_start is None:
            continue
        trip_start_utc = _ensure_utc(trip_start)
        origin = r.get("trip_start_anchorage_id")
        dest = r.get("trip_end_anchorage_id")
        gc_coords_single: dict[tuple[str, str], tuple[float, float, float, float]] = {}
        if origin is not None and dest is not None:
            gc_coords_single[(str(origin), str(dest))] = (
                float(r["orig_lat"]),
                float(r["orig_lon"]),
                float(r["dest_lat"]),
                float(r["dest_lon"]),
            )
        nm, _gc = _distance_for_pair(origin, dest, distance_by_pair, gc_coords_single)
        if nm <= 0.0:
            continue
        sog = BALLAST_NOMINAL_SOG_KNOTS
        sog_fallbacks += 1
        travel_hours = nm / sog
        eta = trip_start_utc + timedelta(hours=travel_hours)
        if eta <= cutoff:
            arriving += 1

    components["avg_sog_fallback_used"] = sog_fallbacks
    return arriving


def _compute_dark_fleet_adjustment(
    dark_fleet_df: pl.DataFrame,
    *,
    as_of: date,
    window_days: int,
    components: dict[str, int | float],
) -> int:
    """Count dark-fleet candidates in ``[as_of - window_days, as_of]``
    that have no matching voyage. Writes the raw (pre-filter) and kept
    counts into ``components``."""
    required = {"detection_timestamp", "has_matching_voyage"}
    if dark_fleet_df.is_empty() or not required.issubset(dark_fleet_df.columns):
        components["dark_fleet_candidates_used"] = 0
        return 0
    as_of_ts = _as_of_to_ts_utc(as_of)
    window_start = as_of_ts - timedelta(days=window_days)
    df = _normalise_datetime_col(dark_fleet_df, "detection_timestamp")
    # Null `has_matching_voyage` policy: treat unknown as "matched" (do not
    # count as dark). Implemented via ``fill_null(True)`` before the negation,
    # so a null row becomes True → negated to False → filtered out. The policy
    # errs on the side of UNDER-counting dark candidates, matching ADR 0007's
    # preference for conservative supply adjustments. Flipping the policy is a
    # one-line edit here.
    kept = df.filter(
        (~pl.col("has_matching_voyage").fill_null(True))
        & (pl.col("detection_timestamp") >= window_start)
        & (pl.col("detection_timestamp") <= as_of_ts)
    )
    components["dark_fleet_candidates_used"] = kept.height
    return kept.height


def _compute_z_score(
    current_ratio: float,
    prior_snapshots_df: pl.DataFrame | None,
    *,
    as_of: date,
    lookback_days: int,
    components: dict[str, int | float],
) -> float | None:
    """Prior snapshots are rows with ``as_of < as_of`` in the last
    ``lookback_days`` days. Requires at least ``MIN_Z_SCORE_SAMPLE``
    samples. Returns None on insufficient samples or zero variance."""
    if prior_snapshots_df is None or prior_snapshots_df.is_empty():
        components["z_score_sample_size"] = 0
        return None
    if "as_of" not in prior_snapshots_df.columns or "ratio" not in prior_snapshots_df.columns:
        log.warning("prior_snapshots_df missing as_of/ratio columns; skipping z-score")
        components["z_score_sample_size"] = 0
        return None
    window_start = as_of - timedelta(days=lookback_days)
    window = prior_snapshots_df.filter(
        (pl.col("as_of") >= window_start) & (pl.col("as_of") < as_of)
    )
    ratios = [float(r) for r in window.get_column("ratio").to_list() if r is not None]
    # Record the ACTUAL sample size used (post null-filter) so the audit trail
    # is not off by the number of null rows in the window.
    components["z_score_sample_size"] = len(ratios)
    if len(ratios) < MIN_Z_SCORE_SAMPLE:
        return None
    mean = statistics.fmean(ratios)
    std = statistics.stdev(ratios)  # sample stdev, ddof=1
    if std == 0.0 or math.isnan(std):
        log.warning(
            "z_score: std=0 over %d-day window at as_of=%s; returning None", lookback_days, as_of
        )
        return None
    return (current_ratio - mean) / std


def compute_daily_tightness(
    as_of: date,
    *,
    voyages_df: pl.DataFrame,
    vessel_registry_df: pl.DataFrame,
    distance_cache_df: pl.DataFrame,
    dark_fleet_df: pl.DataFrame,
    ballast_voyages_df: pl.DataFrame | None = None,
    prior_snapshots_df: pl.DataFrame | None = None,
    route: str = "td3c",
    supply_horizon_days: int = DEFAULT_SUPPLY_HORIZON_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> TightnessSnapshot:
    """Compute the TD3C tightness snapshot for ``as_of``.

    Inputs are pure DataFrames (no IO inside this function); the CLI and
    scheduled job handle IO. The function is deterministic: the same
    frames always produce the same snapshot, and every fallback /
    clamp is recorded in ``TightnessSnapshot.components`` for audit.

    ``ballast_voyages_df`` is optional; when absent we pass an empty
    ballast frame and supply is zero (floored to 1, ratio blows up).
    That branch exists so very-early backtest runs (no live AIS, no
    ballast manifest) still produce a snapshot without crashing — they
    are diagnostic only and will show ``effective_supply_raw = 0`` in
    ``components``.

    ``prior_snapshots_df`` holds historical ``(as_of, ratio)`` rows.
    When None or short, ``z_score_90d`` is None (see
    ``MIN_Z_SCORE_SAMPLE``).
    """
    components: dict[str, int | float] = {}

    forward_demand = _compute_forward_demand(
        voyages_df,
        vessel_registry_df,
        distance_cache_df,
        as_of=as_of,
        route=route,
        components=components,
    )

    forward_supply = _compute_forward_supply(
        ballast_voyages_df if ballast_voyages_df is not None else pl.DataFrame(),
        vessel_registry_df,
        distance_cache_df,
        as_of=as_of,
        supply_horizon_days=supply_horizon_days,
        components=components,
    )

    dark_fleet_adjustment = _compute_dark_fleet_adjustment(
        dark_fleet_df,
        as_of=as_of,
        window_days=DARK_FLEET_WINDOW_DAYS,
        components=components,
    )

    effective_supply_raw = forward_supply - dark_fleet_adjustment
    components["effective_supply_raw"] = effective_supply_raw
    clamped = effective_supply_raw <= 0
    components["supply_floor_clamped"] = 1 if clamped else 0
    # Floor of 1 is non-negotiable: it prevents ratio = x/0 and means
    # effective_supply is always >= 1. No division-by-zero guard is needed
    # here; the ``supply_floor_clamped`` flag in components is the signal
    # downstream IC code uses to drop the day.
    effective_supply = max(effective_supply_raw, 1)

    ratio = forward_demand / effective_supply

    z_score = _compute_z_score(
        ratio,
        prior_snapshots_df,
        as_of=as_of,
        lookback_days=lookback_days,
        components=components,
    )

    return TightnessSnapshot(
        as_of=as_of,
        route=route,
        forward_demand_ton_miles=forward_demand,
        forward_supply_count=forward_supply,
        dark_fleet_supply_adjustment=dark_fleet_adjustment,
        ratio=ratio,
        z_score_90d=z_score,
        components=components,
    )
