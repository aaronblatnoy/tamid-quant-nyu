# Phase 04 — Tightness signal core

## Metadata
- Effort: `max`
- Depends on phases: 01, 02, 03
- Applies security-review: `no`
- Max phase runtime (minutes): 180
- External services:
  - `DATABASE_URL (required)` — already set to local dev postgres. If
    Postgres is down, `docker compose -f infra/docker-compose.yml up -d`
    in preflight; only halt if Docker itself is unavailable (phase 00
    caught that).

## Mission
The core of the product. `compute_daily_tightness(as_of)` takes a date,
pulls voyage state + vessel registry + sea-route distances + dark-fleet
candidates, returns a `TightnessSnapshot` with a single ratio that is the
trading signal. Every downstream phase — IC analysis, backtester, live
pipeline, dashboards, alerts — reads this snapshot. The math here is the
signal's definition; it must be documented, typed, tested, and stable.
Future phases may add baselines, detrend, or regress it; they may not
redefine it without a new ADR. This phase also creates the `signals`
table (alembic migration) so snapshots persist to Postgres for dashboards.

## Signal math (the equations this phase implements)

The phase agent MUST implement these equations literally. Any deviation requires amending ADR 0007 first. All distance units are nautical miles; all time units are days; all vessel counts are integers.

**Forward demand (ton-miles)** — the ton-miles of laden VLCC cargo currently underway that will land in the destination region within the forward horizon:
```
forward_demand_ton_miles(as_of) =
  Σ { cargo_tons(v) × remaining_distance_nm(v, as_of) : v ∈ in_progress_laden_TD3C_voyages(as_of) }
```
where:
- `cargo_tons(v)` = `vessel_registry.dwt` for vessel v; fall back to the route nominal (270,000 dwt for TD3C VLCC) if dwt is null and log a WARN including the vessel_id. Document the fallback count in the snapshot `components` dict as `cargo_tons_fallback_used`.
- `remaining_distance_nm(v, as_of)` = sea-route distance (phase 02 `distance_cache`) from the vessel's latest AIS position at or before `as_of` to the destination anchorage. If the (current_position_s2id, dest_anchorage_s2id) pair is absent from the cache, fall back to great-circle distance and increment `components["great_circle_fallbacks"]`.
- `in_progress_laden_TD3C_voyages(as_of)` = voyages with `route=td3c`, state `in_progress`, `direction=laden`, `trip_start ≤ as_of`, `trip_end` null or `> as_of`, and vessel flagged `is_vlcc_candidate=true` in the registry.

**Forward supply (count)** — the number of ballast VLCCs estimated to arrive in the origin region (Persian Gulf) within `supply_horizon_days` (default 15) of `as_of`:
```
forward_supply_count(as_of) =
  |{ v : v ∈ ballast_VLCCs(as_of)
       AND estimated_arrival_pg(v, as_of) ≤ as_of + supply_horizon_days }|
```
where `estimated_arrival_pg(v, as_of) = as_of + distance_to_pg_nm(v, as_of) / (24 × avg_sog_knots(v))`. `avg_sog_knots(v)` is the 7-day rolling mean SOG from live AIS positions; if insufficient AIS history, fall back to 13 knots (VLCC ballast nominal). Record `components["avg_sog_fallback_used"]` count.

**Dark-fleet supply adjustment** — candidates from phase 03 observed at PG loading terminals within the prior 7 days:
```
dark_fleet_supply_adjustment(as_of) =
  |{ d ∈ dark_fleet_df :
       d.nearest_anchorage in MAJOR_LOADING_TERMINALS
       AND d.has_matching_voyage = false
       AND (as_of - 7d) ≤ d.detection_timestamp ≤ as_of }|
```

**Effective supply** — floored at 1 to avoid division-by-zero inflation and signal the floor case via the snapshot:
```
effective_supply(as_of) = max(forward_supply_count − dark_fleet_supply_adjustment, 1)
```
If the max clamp fires (raw effective ≤ 0), set `components["supply_floor_clamped"] = true`.

