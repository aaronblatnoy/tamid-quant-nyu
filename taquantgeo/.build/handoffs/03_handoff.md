# Phase 03 handoff

## Status
`completed`

## What shipped

- `packages/ais/src/taquantgeo_ais/gfw/sar.py` — new module (~600
  lines). Public surface:
  `load_sar_csv(path) -> pl.DataFrame` (with tz-aware parsing +
  `source_csv` attribution),
  `resolve_terminal_anchorages(anchorages, terminals)` (same-iso3
  nearest-anchorage lookup),
  `filter_near_terminals(sar_df, anchorages, terminals, *,
  buffer_km=10.0)`,
  `cross_reference_with_voyages(sar_df, voyages_df, *,
  time_window_days=3)` (picks smallest |delta| within window),
  `build_dark_fleet_candidates(...)`,
  `ingest_sar(sar_dir, anchorages, voyages_df, terminals, out_path,
  *, since, until, min_length_m, buffer_km, time_window_days)`,
  `load_voyages_for_crossref(voyages_dir)`,
  `great_circle_km(lat1, lon1, lat2, lon2)`. Module-level constants:
  `MIN_VESSEL_LENGTH_M=200.0`, `DEFAULT_BUFFER_KM=10.0`,
  `DEFAULT_TIME_WINDOW_DAYS=3`. Output schema locked in
  `_DARK_FLEET_SCHEMA` (11 columns matching the phase contract).
  Private helpers `_atomic_write_parquet`, `_apply_length_filter`,
  `_best_voyage_match`, `_finalise_output`; all documented.
- `packages/cli/src/taquantgeo_cli/gfw.py` — new `ingest-sar` Typer
  subcommand. Flags: `--since` (required), `--until`
  (inclusive end-of-day UTC), `--sar-dir`, `--anchorages-csv`,
  `--voyages-dir`, `--out`, `--buffer-km`, `--min-length-m`,
  `--time-window-days`. Prints total candidate rows, dark count,
  NULL-MMSI count, and per-anchorage breakdown on completion.
  `ingest_sar` is imported with `as ingest_sar_pipeline` to avoid
  shadowing the Typer handler name (same alias pattern as
  `classify_vessels as classify_vessels_registry`).
- `packages/ais/tests/test_sar.py` — 31 tests. Every public function
  has at least one direct test; boundary paths (empty SAR dir,
  header-only 0-row CSV, null length, tz-naive/aware voyages,
  since/until naive localisation, missing-columns ValueError, tie-
  break on smallest |delta|, time-window respect, empty
  `sar_csvs` iterable) are pinned; CLI end-to-end runs via
  `typer.testing.CliRunner`; atomic-write crash path (`os.replace`
  raises → prior file intact, tmp cleaned up) and happy-path
  (tmp absent after success) both verified.
- `packages/ais/tests/fixtures/sar_sample.csv` — hand-crafted
  10-row SAR CSV; every row's expected pipeline outcome is
  documented row-by-row in `test_pipeline_snapshot_counts`.
- `packages/ais/tests/fixtures/sar_anchorages_sample.csv` — 3-row
  anchorages fixture (SAU/IRQ/ARE) deliberately omitting KWT/IRN/QAT
  so `resolve_terminal_anchorages` exercises its "iso3 missing"
  WARN branch during tests.
- `packages/ais/tests/fixtures/sar_voyages_sample.parquet` — 3-voyage
  fixture (V1 Ras Tanura 2026-03-09 23:00, V2 Ras Tanura 2026-02-20,
  V3 Basrah 2026-03-22 18:00); matched deterministically to
  FIX-01 → V1 and FIX-09 → V3.
- `docs/adrs/0006-sar-dark-fleet-cross-ref.md` — new ADR. Full
  rationale for 200 m length floor (Suezmax-below), 10 km buffer,
  ±3 day window, same-iso3 terminal-resolution rule; known
  limitations (TD3C-only voyages yield 100% dark-rate over-count;
  SAR mid-ocean coverage gap; 3-day window is a judgment call);
  alternatives considered (Spire, category-based filter,
  per-terminal buffers, NULL-MMSI hard-exclude).
- `docs/RESEARCH_LOG.md` — new top-of-file entry: SAR schema
  (`scene_id, timestamp, lat, lon, presence_score, length_m, mmsi,
  matching_score, fishing_score, matched_category`), the
  header-only-CSV dtype-poisoning regression, the 2026-03 smoke
  counts, and the "100% dark vs TD3C-only voyages" caveat.
