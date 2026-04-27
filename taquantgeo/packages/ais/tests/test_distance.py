"""Tests for taquantgeo_ais.gfw.distance.

Three snapshot distances are pinned against industry-reference sea-route
values at ±3% tolerance (absorbs searoute-py waypoint updates; a true
graph-shape regression past that tolerance fires the test, which is what
we want). The pinned centers are the sea-route distances produced by
searoute-py 1.5.0 (MIT; a port of the SeaRoutes industry graph) for each
pair. They agree to within low-single-digit percent with Worldscale
flat-rate tables for Ras Tanura and Basrah loading points, which is the
best public reference we have for TD3C sea-miles.

Rebaseline procedure when searoute-py is upgraded beyond 1.5.x:
  1. Temporarily widen ``SNAPSHOT_TOLERANCE`` and re-run these tests to
     see the new centers.
  2. If the shift looks like a genuine graph improvement (chokepoint
     geometry refined, new waypoint added), update the three pinned
     constants below and the searoute version note.
  3. Open a PR explaining the shift with the new vs. old numbers and
     keep the tolerance at ±3%.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from taquantgeo_ais.gfw import distance as distance_mod
from taquantgeo_ais.gfw.distance import (
    _CACHE_COLUMN_ORDER,
    _CACHE_SCHEMA,
    build_distance_cache,
    collect_unique_pairs,
    compute_distances_cached,
    compute_route_distance,
    great_circle_nm,
)

FIXTURE = Path(__file__).parent / "fixtures" / "distance_sample_voyages.parquet"

# Known VLCC loading points / Chinese discharge points in (lat, lon).
RAS_TANURA = (26.70, 50.18)
NINGBO = (29.87, 121.55)
BASRAH_OT = (29.72, 48.83)
QINGDAO = (36.07, 120.30)

# Pinned centers — the searoute-py 1.5.0 values. Each ±3% absorbs normal
# waypoint-graph updates; a 3%+ divergence is a real regression.
RAS_TANURA_NINGBO_VIA_MALACCA_NM = 5920.0
RAS_TANURA_NINGBO_VIA_SUNDA_NM = 6524.0
BASRAH_QINGDAO_VIA_MALACCA_NM = 6416.0
SNAPSHOT_TOLERANCE = 0.03


def _pct(actual: float, expected: float) -> float:
    return abs(actual - expected) / expected


def _stage_voyages_dir(tmp_path: Path) -> Path:
    voyages_dir = tmp_path / "voyages"
    partition = voyages_dir / "route=td3c" / "year=2026" / "month=03"
    partition.mkdir(parents=True)
    pl.read_parquet(FIXTURE).write_parquet(partition / "fixture.parquet")
    return voyages_dir


def test_ras_tanura_to_ningbo_via_malacca_within_3pct() -> None:
    """TD3C flagship route: Ras Tanura → Ningbo, Malacca-preferred.

    searoute-py 1.5.0 pins this pair at ~5920 NM one-way. Cross-check:
    Worldscale flat-rate tables list Ras Tanura → Ningbo one-way at
    ~5800-6000 NM via Malacca - searoute lands within that band. An
    upstream graph change that moves this past ±3% is a real regression
    worth investigating.
    """
    nm = compute_route_distance(RAS_TANURA, NINGBO, prefer_malacca=True)
    assert _pct(nm, RAS_TANURA_NINGBO_VIA_MALACCA_NM) < SNAPSHOT_TOLERANCE, (
        f"got {nm:.1f} NM, expected {RAS_TANURA_NINGBO_VIA_MALACCA_NM:.0f} ± 3%"
    )


def test_ras_tanura_to_ningbo_via_sunda_adds_expected_diversion() -> None:
    """Forcing Sunda (Malacca restricted) adds ~600 NM vs Malacca - matches
    the industry rule-of-thumb that a Sunda/Lombok diversion costs
    10-11% of the PG → China laden leg. searoute-py 1.5.0 pins the
    no-Malacca route at ~6524 NM."""
    nm = compute_route_distance(RAS_TANURA, NINGBO, prefer_malacca=False)
    assert _pct(nm, RAS_TANURA_NINGBO_VIA_SUNDA_NM) < SNAPSHOT_TOLERANCE, (
        f"got {nm:.1f} NM, expected {RAS_TANURA_NINGBO_VIA_SUNDA_NM:.0f} ± 3%"
    )
    # Sanity: Sunda must be strictly longer than Malacca for this pair.
    malacca = compute_route_distance(RAS_TANURA, NINGBO, prefer_malacca=True)
    assert nm > malacca, "Sunda route should be strictly longer than Malacca"


def test_basrah_to_qingdao_via_malacca_within_3pct() -> None:
    """Basrah Oil Terminal → Qingdao via Malacca. Longer than Ras Tanura →
    Ningbo because Basrah is deeper in the Gulf and Qingdao is further
    north than Ningbo. searoute-py 1.5.0 pins this pair at ~6416 NM,
    consistent with published VLCC passage-planning distances for
    head-of-Gulf to N-China discharge."""
    nm = compute_route_distance(BASRAH_OT, QINGDAO, prefer_malacca=True)
    assert _pct(nm, BASRAH_QINGDAO_VIA_MALACCA_NM) < SNAPSHOT_TOLERANCE, (
        f"got {nm:.1f} NM, expected {BASRAH_QINGDAO_VIA_MALACCA_NM:.0f} ± 3%"
    )


def test_distance_disconnected_returns_great_circle_with_warn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """When searoute raises (e.g. graph-disconnected on invalid input) we
    must return the great-circle distance, log a WARN identifying the
    exception, and flag the cache row."""
    exc_marker = "simulated disconnected-components failure"

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(exc_marker)

    monkeypatch.setattr(distance_mod.sr, "searoute", _raise)
    origin = (26.70, 50.18)
    dest = (29.87, 121.55)
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.distance"):
        nm = compute_route_distance(origin, dest, prefer_malacca=True)
    expected = great_circle_nm(*origin, *dest)
    assert nm == pytest.approx(expected, rel=1e-9)
    # Tight substring match on the exception message proves the raise-branch
    # (not the zero-length branch) was taken.
    assert any(exc_marker in r.message for r in caplog.records)

    pairs = [("o1", "d1", *origin, *dest)]
    df = build_distance_cache(pairs, tmp_path / "disconnected_cache.parquet")
    assert df.height == 1
    assert bool(df.get_column("is_great_circle_fallback")[0]) is True
    assert df.get_column("nautical_miles")[0] == pytest.approx(expected, rel=1e-9)


def test_distance_zero_length_for_non_coincident_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If searoute returns a ~0 NM route for points that are clearly not
    the same location (landlocked snap-to-same-node), we fall back to
    great-circle. This is the genuine "disconnected graph" case — distinct
    from the exception path above."""

    class _Feature:
        def __init__(self) -> None:
            self.properties: dict[str, Any] = {"length": 0.0}

    monkeypatch.setattr(distance_mod.sr, "searoute", lambda *a, **k: _Feature())
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.distance"):
        nm = compute_route_distance(RAS_TANURA, NINGBO)
    expected = great_circle_nm(*RAS_TANURA, *NINGBO)
    assert nm == pytest.approx(expected, rel=1e-9)
    assert any("non-coincident" in r.message for r in caplog.records)