**Ratio** — the tightness signal, in units of ton-miles per ballast vessel:
```
ratio(as_of) = forward_demand_ton_miles(as_of) / effective_supply(as_of)
```
If `effective_supply == 0` (cannot happen given the floor, but defensively): return `float("inf")`. Tests assert the floor prevents this in practice.

**Z-score (90-day)** — standardized vs prior 90 trading days, strictly no look-ahead:
```
window = previous 90 snapshots with as_of' ∈ [as_of − 90d, as_of − 1d]
mean_90d = mean(ratio over window)
std_90d  = stdev(ratio over window, sample)
z_score_90d(as_of) = (ratio(as_of) − mean_90d) / std_90d   if |window| ≥ 30 else None
```
If `std_90d == 0` (flat window), return None and log a WARN including as_of.

**Determinism** — compute_daily_tightness MUST be deterministic for fixed inputs. Any random sampling (none is expected) is seeded and the seed goes into `components`.

Every term above has a corresponding unit test in phase 04's acceptance criteria. The `components` dict is the phase's audit trail and makes every snapshot reproducible.

## Orientation
- `.build/handoffs/00_handoff.md` through `03_handoff.md`
- `docs/data_model.md` — planned `signals` table shape
- `packages/ais/src/taquantgeo_ais/gfw/{voyages,anchorages,routes,
  classifier,distance,sar}.py` — the inputs
- `packages/core/src/taquantgeo_core/{config.py,db.py,schemas.py}` —
  Settings, engine, ORM base
- `infra/alembic/versions/0001_initial_vessels.py` — migration style
- `docs/adrs/0002-gfw-voyages-as-historical-source.md` — what voyages
  CSV gives us and does not
- `packages/ais/src/taquantgeo_ais/gfw/events.py` — quality bar

## Service preflight
- `DATABASE_URL` required. Default resolves to local postgres. If
  `docker compose ps` shows the postgres container is down,
  `docker compose -f infra/docker-compose.yml up -d`. If local postgres
  cannot be reached after bring-up, halt with manual-setup entry.

## Acceptance criteria
- NEW package at `packages/signals/` with `pyproject.toml`,
  `src/taquantgeo_signals/__init__.py`. Added to root `uv.sources`
  and ruff `known-first-party`. `uv sync` clean.
- `packages/signals/src/taquantgeo_signals/tightness.py` with module
  docstring that documents the signal math in FULL (equations, units, why
  each term is what it is). Module exports:
  - `@dataclass(frozen=True) class TightnessSnapshot: as_of: date;
     route: str; forward_demand_ton_miles: int;
     forward_supply_count: int; dark_fleet_supply_adjustment: int;
     ratio: float; z_score_90d: float | None;
     components: dict[str, int | float]`
  - `compute_daily_tightness(as_of: date, *, voyages_df, vessel_registry_df,
     distance_cache_df, dark_fleet_df, route="td3c",
     lookback_days: int = 90) -> TightnessSnapshot`
- Alembic migration `infra/alembic/versions/0002_signals_table.py` adds
  `signals` per `docs/data_model.md` (with slight additions for the new
  columns: `dark_fleet_supply_adjustment`, `components_jsonb`).
- `packages/signals/src/taquantgeo_signals/persistence.py` with
  `upsert_snapshot(session, snapshot) -> None` (upsert on
  `(as_of, route)`).
- Job stub `packages/jobs/src/taquantgeo_jobs/daily_signal.py` with
  `run_once(as_of: date | None = None) -> TightnessSnapshot`. NOT scheduled
  yet — phase 11 wires APScheduler. Stub exists so phase 11 imports a real
  function.
