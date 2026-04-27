"""Shared test fixtures for ``packages/signals``.

We construct the input frames in-memory rather than committing parquet
files. The signal math is pure-function on DataFrames (IO is in the job /
CLI layer), so feeding it ``pl.DataFrame(...)`` directly is both faster
and more auditable than round-tripping through parquet.

Layout:

- ``sample_voyages_df`` — 4 in-progress laden voyages + 1 completed
  (should be excluded) + 1 future (should be excluded)
- ``sample_ballast_voyages_df`` — 3 in-progress td3c_ballast voyages,
  one already in PG, two still en-route, with differing durations so
  the 15-day horizon filter is exercised
- ``sample_registry_df`` — 6 vessels (5 VLCC-candidates, 1 not) matching
  the voyages by mmsi. No ``dwt`` column → every cargo_tons call hits
  the route-nominal fallback (pins the fallback counter).
- ``sample_registry_with_dwt_df`` — same as above but with a ``dwt``
  column (280 k for VLCC rows, null elsewhere) so the "dwt present"
  branch is testable too.
- ``sample_distance_cache_df`` — 3 pairs covering the Ras Tanura →
  Ningbo and Basrah → Qingdao anchorage ids used in the voyages frame.
  One deliberately-missing pair forces the great-circle fallback path.
- ``sample_dark_fleet_df`` — 4 rows: 2 in-window unmatched (counted),
  1 in-window matched (not counted), 1 out-of-window unmatched (not
  counted).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

REF_AS_OF = date(2026, 3, 15)

# MMSI choices — arbitrary but consistent across fixtures.
MMSI_VLCC_1 = 111111111
MMSI_VLCC_2 = 222222222
MMSI_VLCC_3 = 333333333
MMSI_VLCC_4 = 444444444  # laden but completed pre-as_of
MMSI_VLCC_5 = 555555555  # voyage starts after as_of (future)
MMSI_NON_VLCC = 999999999  # not a VLCC

# Anchorage ids (short, arbitrary — match across voyages and distance cache).
RAS_TANURA = "s2_rt"
BASRAH = "s2_br"
NINGBO = "s2_ng"
QINGDAO = "s2_qd"


@pytest.fixture(scope="session")
def as_of() -> date:
    return REF_AS_OF


@pytest.fixture
def sample_voyages_df() -> pl.DataFrame:
    """TD3C laden voyages. Schema matches phase 01 output
    (``packages/ais/src/taquantgeo_ais/gfw/extract.py`` writes this
    shape)."""
    rows: list[dict[str, object]] = [
        # In-progress, Ras Tanura → Ningbo, VLCC-1
        {
            "ssvid": MMSI_VLCC_1,
            "vessel_id": "vid-1",
            "trip_id": "trip-1",
            "trip_start": datetime(2026, 3, 1, 0, 0, 0),
            "trip_end": datetime(2026, 3, 22, 0, 0, 0),  # > as_of
            "trip_start_anchorage_id": RAS_TANURA,
            "trip_end_anchorage_id": NINGBO,
            "orig_iso3": "SAU",
            "orig_label": "RAS TANURA",
            "orig_lat": 26.70,
            "orig_lon": 50.18,
            "dest_iso3": "CHN",
            "dest_label": "NINGBO",
            "dest_lat": 29.87,
            "dest_lon": 121.55,
            "route": "td3c",
        },
        # In-progress, Basrah → Qingdao, VLCC-2
        {
            "ssvid": MMSI_VLCC_2,
            "vessel_id": "vid-2",
            "trip_id": "trip-2",
            "trip_start": datetime(2026, 3, 5, 0, 0, 0),
            "trip_end": None,  # null means still underway
            "trip_start_anchorage_id": BASRAH,
            "trip_end_anchorage_id": QINGDAO,
            "orig_iso3": "IRQ",
            "orig_label": "BASRAH",
            "orig_lat": 29.72,
            "orig_lon": 48.83,
            "dest_iso3": "CHN",
            "dest_label": "QINGDAO",
            "dest_lat": 36.07,
            "dest_lon": 120.30,
            "route": "td3c",
        },
        # In-progress, VLCC-3 (missing-cache pair → GC fallback)
        {
            "ssvid": MMSI_VLCC_3,
            "vessel_id": "vid-3",
            "trip_id": "trip-3",
            "trip_start": datetime(2026, 3, 10, 0, 0, 0),
            "trip_end": None,
            "trip_start_anchorage_id": "s2_missing",
            "trip_end_anchorage_id": "s2_missing_dest",
            "orig_iso3": "ARE",
            "orig_label": "FUJAIRAH",
            "orig_lat": 25.12,
            "orig_lon": 56.34,
            "dest_iso3": "CHN",
            "dest_label": "QINGDAO",
            "dest_lat": 36.07,
            "dest_lon": 120.30,
            "route": "td3c",
        },
        # Completed before as_of — excluded
        {
            "ssvid": MMSI_VLCC_4,
            "vessel_id": "vid-4",
            "trip_id": "trip-4",
            "trip_start": datetime(2026, 2, 1, 0, 0, 0),
            "trip_end": datetime(2026, 3, 1, 0, 0, 0),
            "trip_start_anchorage_id": RAS_TANURA,
            "trip_end_anchorage_id": NINGBO,
            "orig_iso3": "SAU",
            "orig_label": "RAS TANURA",
            "orig_lat": 26.70,
            "orig_lon": 50.18,
            "dest_iso3": "CHN",
            "dest_label": "NINGBO",
            "dest_lat": 29.87,
            "dest_lon": 121.55,
            "route": "td3c",
        },
        # Future start — excluded
        {
            "ssvid": MMSI_VLCC_5,
            "vessel_id": "vid-5",
            "trip_id": "trip-5",
            "trip_start": datetime(2026, 3, 20, 0, 0, 0),
            "trip_end": None,
            "trip_start_anchorage_id": RAS_TANURA,
            "trip_end_anchorage_id": NINGBO,
            "orig_iso3": "SAU",
            "orig_label": "RAS TANURA",
            "orig_lat": 26.70,
            "orig_lon": 50.18,
            "dest_iso3": "CHN",
            "dest_label": "NINGBO",
            "dest_lat": 29.87,
            "dest_lon": 121.55,
            "route": "td3c",
        },
        # In-progress but NOT a VLCC (registry marks is_vlcc_candidate=False)
        {
            "ssvid": MMSI_NON_VLCC,
            "vessel_id": "vid-x",
            "trip_id": "trip-x",
            "trip_start": datetime(2026, 3, 1, 0, 0, 0),
            "trip_end": None,
            "trip_start_anchorage_id": RAS_TANURA,
            "trip_end_anchorage_id": NINGBO,
            "orig_iso3": "SAU",
            "orig_label": "RAS TANURA",
            "orig_lat": 26.70,
            "orig_lon": 50.18,
            "dest_iso3": "CHN",
            "dest_label": "NINGBO",
            "dest_lat": 29.87,
            "dest_lon": 121.55,
            "route": "td3c",
        },
    ]
    return pl.DataFrame(rows)


@pytest.fixture
def sample_ballast_voyages_df() -> pl.DataFrame:
    """td3c_ballast voyages for the supply count. Contains:

    - one ballast trip started 2026-03-01 from Ningbo → Ras Tanura
      (distance ~5920 NM, at 13 kn = ~19 days → ETA ~2026-03-20,
      within as_of + 15d = 2026-03-30 → counts)
    - one ballast trip started 2026-01-01 → 2026-03-01 completed pre-as_of
      (excluded by in-progress filter)
    - one ballast trip started 2026-03-14 (just before as_of) from
      Qingdao → Basrah. ~6400 NM / 13 kn = ~20.5 days → ETA ~2026-04-03
      → outside 2026-03-30 window → does not count.
    """
    rows: list[dict[str, object]] = [
        # 2026-03-01 start, arrives within window
        {
            "ssvid": MMSI_VLCC_1,
            "vessel_id": "vid-1",
            "trip_id": "b-trip-1",
            "trip_start": datetime(2026, 3, 1, 0, 0, 0),
            "trip_end": None,
            "trip_start_anchorage_id": NINGBO,
            "trip_end_anchorage_id": RAS_TANURA,
            "orig_iso3": "CHN",
            "orig_label": "NINGBO",
            "orig_lat": 29.87,
            "orig_lon": 121.55,
            "dest_iso3": "SAU",
            "dest_label": "RAS TANURA",
            "dest_lat": 26.70,
            "dest_lon": 50.18,
            "route": "td3c_ballast",
        },
        # Completed pre-as_of
        {
            "ssvid": MMSI_VLCC_2,
            "vessel_id": "vid-2",
            "trip_id": "b-trip-2",
            "trip_start": datetime(2026, 1, 1, 0, 0, 0),
            "trip_end": datetime(2026, 3, 1, 0, 0, 0),
            "trip_start_anchorage_id": NINGBO,
            "trip_end_anchorage_id": RAS_TANURA,
            "orig_iso3": "CHN",
            "orig_label": "NINGBO",
            "orig_lat": 29.87,
            "orig_lon": 121.55,
            "dest_iso3": "SAU",
            "dest_label": "RAS TANURA",
            "dest_lat": 26.70,
            "dest_lon": 50.18,
            "route": "td3c_ballast",
        },
        # 2026-03-14 start → ETA outside 15-day window
        {
            "ssvid": MMSI_VLCC_3,
            "vessel_id": "vid-3",
            "trip_id": "b-trip-3",
            "trip_start": datetime(2026, 3, 14, 0, 0, 0),
            "trip_end": None,
            "trip_start_anchorage_id": QINGDAO,
            "trip_end_anchorage_id": BASRAH,
            "orig_iso3": "CHN",
            "orig_label": "QINGDAO",
            "orig_lat": 36.07,
            "orig_lon": 120.30,
            "dest_iso3": "IRQ",
            "dest_label": "BASRAH",
            "dest_lat": 29.72,
            "dest_lon": 48.83,
            "route": "td3c_ballast",
        },
    ]
    return pl.DataFrame(rows)


@pytest.fixture
def sample_registry_df() -> pl.DataFrame:
    """Vessel registry. VLCC-5 rows (mmsi 1-5) are candidates; non-VLCC
    mmsi 9 is not. No ``dwt`` column → route nominal fallback fires for
    every voyage."""
    rows = [
        {"mmsi": MMSI_VLCC_1, "is_vlcc_candidate": True},
        {"mmsi": MMSI_VLCC_2, "is_vlcc_candidate": True},
        {"mmsi": MMSI_VLCC_3, "is_vlcc_candidate": True},
        {"mmsi": MMSI_VLCC_4, "is_vlcc_candidate": True},
        {"mmsi": MMSI_VLCC_5, "is_vlcc_candidate": True},
        {"mmsi": MMSI_NON_VLCC, "is_vlcc_candidate": False},
    ]
    return pl.DataFrame(rows)


@pytest.fixture
def sample_registry_with_dwt_df() -> pl.DataFrame:
    """Same as ``sample_registry_df`` but with a ``dwt`` column (280 k
    for VLCC-1, null elsewhere) — exercises the mixed registry branch
    where some vessels hit the fallback and some don't."""
    rows = [
        {"mmsi": MMSI_VLCC_1, "is_vlcc_candidate": True, "dwt": 280_000},
        {"mmsi": MMSI_VLCC_2, "is_vlcc_candidate": True, "dwt": None},
        {"mmsi": MMSI_VLCC_3, "is_vlcc_candidate": True, "dwt": None},
        {"mmsi": MMSI_VLCC_4, "is_vlcc_candidate": True, "dwt": None},
        {"mmsi": MMSI_VLCC_5, "is_vlcc_candidate": True, "dwt": None},
        {"mmsi": MMSI_NON_VLCC, "is_vlcc_candidate": False, "dwt": None},
    ]
    return pl.DataFrame(
        rows,
        schema={
            "mmsi": pl.Int64,
            "is_vlcc_candidate": pl.Boolean,
            "dwt": pl.Int64,
        },
    )


