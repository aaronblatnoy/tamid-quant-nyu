# Phase 05 — Signal snapshot tests

## Metadata
- Effort: `standard`
- Depends on phases: 04
- Applies security-review: `no`
- Max phase runtime (minutes): 60
- External services: none

## Mission
Phase 04's unit tests verify the signal math *per component*. This phase
locks in *end-to-end output* against frozen inputs: a specific month of
real voyages + registry + distances + dark-fleet produces specific
tightness numbers for specific dates. If a later PR changes the math
(even inadvertently — a type coercion, a groupby key, a rounding mode),
these tests fail and force the author to either update the pinned numbers
(with ADR-worthy justification) or revert. Without this layer, a signal
drift can merge silently and corrupt a month of backtesting before anyone
notices.

## Orientation
- `.build/handoffs/04_handoff.md`
- `packages/signals/src/taquantgeo_signals/tightness.py`
- `packages/ais/tests/test_classifier.py` — cassette fixture pattern
- `docs/adrs/0007-tightness-signal-definition.md`

## Service preflight
None (runs entirely on fixtures checked into the repo).

## Acceptance criteria
- `packages/signals/tests/test_tightness_snapshot.py` exists with at least the following six named tests (each loads the frozen fixture set and calls `compute_daily_tightness(as_of=<pinned date>)`; each assertion uses `pytest.approx(expected, rel=1e-3)`):
  - `test_snapshot_busy_loading_day_matches_frozen_ratio` — a date with many concurrent laden departures; pins the ratio, forward_demand_ton_miles, forward_supply_count.
  - `test_snapshot_quiet_day_matches_frozen_ratio` — a low-activity date; pins that the signal behaves on thin data and does not spike spuriously.
  - `test_snapshot_dark_fleet_adjustment_lowers_supply` — a date with ≥1 dark-fleet candidate in the prior 7 days; pins `dark_fleet_supply_adjustment` and the reduced `effective_supply`.
  - `test_snapshot_ratio_infinity_when_effective_supply_zero` — a crafted fixture-day where forward_supply is 0 after adjustment; pins that the floor clamps to 1 and `components["supply_floor_clamped"] == true`.
  - `test_snapshot_zscore_none_when_history_insufficient` — a date within the first 30 days of the fixture window; pins `z_score_90d is None`.
  - `test_snapshot_regression_for_march_2020_covid_window` — the regression test already required by the phase; pins that the signal's March 2020 values do not regress after a signal-math change lands.
- Test-module docstring documents derivation of every pinned value —
  i.e. for date D we expected ratio R because there were N laden voyages
  carrying T ton-miles remaining against M ballast arrivals, adjusted by
  K dark candidates. A reader should be able to re-derive the pinned
  numbers by hand from the fixtures.
- At least one snapshot test is a **regression**: covers a failure mode
  phase 04's unit tests didn't (e.g., an edge where `ratio` goes to
  `inf` because supply is 0 for a specific quiet day; or where z_score
  is None because history is insufficient).
- Fixtures are minimal — ≤100 voyages per file; committed under
  `packages/signals/tests/fixtures/snapshot/`. No live data dumps.
- All quality gates green.

## File plan
- `packages/signals/tests/test_tightness_snapshot.py` — new
- `packages/signals/tests/fixtures/snapshot/voyages.parquet` —
  deterministic fixture
- `packages/signals/tests/fixtures/snapshot/vessel_registry.parquet`
- `packages/signals/tests/fixtures/snapshot/distance_cache.parquet`
- `packages/signals/tests/fixtures/snapshot/dark_fleet.parquet`
- `packages/signals/tests/fixtures/snapshot/README.md` — documents
  *how* each fixture was generated (seed row IDs or deterministic
  sampling code) so fixtures can be regenerated after an intentional
  signal-math change
- `docs/RESEARCH_LOG.md` — append note on the first snapshot values
  computed

## Non-goals
- Regression tests against live historical data — these are pinned to
  fixtures, not to last-year's real data, to keep tests deterministic
  and fast. A different phase may add a monthly full-history regression.
- Multi-route snapshots — td3c only here.
- IC testing — phase 07.

## Quality gates
- Format + lint + typecheck clean
- ≥5 new tests, all green
- Test count increases by ≥5
- Pre-commit meta-review full loop since >1 file + test fixtures
- Single-round review is fine for `Effort: standard`
- Fixtures verified reproducible — the snapshot/README.md regeneration
  instructions must actually regenerate identical files

## Git workflow
1. Branch `test/phase-05-signal-snapshots`
2. Commits:
   - `test(signals): snapshot suite against frozen fixtures`
   - `docs: research log note on signal snapshot values`
3. PR, CI green, squash-merge

## Handoff
Pinned values and the fixtures they were derived from. If any snapshot
test discovered a bug in phase 04's math, note it loudly — phase 04 was
supposed to have caught it with unit tests, so there's a lesson.
