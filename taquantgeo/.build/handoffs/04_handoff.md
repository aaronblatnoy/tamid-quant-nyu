# Phase 04 handoff

## Status
`completed`

## What shipped

- `packages/signals/src/taquantgeo_signals/tightness.py` — new module
  (~420 lines). Public surface:
  `@dataclass(frozen=True) class TightnessSnapshot`,
  `compute_daily_tightness(as_of, *, voyages_df, vessel_registry_df,
  distance_cache_df, dark_fleet_df, ballast_voyages_df=None,
  prior_snapshots_df=None, route="td3c", supply_horizon_days=15,
  lookback_days=90) -> TightnessSnapshot`. Module constants:
  `ROUTE_NOMINAL_DWT=270_000`, `BALLAST_NOMINAL_SOG_KNOTS=13.0`,
  `DEFAULT_SUPPLY_HORIZON_DAYS=15`, `DARK_FLEET_WINDOW_DAYS=7`,
  `DEFAULT_LOOKBACK_DAYS=90`, `MIN_Z_SCORE_SAMPLE=30`. Private
  helpers: `_vlcc_mmsi_set`, `_cargo_tons_lookup`,
  `_build_distance_lookup`, `_distance_for_pair`,
  `_normalise_datetime_col`, `_as_of_to_ts_utc`, `_ensure_utc`,
  `_compute_forward_demand`, `_compute_forward_supply`,
  `_compute_dark_fleet_adjustment`, `_compute_z_score`. Module
  docstring is the executable summary of ADR 0007; any change to
  the math requires a new ADR.
- `packages/signals/src/taquantgeo_signals/models.py` — new
  SQLAlchemy `Signal` model registered on
  `taquantgeo_core.schemas.Base`. Columns: `id` bigserial (INTEGER
  on sqlite via `with_variant`), `as_of` date, `route` text,
  `forward_demand_ton_miles` bigint, `forward_supply_count` int,
  `dark_fleet_supply_adjustment` int, `tightness` double,
  `tightness_z` double nullable, `components` JSON/JSONB,
  `created_at` timestamptz. Indexes:
  `ix_signals_as_of`, `ix_signals_route`, `ix_signals_route_as_of`,
  and `ix_signals_as_of_route_uq` (unique index targeted by ON
  CONFLICT).
- `packages/signals/src/taquantgeo_signals/persistence.py` — new.
  `upsert_snapshot(session, snapshot) -> None` with two branches:
  Postgres via `ON CONFLICT DO UPDATE` (atomic, race-safe);
  sqlite/other dialects via delete-then-add inside the caller's
  transaction. Caller commits.
- `packages/signals/src/taquantgeo_signals/__init__.py` — exports
  `compute_daily_tightness`, `TightnessSnapshot`, `upsert_snapshot`,
  all math-knob constants.
- `infra/alembic/versions/0002_signals_table.py` — new migration.
  Creates `signals` exactly matching `Signal.metadata`.
  `uv run alembic upgrade head` is clean against the local
  docker-compose Postgres.
- `packages/jobs/src/taquantgeo_jobs/daily_signal.py` — new.
  `run_once(as_of: date | None = None, *, route="td3c",
  voyages_dir=..., registry_path=..., distance_cache_path=...,
  dark_fleet_path=..., ballast_voyages_dir=..., persist=False)`.
  Reads the canonical `data/processed/` artefacts, invokes
  `compute_daily_tightness`, optionally upserts to Postgres. Prior
  snapshots for the z-score are fetched from the DB via
  `_load_prior_snapshots_df(route)`; `sqlalchemy.exc.SQLAlchemyError`
  downgrades to `None` so the job does not crash on transient DB
  issues. Route partition filtered by subdir prefix
  (`voyages_dir / f"route={route}"`) — not substring — so
  `route=td3c` cannot accidentally capture `route=td3c_ballast`.
- `packages/jobs/src/taquantgeo_jobs/__init__.py` — re-exports
  `run_daily_signal`.
