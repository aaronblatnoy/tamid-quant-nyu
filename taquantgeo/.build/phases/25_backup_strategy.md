# Phase 25 — Backup strategy

## Metadata
- Effort: `standard`
- Depends on phases: 24
- Applies security-review: `no`
- Max phase runtime (minutes): 120
- External services:
  - `R2_ACCOUNT_ID (required)`, `R2_ACCESS_KEY_ID (required)`,
    `R2_SECRET_ACCESS_KEY (required)`, `R2_BUCKET (required)` — Cloudflare
    R2 for off-host backup. If any missing, halt with manual-setup entry.

## Mission
A production trading system that can't restore from backup is playing
with survivor bias. Nightly pg_dump from Neon ships to Cloudflare R2
with 30-day retention. Parquet cold-archive lifecycle moves
`data/raw/` after 90 days. Restore procedure is not just documented —
it's RUN during this phase against a Neon development branch as a drill
so we know the procedure actually works.

## Orientation
- `.build/handoffs/24_handoff.md`
- `docs/adrs/0001-stack-decisions.md` — R2 choice
- `docs/runbook.md` — existing "Rollback" and backup gaps

## Service preflight
All four R2 env vars required. Neon DATABASE_URL required (live prod
branch).

## Acceptance criteria
- Backup job:
  - `packages/jobs/src/taquantgeo_jobs/backup.py` with:
    - `backup_postgres_to_r2(source_url, r2_config, retention_days=30)`
      — runs pg_dump, streams upload to R2 using multipart, tags with
      date + source branch name
    - `sync_cold_archive(local_dir, r2_config, older_than_days=90)` —
      finds files older than the threshold, uploads + deletes local copy
  - APScheduler registers `backup_postgres_to_r2` daily 02:00 UTC
  - APScheduler registers `sync_cold_archive` weekly Sundays 03:00 UTC
  - Retention enforcement: after each upload, list R2 objects older
    than 30 days and delete them
- Restore:
  - `packages/jobs/src/taquantgeo_jobs/restore.py`:
    - `restore_postgres_from_r2(snapshot_key, target_url)` — downloads
      the backup + runs pg_restore against the target URL
  - CLI: `taq jobs restore-postgres --snapshot <key> --target <url>
    [--dry-run]`
- **DRILL (must actually run during phase)**:
  1. Trigger a manual backup: `taq jobs backup-postgres` against the
     prod DB (not local)
  2. Confirm the backup lands in R2
  3. Create a Neon *development* branch named `restore-drill-YYYYMMDD`
  4. Restore the snapshot to that dev branch
  5. Connect and SELECT COUNT(*) from at least 3 tables to confirm
     data integrity
  6. Record the whole transcript under
     `docs/drills/backup_restore_YYYYMMDD.md` (drills are tracked;
     commit the drill record)
  7. Delete the dev branch after verification
- Tests (no real R2 in CI — use moto or similar mock):
  - `test_backup_calls_pg_dump_with_correct_args`
  - `test_backup_uploads_to_r2_with_dated_key`
  - `test_retention_deletes_old_objects`
  - `test_retention_keeps_recent_objects`
  - `test_restore_downloads_snapshot_and_runs_pg_restore` (mocked
    subprocess)
  - `test_restore_dry_run_does_not_invoke_pg_restore`
  - `test_cold_archive_sync_uploads_and_deletes_local`
  - `test_cold_archive_sync_respects_age_threshold`

## File plan
- `packages/jobs/src/taquantgeo_jobs/backup.py`
- `packages/jobs/src/taquantgeo_jobs/restore.py`
- `packages/jobs/src/taquantgeo_jobs/scheduler.py` — add the new cron
  entries
- `packages/cli/src/taquantgeo_cli/jobs.py` — add `backup-postgres`,
  `restore-postgres` subcommands
- `packages/jobs/tests/test_backup.py`, `test_restore.py`,
  `test_cold_archive.py`
- `docs/adrs/0023-backup-strategy.md` — NEW ADR: R2 + retention
  rationale, 30/90-day choices, drill cadence, why no encrypted-at-rest
  extra layer for v0 (R2 encrypts at rest; add SSE-C candidate if
  compliance requires)
- `docs/drills/backup_restore_<date>.md` — drill record
- `docs/runbook.md` — expand: concrete restore procedure steps,
  how to run the drill, R2 bucket layout

## Non-goals
- Incremental / WAL-level PITR (Neon itself supports this for short
  windows; R2 ships nightly full dumps as the cold tier) — candidate
- Cross-region R2 replication — candidate
- Encrypted-at-rest extra layer (SSE-C) — candidate
- Backup integrity hashing beyond R2's default — candidate

## Quality gates
- Format + lint + typecheck clean
- ≥8 new tests
- Drill executed and recorded; the drill record must show
  non-zero row counts on restore and a successful cleanup
- Pre-commit meta-review full loop (infra-touching)
- ADR 0023 committed

## Git workflow
1. Branch `feat/phase-25-backup-strategy`
2. Commits:
   - `feat(jobs): nightly Postgres backup to R2 with retention`
   - `feat(jobs): weekly cold-archive sync for data/raw/*`
   - `feat(jobs): pg restore from R2 + dry-run safety`
   - `feat(cli): taq jobs backup-postgres + restore-postgres`
   - `test(jobs): backup + restore + retention + archive`
   - `docs: ADR 0023 backup strategy; drill record; runbook`
3. PR, CI green, squash-merge

## Handoff
Drill outcome (link to the drill doc). R2 bucket structure observed
(`taquantgeo-archive/backups/` vs cold tier). Any surprise (e.g.,
pg_dump+Neon pooling idiosyncrasies).