def test_distance_non_numeric_length_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If searoute's GeoJSON comes back without a parseable length (None,
    missing key, garbage string) we fall back rather than crashing or
    silently emitting NaN."""

    class _BadFeature:
        def __init__(self) -> None:
            self.properties: dict[str, Any] = {"length": "not-a-number"}

    monkeypatch.setattr(distance_mod.sr, "searoute", lambda *a, **k: _BadFeature())
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.distance"):
        nm = compute_route_distance(RAS_TANURA, NINGBO)
    assert nm == pytest.approx(great_circle_nm(*RAS_TANURA, *NINGBO), rel=1e-9)
    assert any("non-numeric length" in r.message for r in caplog.records)


def test_distance_coincident_points_returns_zero_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When origin == dest, both great-circle and sea-route are ~0 NM;
    the fallback must NOT trigger (both sides of the threshold are ≤ε)."""

    class _ZeroFeature:
        def __init__(self) -> None:
            self.properties: dict[str, Any] = {"length": 0.0}

    monkeypatch.setattr(distance_mod.sr, "searoute", lambda *a, **k: _ZeroFeature())
    pairs = [("same", "same", 26.70, 50.18, 26.70, 50.18)]
    df = build_distance_cache(pairs, tmp_path / "coincident.parquet")
    assert df.height == 1
    assert df.get_column("nautical_miles")[0] == pytest.approx(0.0, abs=1e-9)
    assert bool(df.get_column("is_great_circle_fallback")[0]) is False


