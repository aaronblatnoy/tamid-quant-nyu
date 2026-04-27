# Phase 26 — Operator runbook expansion

## Metadata
- Effort: `standard`
- Depends on phases: 12, 15, 24
- Applies security-review: `no`
- Max phase runtime (minutes): 90
- External services: none

## Mission
Prior phases added runbook snippets piecemeal. This phase audits the
full runbook, fills gaps, converts vague prose into numbered step-by-
step procedures with *success criteria* and *rollback paths*, and marks
each procedure with a "last drilled" date. Procedures the harness
already exercised (backup drill in phase 25, reconciliation test in
phase 15) carry the phase date; others carry "never drilled" so the
operator knows what's load-bearing-but-unproven.

## Orientation
- `.build/handoffs/12_handoff.md` (alerts), `15_handoff.md`
  (reconciliation), `24_handoff.md` (deploy), `25_handoff.md` (backup)
- Current `docs/runbook.md`
- All ADRs for context on intent

## Service preflight
None.

## Acceptance criteria
- `docs/runbook.md` fully restructured with top-level sections:
  - Daily checklist (reviewed against phase 10's checks)
  - Incident response: Kill switch engagement, Reconciliation mismatch,
    AIS feed dead, Signal compute failed, Alerts not delivering, IBKR
    connection lost, Deploy failed mid-way
  - Deploy & rollback
  - Backup & restore (cross-link to phase 25 drill doc)
  - Database migrations (safety rules, break-glass flag)
  - Certificate renewal (Caddy's automatic LE; what to do if it stops)
  - Scheduler management (systemd unit operations)
  - Audit log: verifying the chain, exporting to parquet, investigating
    a chain break
  - Access & credentials: who has root on Hetzner, where env files live,
    how to rotate an API token
- Each procedure:
  - Numbered steps
  - Explicit success criteria (`verify that X returns Y`)
  - Explicit rollback path (or "irreversible; confirm before
    proceeding")
  - A **Last drilled:** metadata line with YYYY-MM-DD or `never`
- New doc `docs/drills/README.md` documenting the quarterly drill
  cadence and which procedures to exercise each quarter
- Tests: a linter script `docs/scripts/check_runbook.py` verifies:
  - Every section has a Last drilled line
  - No "TODO" or "XXX" or "TBD" markers
  - Every referenced CLI command exists (`uv run taq --help` parses;
    the linter introspects for the referenced subcommands)
  - Tests for the linter itself under `docs/scripts/test_check_runbook.py`
- `.github/workflows/ci.yml` runs the runbook linter on push to `main`
  and on PRs touching `docs/runbook.md`

## File plan
- `docs/runbook.md` — major restructure
- `docs/drills/README.md` — new
- `docs/scripts/check_runbook.py` — new
- `docs/scripts/test_check_runbook.py`
- `.github/workflows/ci.yml` — extend with the runbook lint job

## Non-goals
- Actually *performing* drills (that's ongoing ops, not a phase task).
  This phase only establishes the schedule + doc shape.
- Runbook localization / translation — out of scope
- Video/screencast walkthroughs — candidate

## Quality gates
- Runbook linter green
- `docs/runbook.md` reads coherently (spot-check by re-reading after
  writing; subjective but important)
- Pre-commit meta-review full loop (docs change is cross-cutting)

## Git workflow
1. Branch `docs/phase-26-runbook`
2. Commits:
   - `docs: restructure runbook with numbered procedures + drill metadata`
   - `docs: drill cadence doc`
   - `tools: runbook linter + CI wiring`
   - `test: runbook linter coverage`
3. PR, CI green, squash-merge

## Handoff
Table of procedures with their Last drilled state. Flag any procedure
that is load-bearing but has never been drilled as a candidate for
future drill phases.