- Tests:
  - `test_forward_demand_sums_ton_miles_remaining` — single in-progress
    laden voyage, asserts `forward_demand_ton_miles` equals expected
    distance × cargo-tons remaining proportion
  - `test_forward_supply_counts_ballast_arrivals_in_15_day_window`
  - `test_dark_fleet_adjustment_reduces_supply`
  - `test_ratio_is_demand_over_effective_supply`
  - `test_ratio_handles_zero_supply_returns_inf_not_nan` — explicit
    handling: if effective supply is 0, return `float("inf")`; document
    why (phase 07 IC analysis must not silently divide-by-zero)
  - `test_z_score_90d_uses_rolling_history`
  - `test_z_score_none_when_insufficient_history` — <30 days returns None
  - `test_components_dict_exposes_raw_terms` — components dict must
    include `forward_demand_ton_miles`, `forward_supply_count`,
    `dark_fleet_candidates_used`, `vlcc_vessels_considered`,
    `route_total_distance_nm`
  - `test_upsert_snapshot_idempotent` — running twice yields one row
  - `test_alembic_upgrade_head_creates_signals_table` — integration
    marker; skipped in default CI slice, runs in integration slice
- CLI: `taq signals compute-tightness [--as-of YYYY-MM-DD] [--route td3c]
  [--persist/--no-persist]` writes to stdout + optionally upserts.
- All quality gates green.

## File plan
- `packages/signals/pyproject.toml` — new
- `packages/signals/src/taquantgeo_signals/__init__.py` — exports
  `compute_daily_tightness`, `TightnessSnapshot`
- `packages/signals/src/taquantgeo_signals/tightness.py` — new
- `packages/signals/src/taquantgeo_signals/persistence.py` — new
- `packages/signals/src/taquantgeo_signals/models.py` — NEW SQLAlchemy
  model for `signals` (no `from __future__ import annotations`)
- `packages/jobs/pyproject.toml` + `packages/jobs/src/taquantgeo_jobs/
  __init__.py` + `daily_signal.py` — new package (APScheduler not wired
  yet; phase 11 imports this)
- `packages/cli/src/taquantgeo_cli/signals.py` — new typer subapp
- `packages/cli/src/taquantgeo_cli/main.py` — register `signals_app`
- `pyproject.toml` (root workspace) — add `taquantgeo-signals`,
  `taquantgeo-jobs` to members + sources
- `ruff.toml` (or pyproject ruff section) — add both to
  `known-first-party`
- `infra/alembic/versions/0002_signals_table.py` — new migration
- `packages/signals/tests/test_tightness.py` + `test_persistence.py`
- `packages/signals/tests/fixtures/*.parquet` — pinned fixtures for
  voyages / vessel_registry / distances / dark_fleet
- `docs/adrs/0007-tightness-signal-definition.md` — NEW ADR with the
  exact signal math, units, normalization choice, rationale
- `docs/RESEARCH_LOG.md` — append entry on signal definition decisions
- `CLAUDE.md` — register new packages + new CLI

## Non-goals
- Scheduler wiring — phase 11
- Equity prices + IC — phase 06/07
- Baseline strategies (mean reversion, seasonality) — candidate
- Signal persistence to parquet (only Postgres here) — phase 10+ may add
- Multi-route signals — only `td3c` routed here; multi-route in candidates

## Quality gates
- Format + lint + typecheck clean on both new packages
- `uv run alembic upgrade head` clean against local postgres
- All new tests pass; integration-marker test passes when postgres up
- Test count increases by at least 10
- Pre-commit meta-review full loop
- `Effort: max` → 3-round review loop
- ADR 0007 written — CRITICAL; this is the signal's founding document

## Git workflow
1. Branch `feat/phase-04-tightness-signal`
2. Commits:
   - `feat(signals): new package scaffold`
   - `feat(signals): compute_daily_tightness core + snapshot schema`
   - `feat(signals): signals table alembic migration + persistence`
   - `feat(jobs): scaffold + daily_signal.run_once stub`
   - `feat(cli): taq signals compute-tightness`
   - `test(signals): tightness math + persistence coverage`
   - `docs: ADR 0007 tightness signal definition; CLAUDE.md updates`
3. PR, CI green, squash-merge

## Handoff
Document the exact math that shipped, any surprises discovered while
fixturing, the test coverage report, and the first live computation
against real data on disk (row count of voyages considered, vessels
considered, distance pairs hit).