- `packages/cli/src/taquantgeo_cli/signals.py` — new. `taq signals
  compute-tightness` subcommand. Flags: `--as-of`, `--route`,
  `--voyages-dir`, `--registry-path`, `--distance-cache-path`,
  `--dark-fleet-path`, `--ballast-voyages-dir`,
  `--persist/--no-persist`. Prints the snapshot + components as
  pretty JSON.
- `packages/cli/src/taquantgeo_cli/main.py` — registers
  `signals_app`.
- `packages/cli/pyproject.toml` — adds `taquantgeo-signals`,
  `taquantgeo-jobs` deps.
- `packages/signals/pyproject.toml` / `packages/jobs/pyproject.toml`
  — real dependencies populated (polars, pyarrow, sqlalchemy,
  psycopg, taquantgeo-core, taquantgeo-ais, taquantgeo-signals).
- `packages/signals/tests/conftest.py` — 6 pytest fixtures for
  voyages / ballast / registry / registry-with-dwt / distance
  cache / dark fleet. Pure-in-memory DataFrames; fast.
- `packages/signals/tests/test_tightness.py` — 25 tests pinning
  every equation, every fallback counter, and every boundary
  (tz-naive, empty frames, null columns, dwt=0 sentinel,
  has_matching_voyage=None policy, distance-cache column-guard,
  strict lookahead exclusion).
- `packages/signals/tests/test_persistence.py` — 6 tests (4 unit,
  2 integration). The Postgres ON CONFLICT integration test is the
  one that actually pins the production upsert path.
- `packages/jobs/tests/test_daily_signal.py` — 6 tests (5 unit,
  1 integration). End-to-end `run_once` against a tmp-dir fixture
  tree with both `route=td3c` and `route=td3c_ballast` partitions
  so the ballast-from-disk path is exercised. Persist-path
  integration test writes, reads back, asserts, and cleans up.
- `docs/adrs/0007-tightness-signal-definition.md` — new ADR pinning
  the signal math in full: every term, every unit, the supply
  floor, the z-score lookahead rule, the storage contract, and
  the alternatives considered. The binding document.
- `docs/RESEARCH_LOG.md` — new top-of-file entry: first live
  snapshot numbers (17 in-progress laden TD3C voyages, median 5,894
  NM, forward demand 26.47 B ton-miles, 4 dark-fleet candidates,
  supply floor clamped), fallbacks seen, and the two biggest
  caveats (ballast supply is zero pre-phase-05; every voyage hits
  the 270 k dwt nominal because GFW identity rarely fills dwt).
- `docs/data_model.md` — `signals` section updated to the shipped
  shape (new `dark_fleet_supply_adjustment` and `components jsonb`,
  explicit index names).
- `CLAUDE.md` — adds two `taq signals compute-tightness` example
  invocations to the useful-commands block.
- `uv.lock` — regenerated for the new workspace deps.

## PR

- URL: https://github.com/sn12-dev/taquantgeo/pull/9
- CI status at merge: **green** (label, lint-typecheck, test all
  passing; first run through)
- Merge sha: `a8519d4`

## Surprises / findings

**The supply side is structurally zero pre-phase-05.** The first
live snapshot on 2026-03-15 shows `forward_supply_count = 0` with
`ballast_in_progress = 0`. Root cause: phase 01's
`ingest-voyages` was only run for `route=td3c` (laden PG→China),
not for `route=td3c_ballast`. The on-disk voyages tree has no
ballast partition, so the job's `_load_voyages(..., "td3c_ballast")`
returns empty. Everything downstream works — the supply floor
clamps to 1, `supply_floor_clamped=1` fires in components, and
the signal emits a finite (if denominator-dominated) ratio. But
the ratio is not trading-quality until phase 05+ replaces the GFW
ballast proxy with live-AIS ballast detection. Documented in the
RESEARCH_LOG entry and in the ADR's "Negative consequences" section
so it does not surprise the next phase.

**dwt is absent for every VLCC in the registry today.** 17/17
in-progress laden voyages on 2026-03-15 hit the
`ROUTE_NOMINAL_DWT=270_000` fallback. Root cause: phase 01's
classifier rarely gets `tonnageGt` or `dwt` from GFW's identity
endpoint. The phase-04 code handles this cleanly — the fallback
fires without drama — but the aggregate ton-miles is ~10 %
sensitive to this per-vessel nominal. Phase 10 (IMO/registry
enrichment in `candidate_phases.md`) is the right place to close
this.

