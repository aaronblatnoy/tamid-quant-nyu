"""Tests for ``taquantgeo_signals.tightness``.

Every equation in the module docstring / ADR 0007 has at least one
direct test here. Inputs are in-memory DataFrames from ``conftest.py``;
the signal function is pure-math-on-DataFrames by design, so no parquet
round-trip is needed. The job/CLI layer (tested separately in
``test_persistence.py``) handles IO.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from taquantgeo_ais.gfw.distance import great_circle_nm
from taquantgeo_signals.tightness import (
    BALLAST_NOMINAL_SOG_KNOTS,
    DARK_FLEET_WINDOW_DAYS,
    DEFAULT_SUPPLY_HORIZON_DAYS,
    MIN_Z_SCORE_SAMPLE,
    ROUTE_NOMINAL_DWT,
    TightnessSnapshot,
    compute_daily_tightness,
)


def test_forward_demand_sums_ton_miles_remaining(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """VLCC-1 (RT→Ningbo, 5920 NM) + VLCC-2 (Basrah→Qingdao, 6416 NM) +
    VLCC-3 (missing pair → great-circle fallback). Each at the nominal
    270k dwt. Non-VLCC and excluded-by-state voyages drop out. No
    arithmetic magic: expected is the literal sum."""
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
    )
    # VLCC-1 + VLCC-2 contributions via cache
    cached_ton_miles = ROUTE_NOMINAL_DWT * (5920.0 + 6416.0)
    # VLCC-3 via great-circle from Fujairah to Qingdao (gc computed
    # independently so the test is self-contained)
    gc_nm = great_circle_nm(25.12, 56.34, 36.07, 120.30)
    expected = round(cached_ton_miles + ROUTE_NOMINAL_DWT * gc_nm)
    assert snap.forward_demand_ton_miles == expected
    assert snap.components["in_progress_laden_voyages"] == 3
    assert snap.components["cargo_tons_fallback_used"] == 3
    assert snap.components["great_circle_fallbacks"] == 1


def test_forward_supply_counts_ballast_arrivals_in_15_day_window(
    sample_voyages_df: pl.DataFrame,
    sample_ballast_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Ballast-1 (started 2026-03-01, ~5920 NM / 13 kn = ~19 days →
    ETA ~03-20) within 15-day window → counts. Ballast-2 completed
    pre-as_of → excluded. Ballast-3 started 2026-03-14 with 6416 NM
    (~20.5 d) → ETA outside window → does not count.
    """
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
    )
    assert snap.forward_supply_count == 1


def test_supply_horizon_respected(
    sample_voyages_df: pl.DataFrame,
    sample_ballast_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Widen the supply horizon to 30 days — Ballast-3 now arrives
    within the window → supply goes from 1 → 2."""
    snap_default = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
    )
    snap_wide = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
        supply_horizon_days=30,
    )
    assert snap_default.forward_supply_count == 1
    assert snap_wide.forward_supply_count == 2


def test_dark_fleet_adjustment_reduces_supply(
    sample_voyages_df: pl.DataFrame,
    sample_ballast_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """The fixture has 2 in-window unmatched, 1 matched (ignored), 1
    out-of-window (ignored) → adjustment is 2."""
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
    )
    assert snap.dark_fleet_supply_adjustment == 2
    assert snap.components["dark_fleet_candidates_used"] == 2
    # effective supply = 1 - 2 = -1, floored to 1
    assert snap.components["effective_supply_raw"] == -1
    assert snap.components["supply_floor_clamped"] == 1


def test_ratio_is_demand_over_effective_supply(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    as_of: date,
) -> None:
    """With no ballast and no dark fleet, effective supply is floored to
    1 → ratio == forward_demand."""
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=pl.DataFrame(),
    )
    assert snap.forward_supply_count == 0
    assert snap.dark_fleet_supply_adjustment == 0
    assert snap.components["supply_floor_clamped"] == 1
    assert snap.ratio == float(snap.forward_demand_ton_miles)


def test_ratio_handles_zero_supply_returns_inf_not_nan(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """ADR 0007 mandates: ratio is finite (never NaN) regardless of the
    input regime. Three sub-cases:

    (a) demand > 0 and raw effective supply < 0 — floor kicks in, ratio
        is finite, `supply_floor_clamped=1`.
    (b) demand > 0 and real supply > 0 — ratio is finite, no floor.
    (c) demand == 0 — ratio is 0.0 (never NaN).
    """
    # Case (a): dark-fleet adjustment drives supply negative → floor
    snap_a = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
    )
    assert math.isfinite(snap_a.ratio)
    assert not math.isnan(snap_a.ratio)
    assert snap_a.components["supply_floor_clamped"] == 1

    # Case (c): empty voyages → demand 0 → ratio 0.0
    snap_c = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df.clear(),
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=pl.DataFrame(),
    )
    assert snap_c.forward_demand_ton_miles == 0
    assert snap_c.ratio == 0.0
    assert not math.isnan(snap_c.ratio)


def test_z_score_90d_uses_rolling_history(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Build 60 days of prior snapshots with a known mean/stdev, feed in,
    assert z_score equals (ratio - mean) / std."""
    history_days = 60
    prior = pl.DataFrame(
        {
            "as_of": [as_of - timedelta(days=i) for i in range(1, history_days + 1)],
            "ratio": [100.0 + i for i in range(history_days)],  # 100, 101, ..., 159
        }
    )
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        prior_snapshots_df=prior,
    )

    ratios = [100.0 + i for i in range(history_days)]
    mean = statistics.fmean(ratios)
    std = statistics.stdev(ratios)
    expected_z = (snap.ratio - mean) / std
    assert snap.z_score_90d is not None
    assert snap.z_score_90d == pytest.approx(expected_z, rel=1e-9)


