# Phase 15 — Reconciliation

## Metadata
- Effort: `max`
- Depends on phases: 13, 14
- Applies security-review: `yes`
- Max phase runtime (minutes): 120
- External services:
  - Whatever `make_broker()` returns — uses MockBroker in tests and
    automatically upgrades to IbkrBroker when IBKR env configured.

## Mission
The books at our end and the books at IBKR must match every day or
something is wrong — a fill we missed, a restart that lost state, a
manual trade someone entered outside the system. Daily reconciliation
compares expected positions (our `positions_book`) against broker actual
positions. Any mismatch is a **critical** event: alert via phase 12
sinks, engage kill switch (atomic), append `recon_mismatch` audit entry.
Resolution is manual — no auto-recovery, because a silent "I think I
fixed it" is worse than a halted system.

## Orientation
- `.build/handoffs/13_handoff.md`, `14_handoff.md`
- `packages/trade/src/taquantgeo_trade/broker.py` + `mock_broker.py`
  + `ibkr.py`
- `packages/core/src/taquantgeo_core/alerting.py` — sinks
- `docs/data_model.md` — `reconciliations` table shape
- `docs/runbook.md` — reconciliation-mismatch procedure

## Service preflight
None required — broker is via factory.

## Acceptance criteria
- `packages/trade/src/taquantgeo_trade/reconciliation.py` with:
  - `@dataclass(frozen=True) class ReconResult: as_of: date;
     expected: dict[str, int]; actual: dict[str, int]; matched: bool;
     diff: dict[str, tuple[int, int]]  # ticker -> (expected, actual)`
  - `class Reconciler:`
    - `__init__(self, broker, session_factory, alert_sinks, kill_switch_fn)`
    - `run(self, as_of: date | None = None) -> ReconResult`
    - On mismatch: emit CRITICAL alert, call `kill_switch_fn()`, persist
      a `reconciliations` row with diff, append `recon_mismatch` audit
      entry. All four actions happen atomically — if the alert sink
      raises, the kill switch and audit still happen.
  - `engage_kill_switch_atomic(settings_path, *, reason) -> None` — writes
    `KILL_SWITCH=true` to the env file used by the running process,
    captures a lockfile so concurrent engagements don't race, and
    signals the running scheduler/trading processes to reload. Lockfile
    path documented in runbook.
- Alembic migration `0005_reconciliations_table.py` for the new table.
- CLI: `taq trade reconcile [--as-of YYYY-MM-DD] [--dry-run]`. `--dry-run`
  prints the diff but does NOT engage kill switch or persist.
- Tests (MockBroker controlled mismatch scenarios):
  - `test_recon_matched_when_positions_identical`
  - `test_recon_mismatch_when_actual_missing_ticker`
  - `test_recon_mismatch_when_actual_has_extra_ticker`
  - `test_recon_mismatch_qty_differs`
  - `test_recon_engages_kill_switch_on_mismatch`
  - `test_recon_engages_kill_switch_once_even_on_race` — spawn two
    concurrent reconcilers that both detect mismatch; kill switch
    engages exactly once, both alert, both record audit (thread-safe)
  - `test_recon_alert_failure_still_engages_kill_switch_and_audits`
  - `test_recon_persists_row_with_full_diff_jsonb`
  - `test_recon_dry_run_does_not_engage_or_persist`
  - `test_recon_idempotent_for_same_as_of` — running reconcile twice
    for the same date with same state produces same row (upsert)
- All quality gates green.

## File plan
- `packages/trade/src/taquantgeo_trade/reconciliation.py` — new
- `packages/trade/src/taquantgeo_trade/kill_switch.py` — new (atomic
  engagement)
- `packages/trade/tests/test_reconciliation.py`
- `packages/trade/tests/test_kill_switch.py`
- `packages/cli/src/taquantgeo_cli/trade.py` — new typer subapp with
  `reconcile` subcommand; register in main
- `infra/alembic/versions/0005_reconciliations_table.py`
- `docs/adrs/0015-reconciliation-and-kill-switch.md` — NEW ADR: daily
  check cadence, atomic kill-switch design, manual-only resolution
  policy, alert-then-halt ordering rationale
- `docs/runbook.md` — expand "Reconciliation mismatch" procedure with
  the new CLI + concrete steps to investigate each diff pattern

## Non-goals
- Auto-remediation (replay missing fills) — explicit non-goal. Human-
  required.
- Pre-trade reconciliation (check before every order) — candidate. For
  v0, daily is sufficient given daily rebalance cadence.
- Cross-broker reconciliation — we have one broker.

## Quality gates
- Format + lint + typecheck clean
- ≥10 new tests including the race-condition scenario
- `uv run alembic upgrade head` clean
- Pre-commit meta-review + **security-review subagent mandatory**
  (atomic kill-switch is a critical surface)
- Three-round review loop per `Effort: max`
- ADR 0015 committed

## Git workflow
1. Branch `feat/phase-15-reconciliation`
2. Commits:
   - `feat(trade): atomic kill switch`
   - `feat(trade): daily reconciliation + Reconciler class`
   - `feat(trade): reconciliations table migration`
   - `feat(cli): taq trade reconcile`
   - `test(trade): reconcile + kill-switch race + dry-run coverage`
   - `docs: ADR 0015 reconciliation and kill-switch; runbook expansion`
3. PR, CI green, squash-merge

## Handoff
Smoke test with MockBroker producing an intentional mismatch —
reproduce the kill-switch engagement + alert + audit sequence. Note
which order they fire in. Record in handoff for future debugging.
