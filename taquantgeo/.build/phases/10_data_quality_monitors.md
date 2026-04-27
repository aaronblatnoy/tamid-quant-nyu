# Phase 10 — Data quality monitors

## Metadata
- Effort: `standard`
- Depends on phases: 04, 06
- Applies security-review: `no`
- Max phase runtime (minutes): 90
- External services: none

## Mission
Signals + backtests are only trustworthy if the underlying data pipeline
is healthy. An AIS ingestion outage, a voyage CSV that wasn't refreshed,
a ticker that stopped reporting — any of these silently corrupts the
signal and, eventually, live trades. This phase ships an ensemble of
health checks that run on a schedule (wired by phase 11), emit structured
JSON events per run, and alert (phase 12 sinks) on failure. A check's
success criterion is a single boolean plus a "findings" payload that
tells an on-call human what's wrong.

## Orientation
- `.build/handoffs/04_handoff.md`, `06_handoff.md`
- `packages/ais/src/taquantgeo_ais/` — streamer + GFW pipelines (inputs
  being monitored)
- `packages/prices/src/taquantgeo_prices/` — prices source
- `packages/signals/src/taquantgeo_signals/tightness.py`
- `docs/runbook.md` — existing daily checklist (checks must satisfy this)

## Service preflight
- `DATABASE_URL` required.

## Acceptance criteria
- `packages/jobs/src/taquantgeo_jobs/data_quality.py` with:
  - `@dataclass(frozen=True) class CheckResult: name: str; passed: bool;
     findings: dict[str, object]; duration_ms: float`
  - `class DataQualityCheck(Protocol): def run(self) -> CheckResult: ...`
  - Concrete checks (one class each):
    1. `AisVolumeCheck` — last 24h parquet row count under
       `data/raw/ais_live/` within 2σ of 30-day mean; fail if < 0.5σ
       below mean (outage) or files missing for > 12h
    2. `VoyagesIngestCheck` — latest month file exists on disk within
       expected release window (GFW publishes in first 5 days of M+1)
    3. `PricesArrivedCheck` — yesterday's close is in `prices` table
       for every default ticker (skip weekends + known market holidays)
    4. `SignalComputedCheck` — yesterday's signal row exists in
       `signals` table for every tracked route
    5. `NoNullsInCriticalColumnsCheck` — voyages parquet, vessel
       registry, prices, signals have zero nulls in their NOT-NULL
       columns
- `run_all_checks() -> list[CheckResult]` runs all checks and returns
  results. Each result contains enough info to reproduce the failure
  (affected rows, dates, counts).
- CLI: `taq jobs check-data-quality [--json]`:
  - Prints results as table (default) or JSON
  - Exit 0 if all pass, exit 3 if any fail (so downstream scheduler can
    surface)
  - Includes a `run_id` (uuid4) in JSON output so alerts can correlate
- Tests:
  - `test_ais_volume_passes_within_2sigma`
  - `test_ais_volume_fails_on_outage`
  - `test_ais_volume_fails_on_stale_files_over_12h`
  - `test_voyages_ingest_passes_when_file_present`
  - `test_prices_arrived_skips_weekend_gracefully`
  - `test_prices_arrived_flags_missing_ticker`
  - `test_signal_computed_flags_missing_route`
  - `test_no_nulls_finds_null_in_critical_column`
  - `test_run_all_checks_aggregates`
  - `test_cli_exit_codes_match_pass_fail`
- JSON output schema stable — tests assert shape so downstream alert
  parsing doesn't silently drift.
- All quality gates green.

## File plan
- `packages/jobs/src/taquantgeo_jobs/data_quality.py` — new
- `packages/jobs/src/taquantgeo_jobs/__init__.py` — export checks
- `packages/jobs/tests/test_data_quality.py` — new
- `packages/jobs/tests/fixtures/` — synthetic AIS parquet snippets,
  signal + price fixtures
- `packages/cli/src/taquantgeo_cli/jobs.py` — new typer subapp with
  `check-data-quality` subcommand
- `packages/cli/src/taquantgeo_cli/main.py` — register `jobs_app`
- `docs/runbook.md` — EXTEND existing "Daily checklist" section to map
  each check to a runbook procedure (kill switch, fallback, ticket)
- `CLAUDE.md` — register jobs package + CLI

## Non-goals
- Alert delivery — phase 12 owns that. This phase emits structured
  results only.
- Scheduling — phase 11.
- Auto-remediation — operational; checks detect, humans fix.
- Vessel-level anomaly detection (phantom vessels, teleporting MMSIs) —
  candidate entry.

## Quality gates
- Format + lint + typecheck clean
- ≥10 new tests
- Pre-commit meta-review: single round acceptable for `standard` effort
- Runbook updated

## Git workflow
1. Branch `feat/phase-10-data-quality-monitors`
2. Commits:
   - `feat(jobs): data quality checks + CheckResult protocol`
   - `feat(cli): taq jobs check-data-quality`
   - `test(jobs): per-check + CLI coverage`
   - `docs: runbook daily checklist mapped to checks`
3. PR, CI green, squash-merge

## Handoff
Which checks pass and fail against the real live data at phase time
(not a blocker — observation only). Any check that can't be
deterministically tested should be noted with its confidence
assessment.
