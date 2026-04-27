# Phase 16 — Audit log

## Metadata
- Effort: `standard`
- Depends on phases: 13, 14, 15
- Applies security-review: `yes`
- Max phase runtime (minutes): 120
- External services: none

## Mission
Phase 13 shipped a jsonl stub for audit events; phase 16 replaces it
with the production append-only Postgres table per `docs/data_model.md`,
adds a SHA-256 hash chain so tampering produces a detectable break, and
ships a parquet exporter for archival. The audit log is the only record
of "why did the system do that?" when something goes wrong in production;
the chain makes it evidence-grade for a post-incident review.

## Orientation
- `.build/handoffs/13_handoff.md`, `14_handoff.md`, `15_handoff.md`
- `packages/trade/src/taquantgeo_trade/audit.py` — jsonl stub to replace
- `docs/data_model.md` — `audit_log` table spec
- `packages/trade/src/taquantgeo_trade/risk.py` + `reconciliation.py` —
  current audit callers (they pass a callable; this phase swaps the
  callable to the new sink)

## Service preflight
- `DATABASE_URL` required.

## Acceptance criteria
- Alembic migration `0006_audit_log_table.py` creates `audit_log` per
  `docs/data_model.md` PLUS two added columns:
  - `prev_hash` (bytea NULLABLE — first row is NULL; every subsequent
    row is sha256 of the previous row's canonical JSON)
  - `entry_hash` (bytea NOT NULL — sha256 of this row's canonical JSON
    including prev_hash)
- `packages/trade/src/taquantgeo_trade/audit.py` (rewritten; deletes
  stub):
  - `@dataclass(frozen=True) class AuditEntry: occurred_at: datetime;
     actor: str; event_type: str; payload: dict[str, object]`
  - `class PostgresAuditSink:`
    - `append(self, entry)` — reads most recent row's `entry_hash`
      inside a transaction, computes new `entry_hash`, inserts. Uses
      `SELECT ... FOR UPDATE` on a single-row advisory-lock row so
      concurrent appends serialize (hash chain needs serial order).
    - `verify_chain(self, *, since: datetime | None = None) -> ChainVerifyResult`
      — walks the chain, returns `ChainVerifyResult(ok, first_break_id,
      first_break_at)`
  - `export_to_parquet(session, out_path, *, since=None) -> int` —
    appends to a date-partitioned parquet tree under
    `data/archive/audit/year=YYYY/month=MM/*.parquet` and returns row
    count written
  - Canonical JSON: sorted keys, `default=str` for datetimes, no
    whitespace — documented so anyone verifying a hash externally can
    reproduce it
- Callers updated:
  - `RiskGate` now takes `PostgresAuditSink` (or any sink with
    `append(AuditEntry)`) instead of the jsonl stub
  - `Reconciler` same
- Tests:
  - `test_audit_entry_hashes_match_external_sha256` — compute sha256
    of the canonical JSON in-test and assert equality with stored
    `entry_hash`
  - `test_audit_chain_detects_tampering` — mutate one row's payload in
    the DB; `verify_chain` flags it as the first break
  - `test_audit_chain_contiguous_after_many_appends`
  - `test_audit_append_serializes_under_concurrency` — spawn 20 threads
    appending simultaneously; final chain is well-formed, no gaps,
    no duplicate hashes
  - `test_audit_export_to_parquet_round_trip` — export then re-read;
    rows equal
  - `test_audit_export_partitioned_by_year_month`
  - `test_risk_gate_uses_postgres_audit_when_wired`
  - `test_reconciler_uses_postgres_audit_when_wired`
- Migration drops the jsonl stub path from `audit.py` (code is gone;
  old jsonl file remains on disk for historical reads but is no longer
  written to).
- CLI: `taq trade audit verify [--since YYYY-MM-DD]` runs the chain
  verifier and prints result.
- CLI: `taq trade audit export [--since YYYY-MM-DD] [--out <dir>]`
  exports to parquet.
- All quality gates green.

## File plan
- `infra/alembic/versions/0006_audit_log_table.py` — new
- `packages/trade/src/taquantgeo_trade/audit.py` — rewritten
- `packages/trade/src/taquantgeo_trade/risk.py` — swap audit sink type
  (still a Protocol for testing); narrow from `Callable` to an explicit
  `AuditSink` protocol
- `packages/trade/src/taquantgeo_trade/reconciliation.py` — same swap
- `packages/trade/tests/test_audit.py` — new
- `packages/trade/tests/test_risk.py` — extend with new sink type
- `packages/trade/tests/test_reconciliation.py` — extend
- `packages/cli/src/taquantgeo_cli/trade.py` — add `audit verify` and
  `audit export`
- `docs/adrs/0016-audit-log-hash-chain.md` — NEW ADR: why hash-chained
  vs Merkle tree, canonical-JSON choice, concurrency model
- `docs/runbook.md` — expand audit section: how to verify chain, how to
  export, what a chain break means operationally

## Non-goals
- Remote witness / third-party timestamp (for tamper-evidence against
  the DB admin) — candidate entry
- Signed audit entries — candidate
- Search / query UI — candidate (phase 23's alerts page shows some;
  full audit browser is separate)

## Quality gates
- Format + lint + typecheck clean
- ≥8 new tests including concurrency test
- `uv run alembic upgrade head` clean
- Pre-commit meta-review + **security-review subagent mandatory**
- ADR 0016 committed

## Git workflow
1. Branch `feat/phase-16-audit-log`
2. Commits:
   - `feat(trade): audit_log migration with hash-chain columns`
   - `feat(trade): PostgresAuditSink with chain verify + parquet export`
   - `refactor(trade): RiskGate + Reconciler use new audit sink`
   - `feat(cli): taq trade audit verify/export`
   - `test(trade): audit chain + concurrency + roundtrip`
   - `docs: ADR 0016 audit log hash chain; runbook`
3. PR, CI green, squash-merge

## Handoff
Post-migration row count (should be 0 unless carry-forward). Note
whether the jsonl stub file from phase 13 still exists on disk (it
should — historical evidence, just no longer written to). Concurrency
test run summary.
