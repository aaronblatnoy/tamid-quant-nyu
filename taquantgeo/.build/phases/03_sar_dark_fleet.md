# Phase 03 — SAR vessel detections + dark-fleet cross-ref

## Metadata
- Effort: `max`
- Depends on phases: 01
- Applies security-review: `no`
- Max phase runtime (minutes): 150
- External services: none (uses local SAR CSVs already in
  `data/raw/gfw/sar_vessels/`)

## Mission
ADR 0002 Gap 2: GFW's C4 voyages systematically exclude dark-fleet VLCCs
(AIS off during loading or transit). GFW publishes Sentinel-1 SAR vessel
detections that see vessels via satellite radar independent of AIS. This
phase joins SAR detections spatially to named anchorages near known VLCC
loading terminals, temporally to our AIS-reported voyages, and writes a
`dark_fleet_candidates` parquet: every SAR hit at a loading anchorage that
has NO corresponding AIS-reported voyage in the matching time window. The
output is a dark-fleet supply proxy that phase 04 can subtract from the
declared-ballast supply count to adjust the tightness ratio.

## Orientation
- `.build/handoffs/00_handoff.md`, `01_handoff.md`, `02_handoff.md`
- `packages/ais/src/taquantgeo_ais/gfw/routes.py` —
  `MAJOR_LOADING_TERMINALS` list
- `packages/ais/src/taquantgeo_ais/gfw/anchorages.py` — anchorage
  DataFrame shape (`s2id`, `lat`, `lon`, `iso3`, `label`,
  `drift_radius`, `distance_from_shore_m`)
- `docs/adrs/0002-gfw-voyages-as-historical-source.md` Gap 2
- Data directory: `data/raw/gfw/sar_vessels/*.csv` (3 monthly snapshots
  currently on disk per research log: 202510, 202511, 202512). Inspect
  one with polars to confirm schema before coding.

## Service preflight
None.

## Acceptance criteria
- `packages/ais/src/taquantgeo_ais/gfw/sar.py` exists with:
  - `load_sar_csv(path: Path) -> polars.DataFrame`
  - `filter_near_terminals(sar_df, anchorages, terminals, *, buffer_km:
     float = 10.0) -> polars.DataFrame`
  - `cross_reference_with_voyages(sar_df, voyages_df, *, time_window_days:
     int = 3) -> polars.DataFrame` — returns SAR hits with a
    `has_matching_voyage` bool column
  - Module docstring documents the drift_radius interpretation, the
    buffer math, and the cross-reference heuristic
- CLI: `taq gfw ingest-sar --since YYYY-MM-DD [--until YYYY-MM-DD]
  [--sar-dir data/raw/gfw/sar_vessels] [--anchorages-csv ...]
  [--voyages-dir ...]` exits 0 on a valid month of SAR data.
- Output parquet: `data/processed/dark_fleet_candidates.parquet`, schema:
  `mmsi` (int64 nullable — visible only if the detection has an MMSI
  attached; often NULL for dark fleet),
  `detection_timestamp` (timestamp[us, UTC]),
  `lat` (float64), `lon` (float64),
  `length_m` (float64 nullable — from SAR measurement),
  `nearest_anchorage_id` (str),
  `nearest_anchorage_label` (str),
  `distance_to_anchorage_km` (float64),
  `has_matching_voyage` (bool),
  `matching_voyage_trip_id` (str nullable),
  `source_csv` (str — filename).
- Tests:
  - `test_filter_near_terminals_includes_within_buffer`
  - `test_filter_near_terminals_excludes_outside_buffer`
  - `test_cross_reference_matches_within_time_window`
  - `test_cross_reference_flags_no_match_as_dark_candidate`
  - `test_length_heuristic_excludes_small_vessels` — phase filters SAR
    detections whose measured `length_m < 200` (smaller than a Suezmax)
    to reduce false positives from containerships etc.; test confirms
    exclusion
  - `test_ingest_cli_end_to_end` — smoke test against a 10-row fixture SAR
    CSV, asserts parquet row count and required columns present
- Snapshot: use a tiny hand-crafted SAR CSV fixture where the expected
  dark-fleet-candidate count is pinned. Document in the test docstring
  why the expected count is what it is.

## File plan
- `packages/ais/src/taquantgeo_ais/gfw/sar.py` — new
- `packages/cli/src/taquantgeo_cli/gfw.py` — add `ingest-sar` subcommand
- `packages/ais/tests/test_sar.py` — new
- `packages/ais/tests/fixtures/sar_sample.csv` — 10-row fixture; build
  from the actual SAR schema discovered during orientation
- `packages/ais/tests/fixtures/sar_voyages_sample.parquet` — matching
  voyage fixture (3 voyages, one overlapping with a SAR detection)
- `docs/adrs/0006-sar-dark-fleet-cross-ref.md` — NEW ADR: buffer distance
  choice, time window choice, false-positive management (length filter),
  known limitations (mid-ocean SAR coverage gaps)
- `docs/RESEARCH_LOG.md` — append entry with schema discoveries,
  false-positive rate observed on first live run
- `CLAUDE.md` — useful commands: add `taq gfw ingest-sar ...`

## Non-goals
- Building a real dark-fleet model (classifying vessel identity /
  ownership sanctions status). That's beyond v0 — candidate entry.
- Using SAR infrastructure (fixed structures) — only vessel detections
  matter here. Infrastructure CSV is separate data.
- Correcting SAR coverage gaps in the mid-Indian-Ocean — coverage is
  skewed but we note, don't correct. Future ADR if meaningful.
- Filing sanctions reports / integrating with external watchlists.

## Quality gates
- Format + lint + typecheck clean
- ≥6 new tests
- Pre-commit meta-review full loop (scope: multi-file, >20 lines)
- Three-round review loop per `Effort: max`
- ADR 0006 written

## Git workflow
1. Branch `feat/phase-03-sar-dark-fleet`
2. Commits:
   - `feat(gfw): SAR vessel-detection loader + terminal-proximity filter`
   - `feat(gfw): SAR × voyages cross-reference for dark-fleet candidates`
   - `test(gfw): SAR cassette fixtures + snapshot counts`
   - `docs: ADR 0006 SAR dark-fleet cross-reference`
3. PR, CI green, squash-merge

## Handoff
Total SAR rows ingested, fraction flagged as dark candidates on the
full real SAR data on disk (not just fixtures — run against
`data/raw/gfw/sar_vessels/*.csv` at least once during the phase and
report the count), distribution of `distance_to_anchorage_km`, false-
positive intuitions. Record findings in RESEARCH_LOG too.