**The 100 %-dark-fleet artefact from phase 03 now feeds into the
ratio unchanged.** 4 of the 17 phase-03 dark candidates land in
the 7-day window before 2026-03-15 → supply adjustment = 4. With
ballast in progress = 0, effective supply raw = 0 − 4 = −4 →
floor to 1 → `supply_floor_clamped = 1`. This is actually the
right behaviour for v0 (the math is doing what the ADR says), but
IC / backtest phases MUST filter on
`components["supply_floor_clamped"] == 0` before regressing
against equity returns. The flag is the load-bearing contract
between the signal and the IC code.

**`Effort: max` 3-round meta-review found real issues in round
1.** Round 1 surfaced 1 genuine-major bug (asymmetric VLCC filter
between demand and supply — empty registry silently admitted all
ballast voyages instead of returning zero supply) plus 2 more
major style issues (index names `ix_signals_route_as_of_desc`
implying DESC when they were ASC, and `uq_` prefix used for a
unique index rather than a UniqueConstraint, against the
0001 convention). Round 1 also flagged a dead
`session_ctx_factory` parameter the docstring promised but the
body ignored. Round 2 surfaced 3 coverage-gap majors (no test
for the new `dwt <= 0` sentinel branch, no test for the
`fill_null(True)` dark-fleet policy, no ballast-from-disk test
path), plus several minor polish items. Round 3 was empty across
all three reviewers — the loop converged. Without the `max`
3-round commitment, the empty-registry supply bug would have
shipped, and the `dwt=0` branch would have been untested.

**`from __future__ import annotations` + SQLAlchemy Mapped
resolution.** The `Signal` model must NOT use the `__future__`
import (per the project convention in `CLAUDE.md`) because
SQLAlchemy evaluates `Mapped[...]` at class-definition time. Got
this right first try by following `schemas.py`'s lead, but worth
flagging — a future contributor adding a model is one `from
__future__ import annotations` away from a confusing runtime
error.

**SQLite primary-key gotcha.** `BigInteger` on sqlite does NOT
auto-increment (sqlite only treats `INTEGER` primary keys as
rowid aliases). The sqlite-backed persistence tests failed with
"NOT NULL constraint failed: signals.id" until the model was
changed to `BigInteger().with_variant(Integer, "sqlite")`. The
existing `Vessel` model has the same `BigInteger` primary key
but its tests are integration-marked (Postgres-only), so this
bug had never fired. Documented in models.py; any future model
that wants a sqlite-compatible test path needs the same variant.

**searoute-cache column-guard missing.** Round 1 flagged that if
the distance cache parquet was present but malformed (missing
`origin_s2id` / `dest_s2id` / `nautical_miles`), the signal
computation would KeyError instead of falling back. Fixed via
the extracted `_build_distance_lookup` helper which returns `{}`
on missing required columns. Voyages-frame side already had a
guard; now the distance-cache side matches.

**CLI `Path` + typer + ruff TC003 conflict.** Typer resolves
annotations at runtime to build CLI flag types. With `from
__future__ import annotations` the `Path` import is annotation-only,
which trips ruff's TC003. The sibling `gfw.py` doesn't trip it
because it uses `Path(...)` at runtime as a default value. In
`signals.py` the defaults are `DEFAULT_*` constants imported from
`daily_signal.py`, so `Path` is annotation-only. Landed a
targeted `# noqa: TC003` with a one-line rationale rather than
refactoring the defaults, since the noqa is the more local fix.

## Test count delta

