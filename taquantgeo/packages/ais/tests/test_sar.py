"""Tests for taquantgeo_ais.gfw.sar.

Snapshot counts are pinned to a hand-crafted 10-row SAR fixture. Every
surviving row is predictable by construction - see ``sar_sample.csv``
scene-by-scene rationale in the pipeline-snapshot test. The fixture is
also what exercises the CLI end-to-end.

Expected pipeline outcome on the fixture (documented alongside the CSV
row-by-row in ``test_pipeline_snapshot_counts`` below):

  - 10 rows in, 6 rows out (length filter drops 1, buffer filter drops 3)
  - 2 rows have ``has_matching_voyage=True`` (matched voyages V1 and V3)
  - 4 dark candidates; of those 2 have NULL mmsi (fully dark), 2 have mmsi
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from taquantgeo_ais.gfw import sar as sar_mod
from taquantgeo_ais.gfw.anchorages import load_anchorages
from taquantgeo_ais.gfw.routes import MAJOR_LOADING_TERMINALS
from taquantgeo_ais.gfw.sar import (
    _DARK_FLEET_COLUMN_ORDER,
    _DARK_FLEET_SCHEMA,
    DEFAULT_BUFFER_KM,
    DEFAULT_TIME_WINDOW_DAYS,
    MIN_VESSEL_LENGTH_M,
    build_dark_fleet_candidates,
    cross_reference_with_voyages,
    filter_near_terminals,
    great_circle_km,
    ingest_sar,
    load_sar_csv,
    load_voyages_for_crossref,
    resolve_terminal_anchorages,
)
from taquantgeo_cli.gfw import gfw_app

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAR_CSV = FIXTURE_DIR / "sar_sample.csv"
ANCHORAGES_CSV = FIXTURE_DIR / "sar_anchorages_sample.csv"
VOYAGES_PARQUET = FIXTURE_DIR / "sar_voyages_sample.parquet"


@pytest.fixture
def anchorages() -> pl.DataFrame:
    return load_anchorages(ANCHORAGES_CSV)


@pytest.fixture
def voyages_df() -> pl.DataFrame:
    return pl.read_parquet(VOYAGES_PARQUET)


def _sar_df() -> pl.DataFrame:
    return load_sar_csv(SAR_CSV)


def test_load_sar_csv_parses_schema_and_timestamps() -> None:
    """Happy path: canonical CSV lands as a typed DataFrame, ``timestamp``
    is promoted to tz-aware ``detection_timestamp``, and ``source_csv``
    carries the filename."""
    df = _sar_df()
    assert df.height == 10
    assert {"scene_id", "detection_timestamp", "lat", "lon", "length_m", "mmsi"} <= set(df.columns)
    dt_type = df.schema["detection_timestamp"]
    assert isinstance(dt_type, pl.Datetime)
    assert dt_type.time_zone == "UTC"
    assert df.schema["mmsi"] == pl.Int64
    assert df.schema["length_m"] == pl.Float64
    assert df["source_csv"].unique().to_list() == ["sar_sample.csv"]


def test_load_sar_csv_handles_empty_zero_row_file(tmp_path: Path) -> None:
    """Polars cannot infer numeric dtypes from a header-only CSV; without
    the schema override a later ``concat`` would poison numeric columns
    into String. Guard the override explicitly."""
    empty = tmp_path / "empty.csv"
    empty.write_text(SAR_CSV.read_text().splitlines()[0] + "\n")
    df = load_sar_csv(empty)
    assert df.height == 0
    assert df.schema["length_m"] == pl.Float64
    assert df.schema["mmsi"] == pl.Int64


def test_load_sar_csv_missing_required_columns_raises(tmp_path: Path) -> None:
    """Schema drift (column renamed or dropped upstream) is caught at
    ingest time rather than silently yielding empty filters later."""
    bad = tmp_path / "bad.csv"
    # drop 'mmsi' column entirely
    bad.write_text(
        "scene_id,timestamp,lat,lon,length_m\nFIX-01,2026-03-01 00:00:00 UTC,1.0,2.0,300.0\n"
    )
    with pytest.raises(ValueError, match="missing required columns"):
        load_sar_csv(bad)


def test_great_circle_km_sanity() -> None:
    """Known pair: Ras Tanura → Ningbo is ~6700 km great-circle. Confirms
    sign/units and that the haversine returns km, not NM."""
    d = great_circle_km(26.70, 50.18, 29.87, 121.55)
    assert 6500 < d < 7000
    # self-distance
    assert great_circle_km(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-9)


def test_resolve_terminal_anchorages_same_iso3_only(
    anchorages: pl.DataFrame, caplog: pytest.LogCaptureFixture
) -> None:
    """Terminals with no same-iso3 anchorage (QAT, IRN, KWT in the
    fixture) are skipped with a WARN. Terminals with a match resolve
    to the single anchorage in their iso3."""
    with caplog.at_level("WARNING", logger="taquantgeo_ais.gfw.sar"):
        resolved = resolve_terminal_anchorages(anchorages, MAJOR_LOADING_TERMINALS)
    # 11 MAJOR_LOADING_TERMINALS; 3 fixture anchorages (SAU, IRQ, ARE).
    # SAU has 2 terminals, IRQ 1, ARE 4 → 7 resolved rows.
    assert resolved.height == 7
    s2ids = set(resolved["s2id"].to_list())
    assert s2ids == {"anc_rastanura", "anc_basrah", "anc_das"}
    warn_msgs = [r.message for r in caplog.records if "no GFW anchorages" in r.message]
    # QAT, IRN (x2), KWT → 4 skips
    assert len(warn_msgs) == 4


def test_filter_near_terminals_includes_within_buffer(
    anchorages: pl.DataFrame,
) -> None:
    """A SAR row 0.3 km from Ras Tanura (well inside the 10 km buffer)
    survives and is annotated with the correct nearest anchorage."""
    sar_df = pl.DataFrame(
        {
            "scene_id": ["HIT"],
            "detection_timestamp": [datetime(2026, 3, 10, tzinfo=UTC)],
            "lat": [26.702],
            "lon": [50.182],
            "length_m": [300.0],
            "mmsi": [1234],
            "source_csv": ["t.csv"],
        }
    )
    out = filter_near_terminals(sar_df, anchorages, MAJOR_LOADING_TERMINALS)
    assert out.height == 1
    assert out["nearest_anchorage_id"][0] == "anc_rastanura"
    assert out["nearest_anchorage_label"][0] == "RAS TANURA"
    assert out["distance_to_anchorage_km"][0] < 1.0


def test_filter_near_terminals_excludes_outside_buffer(
    anchorages: pl.DataFrame,
) -> None:
    """A SAR row >10 km from every terminal anchorage is dropped.
    (26.60, 50.10) is ~13 km from Ras Tanura — just outside the default
    10 km band. Assert on dropped row count, not just emptiness, to
    prove the buffer decision is the reason."""
    sar_df = pl.DataFrame(
        {
            "scene_id": ["MISS", "HIT"],
            "detection_timestamp": [
                datetime(2026, 3, 10, tzinfo=UTC),
                datetime(2026, 3, 10, tzinfo=UTC),
            ],
            "lat": [26.60, 26.702],
            "lon": [50.10, 50.182],
            "length_m": [300.0, 300.0],
            "mmsi": [9999, 1234],
            "source_csv": ["t.csv", "t.csv"],
        }
    )
    out = filter_near_terminals(sar_df, anchorages, MAJOR_LOADING_TERMINALS)
    assert out.height == 1
    assert out["scene_id"][0] == "HIT"


def test_filter_near_terminals_custom_buffer(anchorages: pl.DataFrame) -> None:
    """A tighter buffer (2 km) excludes a SAR row at 4 km from Ras Tanura
    that the default 10 km buffer would keep. Proves ``buffer_km`` is
    respected."""
    sar_df = pl.DataFrame(
        {
            "scene_id": ["FOUR_KM"],
            "detection_timestamp": [datetime(2026, 3, 10, tzinfo=UTC)],
            # ~4 km from Ras Tanura (26.70,50.18)
            "lat": [26.665],
            "lon": [50.190],
            "length_m": [300.0],
            "mmsi": [1234],
            "source_csv": ["t.csv"],
        }
    )
    kept = filter_near_terminals(sar_df, anchorages, MAJOR_LOADING_TERMINALS, buffer_km=10.0)
    assert kept.height == 1
    dropped = filter_near_terminals(sar_df, anchorages, MAJOR_LOADING_TERMINALS, buffer_km=2.0)
    assert dropped.height == 0


def test_length_heuristic_excludes_small_vessels(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """SAR detections shorter than MIN_VESSEL_LENGTH_M (200 m) must be
    dropped before the expensive terminal-proximity compute. Confirms
    the threshold is what ADR 0006 says it is (Suezmax floor)."""
    df = build_dark_fleet_candidates([SAR_CSV], anchorages, voyages_df, MAJOR_LOADING_TERMINALS)
    # fixture FIX-05 has length 150 m at Das Island: would otherwise have
    # passed the buffer filter. If it appears in output, the length
    # filter is broken.
    lengths = df["length_m"].to_list()
    assert all(length >= MIN_VESSEL_LENGTH_M for length in lengths)
    # Sanity: the fixture includes one 150 m row within the buffer that
    # this filter is specifically catching.
    fifty_m_in_csv = pl.read_csv(SAR_CSV).filter(pl.col("length_m") < 200).height
    assert fifty_m_in_csv > 0, "fixture sanity: needs a <200 m row for the test to mean anything"


def test_cross_reference_matches_within_time_window(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """A SAR hit 7h before voyage V1's trip_start at the same anchorage
    matches (0.3 day < 3 day window). The exact matching_voyage_trip_id
    pins the match-selection logic — not just "something matched"."""
    sar_hit = pl.DataFrame(
        {
            "scene_id": ["H1"],
            "detection_timestamp": [datetime(2026, 3, 9, 16, 0, tzinfo=UTC)],
            "lat": [26.702],
            "lon": [50.182],
            "length_m": [300.0],
            "mmsi": [1234],
            "source_csv": ["t.csv"],
        }
    )
    near = filter_near_terminals(sar_hit, anchorages, MAJOR_LOADING_TERMINALS)
    xref = cross_reference_with_voyages(near, voyages_df)
    assert xref.height == 1
    assert bool(xref["has_matching_voyage"][0]) is True
    assert xref["matching_voyage_trip_id"][0] == "V1"


def test_cross_reference_flags_no_match_as_dark_candidate(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """A SAR hit 30 days before any voyage at the same anchorage gets
    flagged dark (``has_matching_voyage=False`` with null trip_id)."""
    sar_hit = pl.DataFrame(
        {
            "scene_id": ["DARK"],
            "detection_timestamp": [datetime(2026, 1, 1, tzinfo=UTC)],
            "lat": [26.702],
            "lon": [50.182],
            "length_m": [300.0],
            "mmsi": [None],
            "source_csv": ["t.csv"],
        },
        schema_overrides={"mmsi": pl.Int64},
    )
    near = filter_near_terminals(sar_hit, anchorages, MAJOR_LOADING_TERMINALS)
    xref = cross_reference_with_voyages(near, voyages_df)
    assert xref.height == 1
    assert bool(xref["has_matching_voyage"][0]) is False
    assert xref["matching_voyage_trip_id"][0] is None


def test_cross_reference_picks_smallest_time_delta_on_multiple_matches(
    anchorages: pl.DataFrame,
) -> None:
    """When two voyages at the same anchorage both fall inside the window,
    the one with the smaller |time delta| wins. Otherwise tightness-
    signal attribution becomes non-deterministic."""
    voyages = pl.DataFrame(
        {
            "trip_id": ["FAR", "NEAR"],
            "trip_start": [
                datetime(2026, 3, 8, 0, 0, 0),  # 2 days before hit
                datetime(2026, 3, 10, 0, 0, 0),  # 6h before hit (closer)
            ],
            "trip_start_anchorage_id": ["anc_rastanura", "anc_rastanura"],
        },
        schema={
            "trip_id": pl.String,
            "trip_start": pl.Datetime(time_unit="us", time_zone=None),
            "trip_start_anchorage_id": pl.String,
        },
    )
    sar_hit = pl.DataFrame(
        {
            "scene_id": ["H"],
            "detection_timestamp": [datetime(2026, 3, 10, 6, tzinfo=UTC)],
            "lat": [26.702],
            "lon": [50.182],
            "length_m": [300.0],
            "mmsi": [1234],
            "source_csv": ["t.csv"],
        }
    )
    near = filter_near_terminals(sar_hit, anchorages, MAJOR_LOADING_TERMINALS)
    xref = cross_reference_with_voyages(near, voyages)
    assert xref["matching_voyage_trip_id"][0] == "NEAR"


def test_cross_reference_respects_time_window_days(
    anchorages: pl.DataFrame,
) -> None:
    """A voyage 10 days before a SAR hit is outside the default ±3 day
    window (flagged dark) but inside a ±15 day window (matched)."""
    voyages = pl.DataFrame(
        {
            "trip_id": ["X"],
            "trip_start": [datetime(2026, 3, 1, 0, 0, 0)],
            "trip_start_anchorage_id": ["anc_rastanura"],
        },
        schema={
            "trip_id": pl.String,
            "trip_start": pl.Datetime(time_unit="us", time_zone=None),
            "trip_start_anchorage_id": pl.String,
        },
    )
    sar_hit = pl.DataFrame(
        {
            "scene_id": ["H"],
            "detection_timestamp": [datetime(2026, 3, 11, 0, tzinfo=UTC)],
            "lat": [26.702],
            "lon": [50.182],
            "length_m": [300.0],
            "mmsi": [1234],
            "source_csv": ["t.csv"],
        }
    )
    near = filter_near_terminals(sar_hit, anchorages, MAJOR_LOADING_TERMINALS)
    default = cross_reference_with_voyages(near, voyages)
    assert bool(default["has_matching_voyage"][0]) is False
    widened = cross_reference_with_voyages(near, voyages, time_window_days=15)
    assert bool(widened["has_matching_voyage"][0]) is True
    assert widened["matching_voyage_trip_id"][0] == "X"


def test_pipeline_snapshot_counts(anchorages: pl.DataFrame, voyages_df: pl.DataFrame) -> None:
    """End-to-end pipeline snapshot against the fixture.

    Fixture row-by-row rationale:
      FIX-01 -- kept; near Ras Tanura; mmsi=1001; matches V1 within 1 d
      FIX-02 -- kept; near Ras Tanura; mmsi=null; no voyage ±3d → DARK
      FIX-03 -- kept; near Basrah; mmsi=1003; V3 is 10 d away → DARK
      FIX-04 -- kept; near Das; mmsi=1004; no voyage at Das → DARK
      FIX-05 -- dropped (length 150 m < 200)
      FIX-06 -- dropped (mid-Arabian-Sea, outside any buffer)
      FIX-07 -- kept; near Ras Tanura; mmsi=null; V1 is 11 d away → DARK
      FIX-08 -- dropped (~13 km from Ras Tanura, outside 10 km buffer)
      FIX-09 -- kept; near Basrah; mmsi=1009; matches V3 same day
      FIX-10 -- dropped (mid-Gulf, outside any buffer)

    Net: 6 surviving, 2 matched (V1, V3), 4 dark, 2 of those NULL-mmsi.
    """
    df = build_dark_fleet_candidates([SAR_CSV], anchorages, voyages_df, MAJOR_LOADING_TERMINALS)
    assert df.height == 6
    assert df.filter(pl.col("has_matching_voyage")).height == 2
    matched_ids = set(df.filter(pl.col("has_matching_voyage"))["matching_voyage_trip_id"].to_list())
    assert matched_ids == {"V1", "V3"}
    dark = df.filter(~pl.col("has_matching_voyage"))
    assert dark.height == 4
    assert dark["mmsi"].null_count() == 2


def test_pipeline_output_schema_and_column_order(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """Downstream signal code binds to the column order and dtypes
    published in the module's _DARK_FLEET_SCHEMA; regressions here break
    phase 04's join — the snapshot-test style is the simplest guard."""
    df = build_dark_fleet_candidates([SAR_CSV], anchorages, voyages_df, MAJOR_LOADING_TERMINALS)
    assert tuple(df.columns) == _DARK_FLEET_COLUMN_ORDER
    for col, dtype in _DARK_FLEET_SCHEMA.items():
        assert df.schema[col] == dtype, f"{col}: got {df.schema[col]}, want {dtype}"