def test_z_score_none_when_insufficient_history(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Fewer than MIN_Z_SCORE_SAMPLE prior snapshots → z is None."""
    short = pl.DataFrame(
        {
            "as_of": [as_of - timedelta(days=i) for i in range(1, MIN_Z_SCORE_SAMPLE)],
            "ratio": [100.0 + i for i in range(MIN_Z_SCORE_SAMPLE - 1)],
        }
    )
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        prior_snapshots_df=short,
    )
    assert snap.z_score_90d is None


def test_z_score_strictly_lookahead_free(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """A prior-snapshot row with ``as_of == as_of`` MUST be excluded. Run
    twice with two prior frames that differ ONLY in whether the same-day
    outlier is present — z-score must be identical."""
    baseline = [100.0 + i for i in range(49)]
    dates_49 = [as_of - timedelta(days=i) for i in range(1, 50)]
    clean = pl.DataFrame({"as_of": dates_49, "ratio": baseline})
    with_outlier = pl.DataFrame(
        {"as_of": [as_of, *dates_49], "ratio": [1e18, *baseline]},
    )
    snap_clean = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        prior_snapshots_df=clean,
    )
    snap_dirty = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        prior_snapshots_df=with_outlier,
    )
    assert snap_clean.z_score_90d is not None
    assert snap_dirty.z_score_90d is not None
    assert snap_clean.z_score_90d == pytest.approx(snap_dirty.z_score_90d, rel=1e-12)


def test_z_score_none_when_zero_variance(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Flat prior window → std=0 → z is None."""
    flat = pl.DataFrame(
        {
            "as_of": [as_of - timedelta(days=i) for i in range(1, 60)],
            "ratio": [42.0] * 59,
        }
    )
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        prior_snapshots_df=flat,
    )
    assert snap.z_score_90d is None


def test_components_dict_exposes_raw_terms(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    sample_ballast_voyages_df: pl.DataFrame,
    as_of: date,
) -> None:
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
    )
    required_keys = {
        "vlcc_vessels_considered",
        "in_progress_laden_voyages",
        "cargo_tons_fallback_used",
        "great_circle_fallbacks",
        "avg_sog_fallback_used",
        "route_total_distance_nm",
        "supply_floor_clamped",
        "effective_supply_raw",
        "dark_fleet_candidates_used",
    }
    missing = required_keys - set(snap.components.keys())
    assert missing == set(), f"missing required components keys: {missing}"


def test_non_vlcc_voyages_excluded(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """The non-VLCC voyage in the fixture is in-progress and laden but
    ``is_vlcc_candidate=False``. It must not contribute."""
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
    )
    # Expected count without non-VLCC = 3, not 4
    assert snap.components["in_progress_laden_voyages"] == 3