- Before: 120
- After: 161 (delta **+41**)
- New tests (by name):
  - `test_forward_demand_sums_ton_miles_remaining`
  - `test_forward_supply_counts_ballast_arrivals_in_15_day_window`
  - `test_supply_horizon_respected`
  - `test_dark_fleet_adjustment_reduces_supply`
  - `test_ratio_is_demand_over_effective_supply`
  - `test_ratio_handles_zero_supply_returns_inf_not_nan`
  - `test_z_score_90d_uses_rolling_history`
  - `test_z_score_none_when_insufficient_history`
  - `test_z_score_strictly_lookahead_free`
  - `test_z_score_none_when_zero_variance`
  - `test_components_dict_exposes_raw_terms`
  - `test_non_vlcc_voyages_excluded`
  - `test_dwt_present_uses_per_vessel_dwt`
  - `test_dwt_zero_is_treated_as_fallback`
  - `test_dark_fleet_has_matching_voyage_null_is_treated_as_matched`
  - `test_distance_cache_missing_columns_falls_back`
  - `test_trip_end_null_is_in_progress`
  - `test_deterministic_for_fixed_inputs`
  - `test_tz_naive_voyage_timestamps_accepted`
  - `test_ballast_fallback_sog_counter_fires`
  - `test_default_ballast_supply_is_zero`
  - `test_empty_inputs_produce_valid_snapshot`
  - `test_snapshot_is_frozen`
  - `test_dark_fleet_window_constant_is_seven`
  - `test_supply_horizon_constant_is_fifteen`
  - `test_min_z_score_sample_is_thirty`
  - `test_route_nominal_dwt_is_two_seventy_thousand`
  - `test_ballast_nominal_sog_is_thirteen`
  - `test_tz_aware_dark_fleet_utc_is_accepted`
  - `test_upsert_snapshot_inserts_row`
  - `test_upsert_snapshot_idempotent`
  - `test_upsert_snapshot_different_routes_are_distinct`
  - `test_signals_table_has_expected_columns`
  - `test_alembic_upgrade_head_creates_signals_table` (integration)
  - `test_upsert_snapshot_postgres_on_conflict_is_idempotent`
    (integration)
  - `test_run_once_reads_canonical_layout`
  - `test_run_once_defaults_to_today_utc`
  - `test_run_once_tolerates_missing_parquets`
  - `test_cli_compute_tightness_end_to_end`
  - `test_cli_help_lists_signals`
  - `test_run_once_persist_writes_to_postgres` (integration)
- Tests removed: none.

Phase contract required ≥10 new tests. Delivered **+41** (31
non-integration in `packages/signals`, 6 non-integration in
`packages/jobs`, 3 integration-marked, +1 renamed/redesigned
`test_ratio_handles_zero_supply_returns_inf_not_nan`). Driver
should update `build_state.json.test_count_baseline` from 120 →
161.

## Optional services not configured

None. The phase ships everything against local Postgres
(required service) and on-disk parquet. No cloud R2, no Neon, no
external APIs, no IBKR. Phase 11 (APScheduler) and phase 06/07
(equity prices) are the next places optional-service handling
will matter.

## Deferred / open questions

- **Ballast supply via GFW `td3c_ballast` voyages is a weak
  proxy.** Phase 05+ replaces it with live-AIS ballast detection.
  Deliberately scoped out here; the plumbing is in place
  (`ballast_voyages_df` argument) so phase 05 is a data-source
  swap, not a math change.
- **`cargo_tons` nominal for every vessel.** GFW identity almost
  never fills `dwt`. Phase 10 (IMO / registry enrichment in
  `candidate_phases.md`) can close this at per-vessel ± 10 %
  precision. Until then, ton-miles carry a uniform multiplicative
  error and the **relative** time-series signal is still useful
  for IC work.
- **Dark-fleet 7-day window is unoptimised.** No labelled
  sanctions data to sweep against. Sensitivity analysis deferred
  to a later phase; the constant is a module-scope `Final` so the
  sweep is one-line.
- **No live-AIS SOG, so every ballast voyage uses the 13-knot
  nominal.** Phase 05 fixes. `components["avg_sog_fallback_used"]`
  will flip once live AIS is wired in; no code change in this
  module.
- **Single-route signal.** Multi-route support is `route` column
  in `signals`, but only `td3c` is computed today. A second route
  is a new entry to `routes.py` + a second job schedule — no
  signal-math change.