@pytest.fixture
def sample_distance_cache_df() -> pl.DataFrame:
    """Distance cache: Ras Tanura → Ningbo and Basrah → Qingdao only.
    Ballast pairs (Ningbo → Ras Tanura, Qingdao → Basrah) included for
    supply math. No entry for the deliberately-missing vid-3 pair —
    great-circle fallback exercises that branch."""
    rows = [
        {"origin_s2id": RAS_TANURA, "dest_s2id": NINGBO, "nautical_miles": 5920.0},
        {"origin_s2id": BASRAH, "dest_s2id": QINGDAO, "nautical_miles": 6416.0},
        {"origin_s2id": NINGBO, "dest_s2id": RAS_TANURA, "nautical_miles": 5920.0},
        {"origin_s2id": QINGDAO, "dest_s2id": BASRAH, "nautical_miles": 6416.0},
    ]
    return pl.DataFrame(rows)


@pytest.fixture
def sample_dark_fleet_df() -> pl.DataFrame:
    """4 SAR candidate rows. Column order matches phase 03 output."""
    rows = [
        # In window, unmatched → counts
        {
            "mmsi": None,
            "detection_timestamp": datetime(2026, 3, 13, 10, 0, 0, tzinfo=UTC),
            "nearest_anchorage_id": RAS_TANURA,
            "nearest_anchorage_label": "RAS TANURA",
            "has_matching_voyage": False,
        },
        # In window, unmatched → counts
        {
            "mmsi": None,
            "detection_timestamp": datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC),
            "nearest_anchorage_id": BASRAH,
            "nearest_anchorage_label": "BASRAH",
            "has_matching_voyage": False,
        },
        # In window BUT matched → does not count
        {
            "mmsi": 123456789,
            "detection_timestamp": datetime(2026, 3, 12, 10, 0, 0, tzinfo=UTC),
            "nearest_anchorage_id": RAS_TANURA,
            "nearest_anchorage_label": "RAS TANURA",
            "has_matching_voyage": True,
        },
        # Out-of-window (10 days before) → does not count
        {
            "mmsi": None,
            "detection_timestamp": datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC),
            "nearest_anchorage_id": RAS_TANURA,
            "nearest_anchorage_label": "RAS TANURA",
            "has_matching_voyage": False,
        },
    ]
    return pl.DataFrame(
        rows,
        schema={
            "mmsi": pl.Int64,
            "detection_timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "nearest_anchorage_id": pl.String,
            "nearest_anchorage_label": pl.String,
            "has_matching_voyage": pl.Boolean,
        },
    )