- `CLAUDE.md` — added `taq gfw ingest-sar` to the useful-commands
  block.
- `.build/candidate_phases.md` — added "Shared parquet atomic-write
  helper" candidate, documenting the duplication between sar.py
  and distance.py flagged by phase-03 style-review.
- `data/processed/dark_fleet_candidates.parquet` — generated artefact
  on the live 2026-03 SAR CSV (17 rows, gitignored per
  `data/processed/*`).

## PR

- URL: https://github.com/sn12-dev/taquantgeo/pull/8
- CI status at merge: **green** (label, lint-typecheck, test all
  passing)
- Merge sha: `6e7c2fd`

## Surprises / findings

**The SAR CSV schema has a zero-row-file dtype-poisoning footgun.**
`data/raw/gfw/sar_vessels/` ships `sar_vessel_detections_pipev4_202603.csv`
(107k rows, as expected) **and** `sar_vessel_detections_pipev4_20260417.csv`
— a file with just the header row and zero data. Polars cannot infer
numeric dtypes from a zero-row CSV; every column comes back as
`String`. A subsequent `pl.concat(..., how="vertical_relaxed")` then
promotes all numeric columns to `String` in the merged frame, and the
next `length_m >= 200` filter crashes with "cannot compare string with
numeric type". Fix landed: `load_sar_csv` peeks the header with
`n_rows=0`, builds a schema-overrides dict, and passes it to the real
`read_csv`. `test_load_sar_csv_handles_empty_zero_row_file` and
`test_ingest_sar_concurrent_csvs_are_merged` pin the fix.

**SAR coverage is real and plausible on the live 2026-03 data.**
107,257 global detections collapse to 17 after `length_m >= 200 AND
distance-to-nearest-PG-terminal-anchorage ≤ 10 km`. Distribution
across the 11 terminals: RAS TANURA 3, DAS ISLAND 8, KHARK 3,
AL-BASRAH OIL TERMINAL 1, FUJAIRAH 1, MINA AL AHMADI 1. Zero hits at
Ras Laffan, Juaymah, Jebel Dhanna, Jebel Ali, Assaluyeh — consistent
with the known Sentinel-1 coverage bias (some of those terminals are
SAR-shadowed by terrain or have orbital-gap coverage).

**100% dark-rate on live data — the single biggest caveat.** Every
one of the 17 live SAR candidates has `has_matching_voyage=False`.
The cause is structural, not a bug: our `data/processed/voyages/`
tree currently holds **only** TD3C-filtered voyages. A vessel that
loaded at Ras Tanura and sailed to Europe (a TD23 voyage) correctly
does not appear in our TD3C manifest but is indistinguishable here
from a dark vessel. Documented in ADR 0006 "Negative consequences"
and in the RESEARCH_LOG entry. Mitigation path: ingest a global
(not-TD3C-only) voyages tree for the cross-reference — listed in
`candidate_phases.md` under Spire Maritime. For now, the 5 NULL-MMSI
detections out of 17 are the strongest dark-fleet signal in the
output (a vessel at a loading terminal with no AIS MMSI at all).

**The 200 m length filter is doing the intended work.** Pre-filter
`length_m` distribution on the 107k global rows has p50 ~63 m, p75
~146 m, max 422 m. Post-filter distribution on the 17 PG-terminal
survivors: min 206 m, median 282 m, max 350 m — squarely in the
VLCC/Suezmax band. No bulker / containership contamination observed.

**Round 1 meta-review caught two genuine-major bugs.** Despite
shipping quality looking clean on local gates (format / lint /
types / tests), the round-1 subagents surfaced (a) non-atomic parquet
writes in `ingest_sar` that would corrupt the output on Ctrl-C, and
(b) a CLI `--until` off-by-one where `datetime.fromisoformat("2026-03-31")`
yields midnight and the `<= end` filter silently drops same-day
detections. Both were real bugs (the off-by-one would have
under-counted the March 2026 sample by whatever fraction of vessels
happen to pass through SAR on the 31st) — neither was caught by the
snapshot tests because all SAR rows in the fixture fall before the
31st. Fix for (a): extract `_atomic_write_parquet` mirroring
distance.py's pattern. Fix for (b): `--until` is now treated as
end-of-day UTC (23:59:59.999999). Documented lesson: when the CLI
date-range semantics are "inclusive end date", the parsing MUST
promote to end-of-day explicitly; trust-the-user-input is not safe
for boundary days.

