"""Regenerate the snapshot-test fixtures deterministically.

Run from the repo root::

    uv run python packages/signals/tests/fixtures/snapshot/regenerate.py

Produces four parquet files next to this script:

- ``voyages.parquet``        — 12 rows (10 laden ``td3c`` + 2 ``td3c_ballast``)
- ``vessel_registry.parquet`` — 12 rows (11 VLCC candidates + 1 non-VLCC)
- ``distance_cache.parquet``  — 4 rows (the four PG↔China anchorage pairs)
- ``dark_fleet.parquet``      — 3 rows (SAR detections at PG terminals)

Every row below is hand-picked to exercise a specific snapshot-test
scenario on a specific ``as_of``. See ``README.md`` in this directory for
the derivation of each fixture row and the expected snapshot values.

This script is the source of truth for the fixtures. Do NOT hand-edit the
parquet outputs — regenerate via this script so the README, the
generator, and the committed files stay in lockstep.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

FIXTURE_DIR = Path(__file__).parent

# Anchorage ids — arbitrary short strings shared between voyages and the
# distance cache. Kept s2-style so the format matches what phase 02 writes.
RT = "s2_rt"  # Ras Tanura (SAU)
BR = "s2_br"  # Al-Basrah Oil Terminal (IRQ)
NB = "s2_ng"  # Ningbo (CHN)
QD = "s2_qd"  # Qingdao (CHN)

# Lat/lon pairs used as the great-circle fallback coordinates when the
# cache miss branch fires. All ``td3c`` anchorage pairs below hit the cache
# so the gc fallback does not fire in this fixture; keeping the columns
# populated keeps the schema aligned with phase-01 output.
RT_LAT, RT_LON = 26.70, 50.18
BR_LAT, BR_LON = 29.72, 48.83
NB_LAT, NB_LON = 29.87, 121.55
QD_LAT, QD_LON = 36.07, 120.30

# MMSIs (arbitrary but stable).
V101, V102, V103, V104, V105, V106, V107 = 101, 102, 103, 104, 105, 106, 107
V108, V109 = 108, 109
NON_VLCC = 999
B201, B202 = 201, 202

# Distances (NM) for the four cached pairs. Kept as round numbers so a
# reader can check ton-miles by hand.
NM_RT_NB = 5920.0
NM_BR_QD = 6416.0


def _laden(
    *,
    ssvid: int,
    trip_id: str,
    start: datetime,
    end: datetime | None,
    origin: str,
    dest: str,
) -> dict[str, object]:
    """Build one laden voyage row. ``origin`` and ``dest`` are anchorage ids."""
    coords = {
        RT: (RT_LAT, RT_LON, "SAU", "RAS TANURA"),
        BR: (BR_LAT, BR_LON, "IRQ", "AL-BASRAH OIL TERMINAL"),
        NB: (NB_LAT, NB_LON, "CHN", "NINGBO"),
        QD: (QD_LAT, QD_LON, "CHN", "QINGDAO"),
    }
    o_lat, o_lon, o_iso, o_label = coords[origin]
    d_lat, d_lon, d_iso, d_label = coords[dest]
    return {
        "ssvid": ssvid,
        "vessel_id": f"vid-{ssvid}",
        "trip_id": trip_id,
        "trip_start": start,
        "trip_end": end,
        "trip_start_anchorage_id": origin,
        "trip_end_anchorage_id": dest,
        "orig_iso3": o_iso,
        "orig_label": o_label,
        "orig_lat": o_lat,
        "orig_lon": o_lon,
        "dest_iso3": d_iso,
        "dest_label": d_label,
        "dest_lat": d_lat,
        "dest_lon": d_lon,
        "route": "td3c",
    }


def _ballast(
    *,
    ssvid: int,
    trip_id: str,
    start: datetime,
    end: datetime | None,
    origin: str,
    dest: str,
) -> dict[str, object]:
    row = _laden(ssvid=ssvid, trip_id=trip_id, start=start, end=end, origin=origin, dest=dest)
    row["route"] = "td3c_ballast"
    return row


def build_voyages() -> pl.DataFrame:
    """10 laden ``td3c`` voyages + 2 ``td3c_ballast`` voyages.

    Laden voyages ``v101…v107`` are genuinely in-progress on at least one
    of the test ``as_of`` dates (2020-03-01, 03-05, 03-15, 03-18, 03-20,
    03-22). ``v108`` completes before every test date and ``v109`` starts
    after; both stay in the frame as negative controls that the
    in-progress filter must exclude. The non-VLCC row (ssvid=999)
    exercises the registry filter on the laden side.

    The ballast voyages are short enough that both arrive within the
    15-day supply horizon of 2020-03-15 but not of 2020-03-05.
    """
    laden = [
        _laden(
            ssvid=V101,
            trip_id="l-101",
            start=datetime(2020, 2, 25, 0, 0, 0),
            end=datetime(2020, 3, 20, 0, 0, 0),
            origin=RT,
            dest=NB,
        ),
        _laden(
            ssvid=V102,
            trip_id="l-102",
            start=datetime(2020, 3, 2, 0, 0, 0),
            end=datetime(2020, 3, 28, 0, 0, 0),
            origin=BR,
            dest=QD,
        ),
        _laden(
            ssvid=V103,
            trip_id="l-103",
            start=datetime(2020, 3, 5, 0, 0, 0),
            end=None,
            origin=RT,
            dest=NB,
        ),
        _laden(
            ssvid=V104,
            trip_id="l-104",
            start=datetime(2020, 3, 8, 0, 0, 0),
            end=datetime(2020, 4, 2, 0, 0, 0),
            origin=BR,
            dest=QD,
        ),
        _laden(
            ssvid=V105,
            trip_id="l-105",
            start=datetime(2020, 3, 10, 0, 0, 0),
            end=None,
            origin=RT,
            dest=NB,
        ),
        _laden(
            ssvid=V106,
            trip_id="l-106",
            start=datetime(2020, 3, 12, 0, 0, 0),
            end=None,
            origin=BR,
            dest=QD,
        ),
        _laden(
            ssvid=V107,
            trip_id="l-107",
            start=datetime(2020, 3, 14, 0, 0, 0),
            end=datetime(2020, 4, 5, 0, 0, 0),
            origin=RT,
            dest=NB,
        ),
        # v108 — completed before any test date (negative control)
        _laden(
            ssvid=V108,
            trip_id="l-108",
            start=datetime(2020, 2, 1, 0, 0, 0),
            end=datetime(2020, 2, 25, 0, 0, 0),
            origin=RT,
            dest=NB,
        ),
        # v109 — starts after every test date (negative control)
        _laden(
            ssvid=V109,
            trip_id="l-109",
            start=datetime(2020, 4, 1, 0, 0, 0),
            end=None,
            origin=BR,
            dest=QD,
        ),
        # non-VLCC — in progress on 3/15 but registry excludes it
        _laden(
            ssvid=NON_VLCC,
            trip_id="l-999",
            start=datetime(2020, 3, 10, 0, 0, 0),
            end=None,
            origin=RT,
            dest=NB,
        ),
    ]
    ballast = [
        _ballast(
            ssvid=B201,
            trip_id="b-201",
            start=datetime(2020, 3, 5, 0, 0, 0),
            end=datetime(2020, 3, 24, 0, 0, 0),
            origin=NB,
            dest=RT,
        ),
        _ballast(
            ssvid=B202,
            trip_id="b-202",
            start=datetime(2020, 3, 8, 0, 0, 0),
            end=datetime(2020, 3, 28, 0, 0, 0),
            origin=QD,
            dest=BR,
        ),
    ]
    return pl.DataFrame(
        laden + ballast,
        schema={
            "ssvid": pl.Int64,
            "vessel_id": pl.String,
            "trip_id": pl.String,
            "trip_start": pl.Datetime(time_unit="us"),
            "trip_end": pl.Datetime(time_unit="us"),
            "trip_start_anchorage_id": pl.String,
            "trip_end_anchorage_id": pl.String,
            "orig_iso3": pl.String,
            "orig_label": pl.String,
            "orig_lat": pl.Float64,
            "orig_lon": pl.Float64,
            "dest_iso3": pl.String,
            "dest_label": pl.String,
            "dest_lat": pl.Float64,
            "dest_lon": pl.Float64,
            "route": pl.String,
        },
    )


def build_registry() -> pl.DataFrame:
    """Vessel registry. All laden + ballast ssvids are VLCC candidates;
    mmsi 999 is a non-VLCC that the laden in-progress filter must drop.

    No ``dwt`` column → every voyage's cargo_tons falls back to the
    ROUTE_NOMINAL_DWT (270,000). The snapshot expected values below assume
    this uniform 270k multiplier.
    """
    rows = [
        {"mmsi": V101, "is_vlcc_candidate": True},
        {"mmsi": V102, "is_vlcc_candidate": True},
        {"mmsi": V103, "is_vlcc_candidate": True},
        {"mmsi": V104, "is_vlcc_candidate": True},
        {"mmsi": V105, "is_vlcc_candidate": True},
        {"mmsi": V106, "is_vlcc_candidate": True},
        {"mmsi": V107, "is_vlcc_candidate": True},
        {"mmsi": V108, "is_vlcc_candidate": True},
        {"mmsi": V109, "is_vlcc_candidate": True},
        {"mmsi": B201, "is_vlcc_candidate": True},
        {"mmsi": B202, "is_vlcc_candidate": True},
        {"mmsi": NON_VLCC, "is_vlcc_candidate": False},
    ]
    return pl.DataFrame(rows, schema={"mmsi": pl.Int64, "is_vlcc_candidate": pl.Boolean})


def build_distance_cache() -> pl.DataFrame:
    """Four anchorage pairs covering both the laden and ballast routes.

    All pairs used by the voyages fixture hit the cache → 0 great-circle
    fallbacks. If future snapshots need to exercise the gc fallback, add
    a voyage whose anchorage pair is NOT in this table.
    """
    rows = [
        {"origin_s2id": RT, "dest_s2id": NB, "nautical_miles": NM_RT_NB},
        {"origin_s2id": BR, "dest_s2id": QD, "nautical_miles": NM_BR_QD},
        {"origin_s2id": NB, "dest_s2id": RT, "nautical_miles": NM_RT_NB},
        {"origin_s2id": QD, "dest_s2id": BR, "nautical_miles": NM_BR_QD},
    ]
    return pl.DataFrame(
        rows,
        schema={
            "origin_s2id": pl.String,
            "dest_s2id": pl.String,
            "nautical_miles": pl.Float64,
        },
    )


def build_dark_fleet() -> pl.DataFrame:
    """Three unmatched SAR detections positioned to produce distinct
    dark-fleet-window counts across the snapshot test dates.

    Per ADR 0007 the window is closed on both ends:
    ``[as_of_EoD - 7d, as_of_EoD]``. Given ``as_of_EoD =
    YYYY-MM-DD 23:59:59.999999 UTC``, placing a detection at
    ``day X 10:00 UTC`` means it is in the window for any ``as_of`` such
    that ``X-1 <= as_of_EoD - 7d < X <= as_of_EoD``, i.e. the window is
    ``[as_of - 7, as_of]`` treated inclusively at day granularity.

    Resulting per-date dark counts:

    - 2020-03-01: 0 (all detections post-date the 3/01 window)
    - 2020-03-05: 0 (same reason)
    - 2020-03-15: 0 (all three detections post-date 3/15 EoD)
    - 2020-03-18: 1 (D1 @ 3/17 falls inside [3/11 EoD, 3/18 EoD])
    - 2020-03-20: 2 (D1 @ 3/17 + D2 @ 3/19 fall inside [3/13 EoD, 3/20 EoD])
    - 2020-03-22: 3 (D1 + D2 + D3 @ 3/21 fall inside [3/15 EoD, 3/22 EoD])
    """
    rows = [
        # D1 — in windows of 3/18, 3/20, 3/22; NOT 3/15 (> 3/15 EoD)
        {
            "mmsi": None,
            "detection_timestamp": datetime(2020, 3, 17, 10, 0, 0, tzinfo=UTC),
            "nearest_anchorage_id": RT,
            "nearest_anchorage_label": "RAS TANURA",
            "has_matching_voyage": False,
        },
        # D2 — in windows of 3/20 and 3/22; NOT 3/18 (> 3/18 EoD)
        {
            "mmsi": None,
            "detection_timestamp": datetime(2020, 3, 19, 12, 0, 0, tzinfo=UTC),
            "nearest_anchorage_id": BR,
            "nearest_anchorage_label": "AL-BASRAH OIL TERMINAL",
            "has_matching_voyage": False,
        },
        # D3 — in window of 3/22 only
        {
            "mmsi": None,
            "detection_timestamp": datetime(2020, 3, 21, 12, 0, 0, tzinfo=UTC),
            "nearest_anchorage_id": RT,
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


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    build_voyages().write_parquet(FIXTURE_DIR / "voyages.parquet")
    build_registry().write_parquet(FIXTURE_DIR / "vessel_registry.parquet")
    build_distance_cache().write_parquet(FIXTURE_DIR / "distance_cache.parquet")
    build_dark_fleet().write_parquet(FIXTURE_DIR / "dark_fleet.parquet")
    print(f"wrote snapshot fixtures to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