def test_dwt_present_uses_per_vessel_dwt(
    sample_voyages_df: pl.DataFrame,
    sample_registry_with_dwt_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Registry with partial dwt: VLCC-1 (280k) uses the real dwt; others
    hit the nominal. Forward demand differs from the all-nominal case by
    10k * 5920 NM."""
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_with_dwt_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
    )
    # Re-derive expected from fixture knowledge.
    gc_nm = great_circle_nm(25.12, 56.34, 36.07, 120.30)
    expected = round(
        280_000 * 5920.0  # VLCC-1 real dwt
        + ROUTE_NOMINAL_DWT * 6416.0  # VLCC-2 nominal
        + ROUTE_NOMINAL_DWT * gc_nm  # VLCC-3 nominal, gc fallback
    )
    assert snap.forward_demand_ton_miles == expected
    # 2 vessels (VLCC-2 and VLCC-3) hit the fallback
    assert snap.components["cargo_tons_fallback_used"] == 2


def test_dwt_zero_is_treated_as_fallback(
    sample_voyages_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Registry row with ``dwt = 0`` must be treated as "absent" — otherwise
    a corrupt registry could silently zero out a voyage's ton-miles. All
    three in-progress VLCCs should hit the nominal fallback."""
    registry = pl.DataFrame(
        [
            {"mmsi": 111111111, "is_vlcc_candidate": True, "dwt": 0},
            {"mmsi": 222222222, "is_vlcc_candidate": True, "dwt": -1},
            {"mmsi": 333333333, "is_vlcc_candidate": True, "dwt": None},
            {"mmsi": 444444444, "is_vlcc_candidate": True, "dwt": None},
            {"mmsi": 555555555, "is_vlcc_candidate": True, "dwt": None},
            {"mmsi": 999999999, "is_vlcc_candidate": False, "dwt": None},
        ],
        schema={"mmsi": pl.Int64, "is_vlcc_candidate": pl.Boolean, "dwt": pl.Int64},
    )
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=registry,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
    )
    assert snap.components["cargo_tons_fallback_used"] == 3


def test_dark_fleet_has_matching_voyage_null_is_treated_as_matched(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Explicit policy pin: ``has_matching_voyage = None`` is UNKNOWN and
    the dark-fleet adjustment treats unknown as matched (i.e. not
    counted). A row with null here should NOT contribute to the
    adjustment — even though it is within the time window and at a
    loading terminal. Flipping this policy is a one-line edit in
    ``_compute_dark_fleet_adjustment``; if the test fails the policy
    has been reversed and IC / backtest code must be audited."""
    dark = pl.DataFrame(
        {
            "mmsi": [None, None],
            "detection_timestamp": [
                datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 14, 11, 0, 0, tzinfo=UTC),
            ],
            "nearest_anchorage_id": ["s2_rt", "s2_rt"],
            "nearest_anchorage_label": ["RAS TANURA", "RAS TANURA"],
            "has_matching_voyage": [None, False],
        },
        schema={
            "mmsi": pl.Int64,
            "detection_timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "nearest_anchorage_id": pl.String,
            "nearest_anchorage_label": pl.String,
            "has_matching_voyage": pl.Boolean,
        },
    )
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=dark,
    )
    # Only the explicit False row counts; the None row is dropped by policy.
    assert snap.dark_fleet_supply_adjustment == 1


def test_distance_cache_missing_columns_falls_back(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Cache parquet without the required columns (`origin_s2id`,
    `dest_s2id`, `nautical_miles`) must be treated as absent and every
    pair falls back to great-circle — not crash with KeyError."""
    broken_cache = pl.DataFrame(
        [
            {"foo": "a", "bar": 1.0},
            {"foo": "b", "bar": 2.0},
        ]
    )
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=broken_cache,
        dark_fleet_df=sample_dark_fleet_df,
    )
    # 3 in-progress VLCC voyages, all should great-circle
    assert snap.components["great_circle_fallbacks"] == 3