**Round 2 flagged the `_atomic_write_parquet` duplication.** Two
modules now carry near-identical atomic-write helpers (sar.py and
distance.py). Not fixed in this phase — writing the extraction
correctly without breaking the existing distance.py tests is a
mechanical refactor that deserves its own scope. Candidate entry
added: "Shared parquet atomic-write helper" in
`.build/candidate_phases.md`.

**The `is True` polars-scalar identity pattern bit me one layer
deep.** Sibling test files consistently avoid `assert x is True` on
polars-indexed values because polars has historically flipped
between `numpy.bool_` and Python `bool` return types across versions.
Round-1 review flagged this and I converted to `assert bool(x) is
True`. Minor, but a pattern worth propagating to any new test file
touching polars scalars.

## Test count delta

- Before: 89
- After: 120 (delta **+31**)
- New tests (by name):
  - `test_load_sar_csv_parses_schema_and_timestamps`
  - `test_load_sar_csv_handles_empty_zero_row_file`
  - `test_load_sar_csv_missing_required_columns_raises`
  - `test_great_circle_km_sanity`
  - `test_resolve_terminal_anchorages_same_iso3_only`
  - `test_filter_near_terminals_includes_within_buffer`
  - `test_filter_near_terminals_excludes_outside_buffer`
  - `test_filter_near_terminals_custom_buffer`
  - `test_length_heuristic_excludes_small_vessels`
  - `test_cross_reference_matches_within_time_window`
  - `test_cross_reference_flags_no_match_as_dark_candidate`
  - `test_cross_reference_picks_smallest_time_delta_on_multiple_matches`
  - `test_cross_reference_respects_time_window_days`
  - `test_pipeline_snapshot_counts`
  - `test_pipeline_output_schema_and_column_order`
  - `test_build_dark_fleet_candidates_since_until_filter`
  - `test_ingest_sar_empty_dir_writes_empty_parquet`
  - `test_load_voyages_for_crossref_returns_empty_on_empty_tree`
  - `test_ingest_cli_end_to_end`
  - `test_ingest_sar_concurrent_csvs_are_merged`
  - `test_constants_are_sensible`
  - `test_filter_near_terminals_handles_empty_sar`
  - `test_sar_module_private_helpers_match_docstring`
  - `test_cross_reference_handles_tz_aware_voyages`
  - `test_load_voyages_for_crossref_populated_tree`
  - `test_length_filter_drops_null_length_rows`
  - `test_since_until_naive_datetimes_are_localised`
  - `test_build_dark_fleet_candidates_empty_sar_iterable`
  - `test_ingest_sar_is_crash_safe_on_write_failure`
  - `test_cross_reference_raises_on_voyages_missing_columns`
  - `test_atomic_write_leaves_no_tmp_on_success`
- Tests removed: none.

Phase contract required ≥6 new tests. Delivered **+31**. Driver
should update `build_state.json.test_count_baseline` from 89 → 120.

## Optional services not configured

None — this phase uses only on-disk SAR CSVs already present under
`data/raw/gfw/sar_vessels/`. No env vars required. No network calls.

## Deferred / open questions

- **TD3C-only voyages produce a 100% dark-rate artefact.** As noted
  in "Surprises", the cross-reference today compares SAR hits at
  PG loading terminals against a voyages tree filtered to
  TD3C-destined voyages. A ballast VLCC at Ras Tanura loading for
  Europe is correctly absent from our TD3C manifest but is
  incorrectly flagged "dark". Proper mitigation needs a global
  voyages source (Spire academic tier pending, tracked in
  `candidate_phases.md`); in the interim the NULL-MMSI subset
  (~30% of the 17-candidate live sample) is a sharper dark signal.
- **The 3-day match window is a judgment call.** Worldscale lifts
  span 24-48 h + Sentinel-1 overpass cadence is ~2-3 days, so 3
  days is defensible, but we have no labelled data to optimise
  against. A later tuning phase once sanctions-list ground truth
  is available should sweep this.
