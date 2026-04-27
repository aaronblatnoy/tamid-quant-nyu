"""Tests for GFW voyage loading/join/filter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import pytest

from taquantgeo_ais.gfw.routes import TD3C
from taquantgeo_ais.gfw.voyages import filter_by_route, join_to_anchorages, load_voyages

if TYPE_CHECKING:
    from pathlib import Path

_ANCHORAGES = pl.DataFrame(
    {
        "s2id": ["gulf1", "chn1", "usa1"],
        "lat": [26.7, 30.1, 37.8],
        "lon": [50.1, 122.4, -122.4],
        "iso3": ["SAU", "CHN", "USA"],
        "label": ["RAS_TANURA", "NINGBO", "OAKLAND"],
    }
)

_VOYAGES_CSV = """\
ssvid,vessel_id,trip_id,trip_start,trip_end,trip_start_anchorage_id,trip_end_anchorage_id,trip_start_visit_id,trip_end_visit_id
100,v1,t1,2024-01-01 00:00:00 UTC,2024-01-25 00:00:00 UTC,gulf1,chn1,vs1,ve1
101,v2,t2,2024-01-05 00:00:00 UTC,2024-01-10 00:00:00 UTC,gulf1,chn1,vs2,ve2
102,v3,t3,2024-01-01 00:00:00 UTC,2024-01-26 00:00:00 UTC,usa1,chn1,vs3,ve3
103,v4,t4,2024-01-01 00:00:00 UTC,2024-02-02 00:00:00 UTC,gulf1,chn1,vs4,ve4
"""


@pytest.fixture
def voyages_path(tmp_path: Path) -> Path:
    p = tmp_path / "voyages.csv"
    p.write_text(_VOYAGES_CSV)
    return p


def test_load_voyages_parses_timestamps(voyages_path: Path) -> None:
    df = load_voyages(voyages_path)
    assert df.shape == (4, 9)
    assert df["trip_start"].dtype == pl.Datetime
    assert df["trip_end"].dtype == pl.Datetime


def test_join_to_anchorages_enriches_origin_and_destination(voyages_path: Path) -> None:
    voyages = load_voyages(voyages_path)
    enriched = join_to_anchorages(voyages, _ANCHORAGES)
    row = enriched.filter(pl.col("ssvid") == 100).to_dicts()[0]
    assert row["orig_iso3"] == "SAU"
    assert row["orig_label"] == "RAS_TANURA"
    assert row["dest_iso3"] == "CHN"
    assert row["dest_label"] == "NINGBO"
    assert row["duration_days"] == 24.0


def test_filter_by_td3c_keeps_pg_china_in_duration_band(voyages_path: Path) -> None:
    voyages = load_voyages(voyages_path)
    enriched = join_to_anchorages(voyages, _ANCHORAGES)
    # With duration filter: only v1 (24 days) and v4 (32 days) survive.
    # v2 is 5 days (too short), v3 is USA origin (wrong geography).
    filtered = filter_by_route(enriched, TD3C)
    assert set(filtered["ssvid"].to_list()) == {100, 103}


def test_filter_by_td3c_without_duration_admits_short_voyages(voyages_path: Path) -> None:
    voyages = load_voyages(voyages_path)
    enriched = join_to_anchorages(voyages, _ANCHORAGES)
    filtered = filter_by_route(enriched, TD3C, apply_duration_filter=False)
    # Drops v3 (USA origin) but keeps v1, v2, v4.
    assert set(filtered["ssvid"].to_list()) == {100, 101, 103}