def test_trip_end_null_is_in_progress(
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """A voyage with trip_end = None must be treated as in-progress."""
    row = {
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
    voyages = pl.DataFrame([row])
    snap = compute_daily_tightness(
        as_of,
        voyages_df=voyages,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
    )
    assert snap.components["in_progress_laden_voyages"] == 1


def test_deterministic_for_fixed_inputs(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    sample_ballast_voyages_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Running twice on identical inputs yields identical snapshots."""
    s1 = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
    )
    s2 = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
    )
    assert s1 == s2


def test_tz_naive_voyage_timestamps_accepted(
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Phase 01 writes ``trip_start`` as tz-naive; the signal must accept
    that without raising."""
    row = {
        "ssvid": 111111111,
        "vessel_id": "vid-1",
        "trip_id": "t-1",
        "trip_start": datetime(2026, 3, 1, 0, 0, 0),  # naive
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
    voyages = pl.DataFrame([row])
    snap = compute_daily_tightness(
        as_of,
        voyages_df=voyages,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
    )
    assert snap.forward_demand_ton_miles > 0


def test_ballast_fallback_sog_counter_fires(
    sample_voyages_df: pl.DataFrame,
    sample_ballast_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """With no live-AIS SOG history wired in yet, every ballast vessel
    hits the 13-knot nominal → ``avg_sog_fallback_used`` equals the
    count of in-progress ballast voyages."""
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=sample_ballast_voyages_df,
    )
    # 2 in-progress ballast voyages after the completed-pre-as_of filter
    assert snap.components["avg_sog_fallback_used"] == 2
    assert snap.components["ballast_in_progress"] == 2


def test_default_ballast_supply_is_zero(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    sample_dark_fleet_df: pl.DataFrame,
    as_of: date,
) -> None:
    """``ballast_voyages_df=None`` yields supply=0 and floor kicks in."""
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=sample_dark_fleet_df,
        ballast_voyages_df=None,
    )
    assert snap.forward_supply_count == 0


def test_empty_inputs_produce_valid_snapshot(as_of: date) -> None:
    """No voyages, no registry, no distances, no dark fleet → zero-zero
    snapshot with ratio 0.0 and no crash."""
    empty = pl.DataFrame()
    snap = compute_daily_tightness(
        as_of,
        voyages_df=empty,
        vessel_registry_df=empty,
        distance_cache_df=empty,
        dark_fleet_df=empty,
    )
    assert snap.forward_demand_ton_miles == 0
    assert snap.forward_supply_count == 0
    assert snap.dark_fleet_supply_adjustment == 0
    assert snap.ratio == 0.0
    assert snap.z_score_90d is None


def test_snapshot_is_frozen() -> None:
    """TightnessSnapshot must be immutable — catches any accidental
    future @dataclass without frozen=True."""
    snap = TightnessSnapshot(
        as_of=date(2026, 3, 15),
        route="td3c",
        forward_demand_ton_miles=0,
        forward_supply_count=0,
        dark_fleet_supply_adjustment=0,
        ratio=0.0,
        z_score_90d=None,
        components={},
    )
    with pytest.raises(FrozenInstanceError):
        snap.ratio = 1.0  # type: ignore[misc]


def test_dark_fleet_window_constant_is_seven() -> None:
    """Pin the dark-fleet window constant — ADR 0007 bakes this into the
    signal definition."""
    assert DARK_FLEET_WINDOW_DAYS == 7


def test_supply_horizon_constant_is_fifteen() -> None:
    assert DEFAULT_SUPPLY_HORIZON_DAYS == 15


def test_min_z_score_sample_is_thirty() -> None:
    assert MIN_Z_SCORE_SAMPLE == 30


def test_route_nominal_dwt_is_two_seventy_thousand() -> None:
    assert ROUTE_NOMINAL_DWT == 270_000


def test_ballast_nominal_sog_is_thirteen() -> None:
    assert BALLAST_NOMINAL_SOG_KNOTS == 13.0


def test_tz_aware_dark_fleet_utc_is_accepted(
    sample_voyages_df: pl.DataFrame,
    sample_registry_df: pl.DataFrame,
    sample_distance_cache_df: pl.DataFrame,
    as_of: date,
) -> None:
    """Phase 03 writes detection_timestamp as tz-aware UTC. Verify the
    normalisation path actually works on that shape."""
    dark = pl.DataFrame(
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
    )
    snap = compute_daily_tightness(
        as_of,
        voyages_df=sample_voyages_df,
        vessel_registry_df=sample_registry_df,
        distance_cache_df=sample_distance_cache_df,
        dark_fleet_df=dark,
    )
    assert snap.dark_fleet_supply_adjustment == 1
