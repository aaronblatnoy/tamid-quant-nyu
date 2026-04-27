# Phase 11 — APScheduler daily pipeline

## Metadata
- Effort: `standard`
- Depends on phases: 04, 06, 10
- Applies security-review: `no`
- Max phase runtime (minutes): 90
- External services: none

## Mission
Operational glue. The pieces exist (live streamer, signal compute, price
update, data-quality checks, voyage monthly ingest); they just need to
run on a schedule without human triggers. APScheduler inside a single
long-running systemd-managed process handles daily jobs; the AIS
streamer stays in its own systemd unit because it's a long-lived
WebSocket consumer. This phase declares the cron-like schedule, wires
each job to its existing function (NOT re-implementing any), ships
systemd unit templates, and ensures jobs are idempotent (safe to run
twice because of a restart).

## Orientation
- `.build/handoffs/04_handoff.md`, `06_handoff.md`, `10_handoff.md`
- `packages/jobs/src/taquantgeo_jobs/daily_signal.py` — phase 04 stub
- `packages/jobs/src/taquantgeo_jobs/data_quality.py` — phase 10
- `packages/prices/src/taquantgeo_prices/` — `taq prices update` target
- `packages/ais/src/taquantgeo_ais/gfw/extract.py` — voyages ingest
- `packages/ais/src/taquantgeo_ais/` — live streamer entry point (already
  in a separate systemd unit conceptually)
- `docs/adrs/0001-stack-decisions.md` — APScheduler choice

## Service preflight
None (uses local postgres for state; already required).

## Acceptance criteria
- `packages/jobs/src/taquantgeo_jobs/scheduler.py` with:
  - `build_scheduler(app_env: str) -> BackgroundScheduler`
  - Job schedule (all UTC):
    - `prices_update` — daily at 21:00
    - `daily_signal` — daily at 23:00
    - `data_quality_check` — daily at 23:30
    - `voyages_monthly_ingest` — day-5 of each month at 03:00
  - Each job wraps the existing function and emits a structured log line
    with `run_id`, start, end, exit_code. On exception, logs + sends
    alert (phase 12 interface — use the no-op sink if phase 12 not yet
    in place; this phase may import phase 12 surfaces since dep chain
    allows)
  - Job misfire grace time configured (60 min) so a brief host restart
    doesn't skip a run
  - `coalesce=True` — if the host is offline for > 1 run, don't run
    backlog on startup; log and move on
- CLI: `taq jobs run-scheduler` — runs the scheduler in the foreground
  (useful for dev + systemd exec mode).
- Idempotency: each job's underlying function is already idempotent
  (signal upsert, price upsert, voyages parquet partitioned by
  (route, year, month) so rewrite is safe). Tests verify this.
- Systemd units:
  - `infra/systemd/taquantgeo-scheduler.service` — runs
    `uv run taq jobs run-scheduler` under the service user
  - `infra/systemd/taquantgeo-ais-stream.service` — runs the AIS live
    streamer (reuses phase 1a CLI, just formalizing the unit)
  - Both units use `Restart=on-failure`, `RestartSec=30s`, drop
    privileges to a non-root `taq` user, load env from
    `/opt/taquantgeo/.env`
- Tests (NO real APScheduler running in CI; use MemoryJobStore +
  time-mocked):
  - `test_scheduler_registers_all_expected_jobs`
  - `test_scheduler_job_times_are_utc`
  - `test_job_exception_does_not_kill_scheduler`
  - `test_job_emits_structured_log_line_with_run_id`
  - `test_run_id_changes_per_run`
  - `test_misfire_grace_time_is_60_min`
  - `test_coalesce_true`
- All quality gates green.

## File plan
- `packages/jobs/src/taquantgeo_jobs/scheduler.py` — new
- `packages/jobs/src/taquantgeo_jobs/voyages_ingest.py` — thin wrapper
  around existing `extract_route` for scheduled monthly ingest
- `packages/jobs/src/taquantgeo_jobs/prices_update.py` — thin wrapper
  that calls `taquantgeo_prices.persistence.update_all_tickers`
- `packages/jobs/src/taquantgeo_jobs/daily_signal.py` — upgrade stub
  from phase 04 to the full idempotent job (still called
  `run_once(as_of=None)`; adds structured logging)
- `packages/jobs/tests/test_scheduler.py`
- `packages/cli/src/taquantgeo_cli/jobs.py` — add `run-scheduler` cmd
- `infra/systemd/taquantgeo-scheduler.service`
- `infra/systemd/taquantgeo-ais-stream.service`
- `docs/runbook.md` — add "Scheduler management" section (status,
  restart, logs location)
- `docs/adrs/0012-daily-pipeline-scheduling.md` — NEW ADR: APScheduler
  vs cron vs GH Actions tradeoffs, why we chose APS in-process for v0,
  what would push us to a queue (Celery / Dramatiq) later

## Non-goals
- Distributed scheduling (only one host runs this). Candidate entry for
  when we scale past one host.
- Job backfill tooling (manual: `taq signals compute-tightness --as-of
  <date>`). Candidate entry for a `backfill` CLI.
- Retry-on-failure logic beyond APScheduler's misfire_grace_time.
  Candidate.

## Quality gates
- Format + lint + typecheck clean
- ≥7 new tests
- `systemctl edit --full --no-pager taquantgeo-scheduler` (dry parse
  only in the phase) shows valid unit file — phase may use `systemd-
  analyze verify <path>` if available, else skip and note in handoff
- Pre-commit meta-review full loop (>1 file, deploy-adjacent)

## Git workflow
1. Branch `feat/phase-11-scheduled-jobs`
2. Commits:
   - `feat(jobs): APScheduler daily pipeline`
   - `feat(jobs): prices + signal + voyages + quality job wrappers`
   - `feat(cli): taq jobs run-scheduler`
   - `feat(infra): systemd units for scheduler + AIS streamer`
   - `test(jobs): scheduler registration + job isolation`
   - `docs: ADR 0012 daily pipeline scheduling; runbook updates`
3. PR, CI green, squash-merge

## Handoff
Schedule table reproduced. Unit file validation result. Note whether
the scheduler has been exercised locally (smoke: start, let one job
fire, stop).