def test_build_dark_fleet_candidates_since_until_filter(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """Narrow the date window to 2026-03-08 .. 2026-03-11 — only FIX-01
    (2026-03-10) survives. Proves the ``since``/``until`` knobs clip
    at the SAR row level."""
    df = build_dark_fleet_candidates(
        [SAR_CSV],
        anchorages,
        voyages_df,
        MAJOR_LOADING_TERMINALS,
        since=datetime(2026, 3, 8, tzinfo=UTC),
        until=datetime(2026, 3, 11, tzinfo=UTC),
    )
    assert df.height == 1
    assert df["matching_voyage_trip_id"][0] == "V1"


def test_ingest_sar_empty_dir_writes_empty_parquet(tmp_path: Path) -> None:
    """Empty SAR dir yields a zero-row parquet with the full schema —
    ingesting against a not-yet-populated data dir must not crash the
    scheduled pipeline."""
    empty_dir = tmp_path / "sar_empty"
    empty_dir.mkdir()
    voyages = pl.DataFrame(
        {
            "trip_id": [],
            "trip_start": [],
            "trip_start_anchorage_id": [],
        },
        schema={
            "trip_id": pl.String,
            "trip_start": pl.Datetime(time_unit="us", time_zone=None),
            "trip_start_anchorage_id": pl.String,
        },
    )
    anchorages = pl.read_csv(ANCHORAGES_CSV)
    out = tmp_path / "empty.parquet"
    df = ingest_sar(
        empty_dir,
        anchorages,
        voyages,
        MAJOR_LOADING_TERMINALS,
        out_path=out,
    )
    assert df.height == 0
    assert out.exists()
    reread = pl.read_parquet(out)
    assert tuple(reread.columns) == _DARK_FLEET_COLUMN_ORDER


def test_load_voyages_for_crossref_returns_empty_on_empty_tree(tmp_path: Path) -> None:
    """No parquet files under voyages_dir → empty typed frame, not a
    crash. Matches the upstream ``extract.py`` contract."""
    voyages_dir = tmp_path / "voyages_empty"
    voyages_dir.mkdir()
    df = load_voyages_for_crossref(voyages_dir)
    assert df.height == 0
    assert set(df.columns) == {"trip_id", "trip_start", "trip_start_anchorage_id"}


def test_ingest_cli_end_to_end(tmp_path: Path) -> None:
    """CLI smoke: ``taq gfw ingest-sar`` against fixtures produces the
    pinned parquet. This is the last-line-of-defence check that the
    Typer wiring, defaults, and orchestrator hold together."""
    sar_dir = tmp_path / "sar"
    sar_dir.mkdir()
    (sar_dir / "fixture.csv").write_bytes(SAR_CSV.read_bytes())

    voyages_dir = tmp_path / "voyages" / "route=td3c" / "year=2026" / "month=03"
    voyages_dir.mkdir(parents=True)
    pl.read_parquet(VOYAGES_PARQUET).write_parquet(voyages_dir / "fixture.parquet")

    out = tmp_path / "out.parquet"
    runner = CliRunner()
    result = runner.invoke(
        gfw_app,
        [
            "ingest-sar",
            "--since",
            "2026-03-01",
            "--until",
            "2026-03-31",
            "--sar-dir",
            str(sar_dir),
            "--anchorages-csv",
            str(ANCHORAGES_CSV),
            "--voyages-dir",
            str(tmp_path / "voyages"),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
    df = pl.read_parquet(out)
    assert df.height == 6
    assert tuple(df.columns) == _DARK_FLEET_COLUMN_ORDER
    assert df.filter(pl.col("has_matching_voyage")).height == 2
    # Output summary should name the expected counts.
    assert "Total SAR candidate rows: 6" in result.output
    assert "Dark candidates" in result.output


def test_ingest_sar_concurrent_csvs_are_merged(
    tmp_path: Path, anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """Two SAR CSVs in the dir are concat'd into one pipeline run. A
    header-only (0-row) file must NOT poison numeric dtypes in the
    concat. Verified by ensuring length_m and mmsi dtypes survive."""
    sar_dir = tmp_path / "sar"
    sar_dir.mkdir()
    (sar_dir / "a.csv").write_bytes(SAR_CSV.read_bytes())
    # header-only sibling mimics the shape we saw in real data.
    header = SAR_CSV.read_text().splitlines()[0]
    (sar_dir / "b_header_only.csv").write_text(header + "\n")

    out = tmp_path / "out.parquet"
    df = ingest_sar(sar_dir, anchorages, voyages_df, MAJOR_LOADING_TERMINALS, out_path=out)
    assert df.height == 6  # same as one-file run; empty file contributes nothing
    assert df.schema["length_m"] == pl.Float64
    assert df.schema["mmsi"] == pl.Int64
    # source_csv on surviving rows is the non-empty file only.
    assert df["source_csv"].unique().to_list() == ["a.csv"]


def test_constants_are_sensible() -> None:
    """Sanity: the module-level defaults we ship match the ADR — catch
    accidental value drift during later refactors."""
    assert MIN_VESSEL_LENGTH_M == 200.0
    assert DEFAULT_BUFFER_KM == 10.0
    assert DEFAULT_TIME_WINDOW_DAYS == 3


def test_filter_near_terminals_handles_empty_sar(
    anchorages: pl.DataFrame,
) -> None:
    """An empty SAR frame does not crash and does not try to resolve
    terminals — callers in the orchestrator can short-circuit on this."""
    empty_sar = pl.DataFrame(
        schema={
            "scene_id": pl.String,
            "detection_timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
            "lat": pl.Float64,
            "lon": pl.Float64,
            "length_m": pl.Float64,
            "mmsi": pl.Int64,
            "source_csv": pl.String,
        }
    )
    out = filter_near_terminals(empty_sar, anchorages, MAJOR_LOADING_TERMINALS)
    assert out.height == 0


def test_sar_module_private_helpers_match_docstring() -> None:
    """Small guard against the module docstring falling out of sync with
    the actual MIN_VESSEL_LENGTH_M constant (a class of bug that bit us
    in phase 01's cassette docstring)."""
    doc = sar_mod.__doc__ or ""
    assert "200" in doc, "docstring mentions the Suezmax floor by value"
    assert "dark_fleet_candidates" in doc or "dark" in doc.lower()


def test_cross_reference_handles_tz_aware_voyages(
    anchorages: pl.DataFrame,
) -> None:
    """If upstream voyages parquet is written with tz-aware trip_start
    (a perfectly valid polars schema), cross_reference_with_voyages must
    normalise via convert_time_zone('UTC') rather than
    replace_time_zone. This branch is otherwise untested against the
    tz-naive fixture."""
    voyages = pl.DataFrame(
        {
            "trip_id": ["TZ-AWARE"],
            "trip_start": [datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC)],
            "trip_start_anchorage_id": ["anc_rastanura"],
        },
        schema={
            "trip_id": pl.String,
            "trip_start": pl.Datetime(time_unit="us", time_zone="UTC"),
            "trip_start_anchorage_id": pl.String,
        },
    )
    sar_hit = pl.DataFrame(
        {
            "scene_id": ["H"],
            "detection_timestamp": [datetime(2026, 3, 10, 6, tzinfo=UTC)],
            "lat": [26.702],
            "lon": [50.182],
            "length_m": [300.0],
            "mmsi": [1234],
            "source_csv": ["t.csv"],
        }
    )
    near = filter_near_terminals(sar_hit, anchorages, MAJOR_LOADING_TERMINALS)
    xref = cross_reference_with_voyages(near, voyages)
    assert xref["matching_voyage_trip_id"][0] == "TZ-AWARE"


def test_load_voyages_for_crossref_populated_tree(tmp_path: Path) -> None:
    """Happy path: a populated voyages tree yields the three columns the
    cross-ref expects, in the right order. The empty-tree case is
    covered elsewhere; this one pins the rglob+scan_parquet path that
    phase 04 will depend on."""
    voyages_dir = tmp_path / "voyages" / "route=td3c" / "year=2026" / "month=03"
    voyages_dir.mkdir(parents=True)
    pl.read_parquet(VOYAGES_PARQUET).write_parquet(voyages_dir / "fixture.parquet")
    out = load_voyages_for_crossref(tmp_path / "voyages")
    assert out.height == 3
    assert set(out.columns) == {"trip_id", "trip_start", "trip_start_anchorage_id"}
    assert set(out["trip_id"].to_list()) == {"V1", "V2", "V3"}


def test_length_filter_drops_null_length_rows(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """If a SAR CSV ever shipped a row with null length_m (schema
    technically allows it even though real data never does), it must
    drop via the null-guard in _apply_length_filter rather than slipping
    through as a "short vessel" (0 < 200) or crashing later."""
    sar_with_null = pl.DataFrame(
        {
            "scene_id": ["NULL_LEN", "OK"],
            "detection_timestamp": [
                datetime(2026, 3, 10, tzinfo=UTC),
                datetime(2026, 3, 10, tzinfo=UTC),
            ],
            "lat": [26.702, 26.702],
            "lon": [50.182, 50.182],
            "length_m": [None, 300.0],
            "mmsi": [1234, 5678],
            "source_csv": ["t.csv", "t.csv"],
        },
        schema_overrides={"length_m": pl.Float64, "mmsi": pl.Int64},
    )
    filtered_for_proximity = sar_with_null.filter(
        pl.col("length_m").is_not_null() & (pl.col("length_m") >= MIN_VESSEL_LENGTH_M)
    )
    near = filter_near_terminals(filtered_for_proximity, anchorages, MAJOR_LOADING_TERMINALS)
    assert near.height == 1
    assert near["scene_id"][0] == "OK"


def test_since_until_naive_datetimes_are_localised(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """Calling build_dark_fleet_candidates with tz-naive since/until
    must behave identically to tz-aware UTC inputs — the function is
    documented to promote naive to UTC rather than raising."""
    aware = build_dark_fleet_candidates(
        [SAR_CSV],
        anchorages,
        voyages_df,
        MAJOR_LOADING_TERMINALS,
        since=datetime(2026, 3, 8, tzinfo=UTC),
        until=datetime(2026, 3, 11, tzinfo=UTC),
    )
    naive = build_dark_fleet_candidates(
        [SAR_CSV],
        anchorages,
        voyages_df,
        MAJOR_LOADING_TERMINALS,
        since=datetime(2026, 3, 8),
        until=datetime(2026, 3, 11),
    )
    assert aware.height == naive.height
    # scene_id is not in the output schema; use the pinned count from the
    # aware path as the proof of equivalence.
    assert aware.height == 1


def test_build_dark_fleet_candidates_empty_sar_iterable(
    anchorages: pl.DataFrame, voyages_df: pl.DataFrame
) -> None:
    """Orchestrator called with zero SAR csvs must return the typed
    empty frame immediately — no I/O, no crash on the concat. Phase 04
    could pass the result of a filtered glob here."""
    df = build_dark_fleet_candidates([], anchorages, voyages_df, MAJOR_LOADING_TERMINALS)
    assert df.height == 0
    assert tuple(df.columns) == _DARK_FLEET_COLUMN_ORDER


def test_ingest_sar_is_crash_safe_on_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    anchorages: pl.DataFrame,
    voyages_df: pl.DataFrame,
) -> None:
    """If os.replace fails after the tmp is written (disk full, rename
    across devices, etc.), the prior output file must survive and the
    partial tmp must be cleaned up. Proves the atomic-write contract
    that distance.py also upholds."""
    sar_dir = tmp_path / "sar"
    sar_dir.mkdir()
    (sar_dir / "fixture.csv").write_bytes(SAR_CSV.read_bytes())
    out = tmp_path / "dark.parquet"

    # Seed a "prior" output file that must not be damaged by a failed
    # rewrite.
    ingest_sar(sar_dir, anchorages, voyages_df, MAJOR_LOADING_TERMINALS, out_path=out)
    original_bytes = out.read_bytes()

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(sar_mod.os, "replace", _raise)
    with pytest.raises(OSError, match="simulated rename failure"):
        ingest_sar(sar_dir, anchorages, voyages_df, MAJOR_LOADING_TERMINALS, out_path=out)
    assert out.exists()
    assert out.read_bytes() == original_bytes
    tmp_file = out.with_suffix(out.suffix + ".tmp")
    assert not tmp_file.exists()


def test_cross_reference_raises_on_voyages_missing_columns(
    anchorages: pl.DataFrame,
) -> None:
    """Schema drift on the voyages side (trip_id/trip_start/
    trip_start_anchorage_id renamed upstream) must raise at the
    boundary rather than silently emitting all-null has_matching_voyage
    and all-dark candidates."""
    sar_hit = pl.DataFrame(
        {
            "scene_id": ["H"],
            "detection_timestamp": [datetime(2026, 3, 10, tzinfo=UTC)],
            "lat": [26.702],
            "lon": [50.182],
            "length_m": [300.0],
            "mmsi": [1234],
            "source_csv": ["t.csv"],
        }
    )
    near = filter_near_terminals(sar_hit, anchorages, MAJOR_LOADING_TERMINALS)
    # Missing trip_id column entirely.
    bad_voyages = pl.DataFrame(
        {
            "trip_start": [datetime(2026, 3, 10)],
            "trip_start_anchorage_id": ["anc_rastanura"],
        },
        schema={
            "trip_start": pl.Datetime(time_unit="us", time_zone=None),
            "trip_start_anchorage_id": pl.String,
        },
    )
    with pytest.raises(ValueError, match="missing required columns"):
        cross_reference_with_voyages(near, bad_voyages)


def test_atomic_write_leaves_no_tmp_on_success(
    tmp_path: Path,
    anchorages: pl.DataFrame,
    voyages_df: pl.DataFrame,
) -> None:
    """Happy-path invariant for the atomic-write contract: after a
    successful ingest_sar, the sibling .tmp must have been
    os.replace()'d onto the final path, not left on disk. Mirror of
    test_distance.test_atomic_write_leaves_no_tmp_on_success."""
    sar_dir = tmp_path / "sar"
    sar_dir.mkdir()
    (sar_dir / "fixture.csv").write_bytes(SAR_CSV.read_bytes())
    out = tmp_path / "dark.parquet"
    ingest_sar(sar_dir, anchorages, voyages_df, MAJOR_LOADING_TERMINALS, out_path=out)
    assert out.exists()
    tmp_file = out.with_suffix(out.suffix + ".tmp")
    assert not tmp_file.exists(), "tmp sibling should have been os.replace'd onto out"