- **Length filter is VLCC/Suezmax-centric.** The 200 m cutoff
  excludes MR/LR1 tankers; correct for TD3C but would break a
  future product-tanker signal. Not generalisable as-is.
- **`_atomic_write_parquet` duplication between sar.py and
  distance.py.** Candidate phase proposed. Not blocking for v0
  but worth extracting before a third module needs the same
  pattern (likely: phase 04 tightness-signal persistence or phase
  08 backtester results).
- **Tie-break on exactly-equal |time deltas| between two voyages
  at the same anchorage is not pinned.** Round-1 test-review
  flagged this — a genuine tie is astronomically unlikely at
  microsecond precision, but if ever encountered the outcome
  depends on `voyages_df` row order (first-seen wins). Documented
  in `_best_voyage_match`'s docstring; not worth special-casing.
- **Schema / dtype-drift defenses** — the module raises on missing
  required columns (SAR and voyages both) but not on unexpected
  extra columns or value-range anomalies (e.g. length_m > 500 m
  would be a SAR-anomaly worth flagging). Future data-quality
  phase (10) can add this.

## Ideas for future phases

Appended to `.build/candidate_phases.md`:

- **Shared parquet atomic-write helper.** Extract `_atomic_write_parquet`
  to a single location (likely `taquantgeo_ais/io.py` or
  `taquantgeo_core/io.py`) and replace the duplicated copies in
  `sar.py` and `distance.py`. Also collapses the two parallel
  atomic-write test bodies. Estimated effort: `standard`.

Two informal ideas surfaced during review but not promoted:

- Per-terminal buffer tuning. The current 10 km buffer is global;
  some terminals (e.g. Ras Tanura approach) have larger holding
  areas and a wider buffer might improve recall. Requires labeled
  SAR vs voyage data; defer until we have that.
- `matching_score` / `presence_score` weighting. SAR scores are
  currently ignored. A probabilistic weighted dark-candidate count
  (instead of 0/1) could feed phase 04 better. Deferred until we
  have downstream IC evidence that the binary count is limiting.

## For the next phase

- **Canonical output path**: `data/processed/dark_fleet_candidates.parquet`.
  Column order locked at `_DARK_FLEET_COLUMN_ORDER` in
  `packages/ais/src/taquantgeo_ais/gfw/sar.py`. Phase 04's
  `compute_daily_tightness` should filter on
  `has_matching_voyage == False` and compute
  `dark_fleet_supply_adjustment(as_of) = |{ d : d.detection_timestamp
  in [as_of - 7d, as_of] AND d.nearest_anchorage is a PG terminal }|`.
- **Over-counting warning.** Because our voyages tree is TD3C-only,
  phase 04's dark-fleet adjustment is structurally over-counted by
  the count of non-TD3C PG loadings. Mitigations phase 04 may want
  to consider:
  - Report two tightness ratios side-by-side: with and without
    the dark adjustment, so the operator can judge signal quality.
  - Use only the NULL-MMSI subset
    (`df.filter(pl.col("mmsi").is_null())`) as the "strong dark"
    signal and the rest as weak-dark (reported but not adjusting).
  - Consider a per-terminal cap on the dark adjustment (e.g. no
    more than 2× the TD3C-declared loading count per terminal).
- **The output includes the matched rows**, not just the dark ones.
  Phase 04 is expected to filter; the matched rows are there for
  audit (confirming the cross-reference is finding real matches)
  and for dark-rate = dark / total computation.
- **`load_voyages_for_crossref(voyages_dir)`** returns the
  three cross-ref columns only (`trip_id, trip_start,
  trip_start_anchorage_id`). When phase 04 needs more columns for
  its own math, it should read the voyage parquet directly (not
  re-extend `load_voyages_for_crossref` — keeping its contract
  narrow makes the cross-reference easier to reason about).
- **Atomic-write contract.** `ingest_sar` writes via
  `_atomic_write_parquet` (tmp + `os.replace`). Any downstream code
  that writes the same path must also be atomic or risk corrupting
  the cache between runs.
- **Idempotency**. Re-running `ingest_sar` with the same inputs
  produces the same output (to within `computed_at`-free rows —
  no wall-clock column in this parquet, by design). Safe to
  re-run; safe to kill mid-run.
- **`GfwClient` is not used.** This phase touched no GFW REST
  endpoints. All data is from on-disk CSVs shipped via the bulk
  research-token download.
