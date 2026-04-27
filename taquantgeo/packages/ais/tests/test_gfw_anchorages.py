"""Tests for GFW anchorages loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from taquantgeo_ais.gfw.anchorages import filter_by_iso3, load_anchorages

if TYPE_CHECKING:
    from pathlib import Path


_CSV = """\
s2id,lat,lon,label,sublabel,label_source,iso3,distance_from_shore_m,drift_radius,dock
a1,26.7,50.1,RAS_TANURA,,top_destination,SAU,,0.1,false
a2,30.1,122.4,NINGBO,,top_destination,CHN,,0.1,true
a3,25.1,56.3,FUJAIRAH,,top_destination,ARE,,0.1,false
"""


@pytest.fixture
def anchorages_path(tmp_path: Path) -> Path:
    p = tmp_path / "anchorages.csv"
    p.write_text(_CSV)
    return p


def test_load_anchorages_parses_rows(anchorages_path: Path) -> None:
    df = load_anchorages(anchorages_path)
    assert df.shape == (3, 10)
    assert set(df["iso3"].to_list()) == {"SAU", "CHN", "ARE"}


def test_load_anchorages_raises_on_missing_columns(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_anchorages(p)


def test_filter_by_iso3_keeps_requested_countries(anchorages_path: Path) -> None:
    df = load_anchorages(anchorages_path)
    gulf = filter_by_iso3(df, frozenset({"SAU", "ARE"}))
    assert gulf.shape[0] == 2
    assert set(gulf["iso3"].to_list()) == {"SAU", "ARE"}


def test_filter_by_iso3_accepts_list_and_set(anchorages_path: Path) -> None:
    df = load_anchorages(anchorages_path)
    assert filter_by_iso3(df, ["CHN"]).shape[0] == 1
    assert filter_by_iso3(df, {"CHN"}).shape[0] == 1
    assert filter_by_iso3(df, frozenset({"CHN"})).shape[0] == 1