- **`_compute_forward_supply` does not filter by `route` column
  itself** (relies on caller pre-scoping via
  `_load_voyages(..., "td3c_ballast")`). Round-2 style review
  flagged this as a latent footgun; kept as-is for v0 because the
  only caller is the job layer which does scope correctly.
  Documenting here so a future refactor adds an explicit `route=`
  parameter.
- **Z-score is only computed when `persist=True`** (because that's
  when the job reads prior snapshots from the DB). Dry runs
  always emit `z_score_90d=None`. Intentional — keeps the
  `--no-persist` path DB-free — but an operator eyeballing a
  one-shot snapshot cannot sanity-check the z dimensionally.
  Phase 07 IC work will surface this; trivial to change.

## Ideas for future phases

Nothing appended to `candidate_phases.md` this run. The `Shared
parquet atomic-write helper` candidate that phase 03 added
remains open but is not blocking phase 04 (this phase's Postgres
upsert is atomic by construction via ON CONFLICT; the parquet
writes are read-only from phase 01-03 outputs).

Two informal ideas raised during round-2 review but not promoted:

- **Decouple z-score prior-history load from the `persist` flag.**
  Currently `prior_df` is None when persist=False, so --no-persist
  always returns `z_score_90d=None`. A more natural split: the
  job always reads prior snapshots (DB-safe with the
  SQLAlchemyError catch) and only the upsert itself is gated on
  `persist`. Not blocking v0.
- **Signal versioning.** If we ever change the equation (requiring
  a new ADR), we need a way to say "re-run the whole history
  under the new definition" without clobbering old snapshots. A
  `signal_version` column in `signals` + upsert key
  `(as_of, route, signal_version)` would do it. Not needed until
  the first ADR 0007 successor.

## For the next phase

- **Canonical paths** (stable since phase 01-03):
  - `data/processed/voyages/route=<route>/year=YYYY/month=MM/*.parquet`
  - `data/processed/vessel_registry.parquet`
  - `data/processed/distance_cache.parquet`
  - `data/processed/dark_fleet_candidates.parquet`
- **Snapshot contract.** `TightnessSnapshot` is frozen; dataclass
  fields are part of the API. Adding a field = minor version
  bump; removing / renaming one = new ADR. The `components` dict
  is the escape hatch for new audit / diagnostic data — it is
  Mapped[dict[str, int | float]] so keep values to int/float.
- **The `components["supply_floor_clamped"]` flag is the signal-
  quality gate.** Any IC / backtest code in phase 07+ that
  regresses against equity returns MUST filter on
  `components["supply_floor_clamped"] == 0` or accept that the
  pre-phase-05 era's ratios are mostly the floor telling you
  "supply is unknown". Document this in phase 07's test fixtures.
- **Determinism.** `compute_daily_tightness` is bit-deterministic
  for fixed inputs; `test_deterministic_for_fixed_inputs` pins
  this. Backtests can rely on re-running the same date producing
  the same snapshot.
- **Float sensitivity.** `forward_demand_ton_miles` is a `round()`
  of a sum-of-products in float64. Magnitudes are ~10^10, so 1-bit
  FP noise is possible across platforms. Tests currently use `==`;
  on CI flake this is the first thing to switch to
  `pytest.approx(..., abs=1)` — noted but not yet needed.
- **Alembic 0002 is idempotent** via `alembic upgrade head`; the
  integration test `test_alembic_upgrade_head_creates_signals_table`
  asserts the unique index exists by column set, not name — so
  renaming the index in a later migration would still satisfy
  the test, but the Python constant targeting `index_elements=
  ["as_of", "route"]` in `upsert_snapshot` does the same by-column
  targeting.
- **Log hygiene.** Per-voyage `cargo_tons fallback` is aggregated
  to a single summary WARN at the end of a compute call. If a
  future debugger wants per-vessel granularity, the
  `fallback_mmsis` list is already accumulated inside
  `_compute_forward_demand` — promote to DEBUG.
- **`run_once(persist=True)`** does two DB round-trips: one to
  load prior snapshots, one to upsert the new one. Both are inside
  `session_scope()` with proper commit/rollback. If phase 11's
  APScheduler retries a failed run, re-running is safe (upsert is
  idempotent on Postgres via ON CONFLICT).