def test_distance_idempotent_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Second invocation against the same voyages dir must not recompute
    cached pairs. Verified by call-counter on searoute."""
    voyages_dir = _stage_voyages_dir(tmp_path)
    out = tmp_path / "distance_cache.parquet"

    call_count = {"n": 0}
    original = distance_mod.sr.searoute

    def _counting(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(distance_mod.sr, "searoute", _counting)

    df1 = compute_distances_cached(voyages_dir, out)
    assert df1.height == 2
    first_call_count = call_count["n"]
    assert first_call_count == 2

    df2 = compute_distances_cached(voyages_dir, out)
    assert df2.height == df1.height
    assert call_count["n"] == first_call_count, (
        "searoute was called again on the second invocation - cache is not idempotent"
    )
    # Strong idempotency: computed_at unchanged row-for-row means the cache
    # file was not silently rewritten.
    assert df2.get_column("computed_at").to_list() == df1.get_column("computed_at").to_list()

    # Force: pairs are recomputed even though they are in the cache.
    df3 = compute_distances_cached(voyages_dir, out, force=True)
    assert df3.height == df1.height
    assert call_count["n"] == first_call_count + 2


def test_compute_distances_cached_partial_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the cache has some but not all pairs, only the missing ones
    are computed; cached rows carry through unchanged."""
    voyages_dir = _stage_voyages_dir(tmp_path)
    out = tmp_path / "cache.parquet"

    # Seed the cache with ONE of the two pairs.
    seed_pairs = [("a-rastanura", "b-ningbo", 26.70, 50.18, 29.87, 121.55)]
    seed_df = build_distance_cache(seed_pairs, out)
    seed_computed_at = seed_df.get_column("computed_at")[0]

    call_count = {"n": 0}
    original = distance_mod.sr.searoute

    def _counting(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(distance_mod.sr, "searoute", _counting)

    merged = compute_distances_cached(voyages_dir, out)
    assert merged.height == 2
    # Exactly one new searoute call — for the missing Basrah pair.
    assert call_count["n"] == 1
    # Cached row preserved unchanged (same computed_at).
    rastanura_row = merged.filter(pl.col("origin_s2id") == "a-rastanura")
    assert rastanura_row.get_column("computed_at")[0] == seed_computed_at
    basrah_row = merged.filter(pl.col("origin_s2id") == "c-basrah")
    assert basrah_row.height == 1
    assert basrah_row.get_column("computed_at")[0] != seed_computed_at


def test_compute_distances_cached_empty_voyages_dir(tmp_path: Path) -> None:
    """Orchestrator against an empty tree writes an empty, schema-correct
    cache parquet — not a crash."""
    voyages_dir = tmp_path / "empty_voyages"
    voyages_dir.mkdir()
    out = tmp_path / "empty_cache.parquet"
    df = compute_distances_cached(voyages_dir, out)
    assert df.height == 0
    assert out.exists()
    reread = pl.read_parquet(out)
    assert tuple(reread.columns) == _CACHE_COLUMN_ORDER
    assert reread.height == 0


def test_compute_distances_cached_corrupt_cache_recovers(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """A truncated / corrupt cache file from a crashed prior run must not
    poison subsequent invocations — it's treated as 'no cache' and
    recomputed. Assertion on the WARN log proves the recovery branch ran
    rather than the recompute happening for an unrelated reason."""
    voyages_dir = _stage_voyages_dir(tmp_path)
    out = tmp_path / "cache.parquet"
    out.write_bytes(b"not a parquet file")

    call_count = {"n": 0}
    original = distance_mod.sr.searoute

    def _counting(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(distance_mod.sr, "searoute", _counting)
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.distance"):
        df = compute_distances_cached(voyages_dir, out)
    assert df.height == 2
    assert call_count["n"] == 2
    assert any("unreadable" in r.message for r in caplog.records)


def test_compute_distances_cached_schema_drift_recovers(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """An older cache missing required columns (e.g. from before the
    is_great_circle_fallback column existed) is treated as 'no cache'."""
    voyages_dir = _stage_voyages_dir(tmp_path)
    out = tmp_path / "cache.parquet"
    # Write a readable parquet missing is_great_circle_fallback.
    pl.DataFrame(
        {
            "origin_s2id": ["a-rastanura"],
            "dest_s2id": ["b-ningbo"],
            "nautical_miles": [5920.0],
        }
    ).write_parquet(out)

    call_count = {"n": 0}
    original = distance_mod.sr.searoute

    def _counting(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(distance_mod.sr, "searoute", _counting)
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.distance"):
        df = compute_distances_cached(voyages_dir, out)
    assert df.height == 2
    assert call_count["n"] == 2
    assert any("missing columns" in r.message for r in caplog.records)


def test_compute_distances_cached_dtype_drift_recovers(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Readable parquet with all columns present but wrong dtypes (e.g.
    int nautical_miles from an older pipeline) is rejected rather than
    silently crashing the downstream concat."""
    from datetime import UTC, datetime  # noqa: PLC0415

    voyages_dir = _stage_voyages_dir(tmp_path)
    out = tmp_path / "cache.parquet"
    pl.DataFrame(
        {
            "origin_s2id": ["a-rastanura"],
            "dest_s2id": ["b-ningbo"],
            "origin_lat": [26.70],
            "origin_lon": [50.18],
            "dest_lat": [29.87],
            "dest_lon": [121.55],
            "nautical_miles": [5920],  # Int64 instead of Float64
            "is_great_circle_fallback": [False],
            "computed_at": [datetime.now(tz=UTC)],
        }
    ).write_parquet(out)
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.distance"):
        df = compute_distances_cached(voyages_dir, out)
    assert df.height == 2
    assert any("drifted dtypes" in r.message for r in caplog.records)


def test_atomic_write_removes_tmp_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If os.replace fails after the tmp is written, the partial tmp must
    be removed and out_path must retain its prior contents (or not exist).
    Proves the crash-safety contract end-to-end."""
    out = tmp_path / "cache.parquet"
    # Seed a good cache to ensure old content survives the failed rewrite.
    build_distance_cache([("a", "b", 26.70, 50.18, 29.87, 121.55)], out)
    original_bytes = out.read_bytes()

    def _raise_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(distance_mod.os, "replace", _raise_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        build_distance_cache([("c", "d", 29.72, 48.83, 36.07, 120.30)], out)
    assert out.exists()
    assert out.read_bytes() == original_bytes, "old cache must remain untouched"
    tmp_file = out.with_suffix(out.suffix + ".tmp")
    assert not tmp_file.exists(), "partial tmp must be cleaned up on failure"


def test_cache_schema_column_order_and_types(tmp_path: Path) -> None:
    """The cache parquet must have the declared column order and types -
    downstream joins rely on both."""
    out = tmp_path / "cache.parquet"
    df = build_distance_cache(
        [("a", "b", 26.70, 50.18, 29.87, 121.55)],
        out,
    )
    assert tuple(df.columns) == _CACHE_COLUMN_ORDER
    for col, expected_dtype in _CACHE_SCHEMA.items():
        actual = df.schema[col]
        assert actual == expected_dtype, (
            f"column {col}: got {actual!r}, expected {expected_dtype!r}"
        )
    reread = pl.read_parquet(out)
    assert tuple(reread.columns) == _CACHE_COLUMN_ORDER


def test_atomic_write_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    out = tmp_path / "cache.parquet"
    build_distance_cache([("a", "b", 26.70, 50.18, 29.87, 121.55)], out)
    assert out.exists()
    tmp_file = out.with_suffix(out.suffix + ".tmp")
    assert not tmp_file.exists(), "tmp sibling should have been os.replace'd onto out"


def test_collect_unique_pairs_drops_nulls_dedupes_and_preserves_coords(
    tmp_path: Path,
) -> None:
    """collect_unique_pairs drops null-anchorage rows, dedupes on (origin_s2id,
    dest_s2id), and keeps the original lat/lons. We assert the actual
    coordinate values to catch any alias/swap bug."""
    voyages_dir = _stage_voyages_dir(tmp_path)
    pairs = collect_unique_pairs(voyages_dir)
    assert pairs.height == 2
    rows = {
        (r["origin_s2id"], r["dest_s2id"]): (
            r["origin_lat"],
            r["origin_lon"],
            r["dest_lat"],
            r["dest_lon"],
        )
        for r in pairs.iter_rows(named=True)
    }
    assert rows[("a-rastanura", "b-ningbo")] == (26.70, 50.18, 29.87, 121.55)
    assert rows[("c-basrah", "d-qingdao")] == (29.72, 48.83, 36.07, 120.30)


def test_collect_unique_pairs_warns_on_inconsistent_latlon(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If two rows have the same s2id pair but different lat/lons (upstream
    data drift), the first one wins AND we log a WARN — silent drift is the
    failure mode the audit message is there to prevent."""
    voyages_dir = tmp_path / "voyages"
    partition = voyages_dir / "route=td3c" / "year=2026" / "month=03"
    partition.mkdir(parents=True)
    pl.DataFrame(
        {
            "trip_start_anchorage_id": ["x", "x"],
            "trip_end_anchorage_id": ["y", "y"],
            "orig_lat": [10.0, 11.0],  # different lat!
            "orig_lon": [20.0, 20.0],
            "dest_lat": [30.0, 30.0],
            "dest_lon": [40.0, 40.0],
        }
    ).write_parquet(partition / "drift.parquet")
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.distance"):
        pairs = collect_unique_pairs(voyages_dir)
    assert pairs.height == 1
    assert any("inconsistent lat/lon" in r.message for r in caplog.records)


def test_collect_unique_pairs_empty_tree_returns_empty(tmp_path: Path) -> None:
    """Empty voyages_dir must not raise - returns a typed-but-empty frame."""
    pairs = collect_unique_pairs(tmp_path)
    assert pairs.height == 0
    assert set(pairs.columns) >= {"origin_s2id", "dest_s2id"}


def test_great_circle_sanity() -> None:
    """Great-circle Ras Tanura → Ningbo is ~3500-3900 NM (arc over land) -
    must be materially shorter than the sea-route. This is exactly the
    ton-mile-understatement we're avoiding by using searoute."""
    gc = great_circle_nm(*RAS_TANURA, *NINGBO)
    assert 3500.0 < gc < 3900.0
    sea = compute_route_distance(RAS_TANURA, NINGBO, prefer_malacca=True)
    assert sea > gc * 1.4, "sea-route should be >>> great-circle for PG→China"


def test_great_circle_self_distance_is_zero_and_antipode_is_pi_r() -> None:
    assert great_circle_nm(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-9)
    # Equatorial antipode: (0,0) ↔ (0,180) — maximum great-circle arc = π·R.
    assert great_circle_nm(0.0, 0.0, 0.0, 180.0) == pytest.approx(math.pi * 3440.065, rel=1e-6)
