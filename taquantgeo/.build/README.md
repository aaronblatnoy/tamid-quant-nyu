# Autonomous build harness

Self-driving build for TaQuantGeo. Given the harness state at `main@ccdefeb`
(phase 0 + 1a + 1b core already merged), running `python3 .build/run.py`
autonomously executes the remaining 33 phases — vessel classifier through
production deploy, audit, release, and final holistic audit — and merges
each PR on green CI without human review pauses.

## Philosophy

Every phase file under `.build/phases/` is a literal contract the phase
agent treats as law. Acceptance criteria are testable shell commands.
Quality gates are non-negotiable: lint + typecheck + tests green, meta-
review loop executed when scope gate met, security-review additionally
when the phase frontmatter declares it, test count never drops.

The harness halts ONLY on:
- A phase reporting `status: failed` after 3 consecutive red CI cycles
  with fix attempts between (phase-level halt — see
  `reports/NN_failure.md`)
- A `required`-severity external service being unconfigured (harness
  writes `manual_setup_required.md`, exits 0; user provisions and re-runs)
- Phase 07 IC analysis reporting no-edge across all horizons — the
  whole harness halts because the downstream work on a no-edge signal
  is worthless

No cost cap. `cost_ledger.json` is telemetry only. No human review
pauses anywhere.

## Run it

```bash
python3 .build/run.py
```

## Flags

```
--dry-run                  List phases in order; do not invoke Claude
--from-phase NN            Start at NN; skip earlier completed phases
--only NN                  Run exactly one phase, then exit
--skip NN[,MM,...]         Skip these phases for this run
--resume                   Default; implicitly continues from state
```

`NN` accepts letter-suffixed sub-phase numbers like `23b`, `23c`.

## What happens on a run

1. Preflight — verify `claude` on PATH, working tree clean, on `main`,
   pull latest, drift-check against state baseline
2. Bootstrap state — first run records `commit_baseline` +
   `test_count_baseline` (53 at `ccdefeb`)
3. Re-attempt any previously blocked phases — their manual-setup
   dependencies may have been provisioned since the last run
4. Iterate phases in numeric order (00 → 01 → … → 23c → 24 → 28 → 90 → 99)
5. Phase 90 can promote candidate phases into the 91-98 reserved slot;
   the driver re-scans and runs newly promoted phases before phase 99
6. Exit cleanly at completion, or on clean halt (blocked phase /
   IC gate fail-fast)

## Committed vs gitignored

Committed (part of the repo):
- `.build/phases/*.md` — the contracts
- `.build/templates/*.md`
- `.build/candidate_phases.md` — harness-written candidates
- `.build/archived_candidates.md`
- `.build/triage_report.md` — phase 90 output
- `.build/run.py`
- `.build/README.md`
- `.build/CHANGELOG.md`

Gitignored (ephemeral, per-machine):
- `.build/handoffs/NN_handoff.md` — phase agent outputs
- `.build/reports/*.log` — subprocess stdout/stderr captures
- `.build/build_state.json` — persistent state
- `.build/cost_ledger.json` — telemetry
- `.build/manual_setup_required.md` — operator guidance

## Manual-setup items worth provisioning BEFORE kickoff

These are `required`-severity services a later phase will halt on if
unset. Provision them up-front if you want an uninterrupted run:

- **Hetzner CX32 VPS + SSH key** (phase 24) — €6/mo, ~10 min. Needed
  for backend deploy
- **Neon production branch DATABASE_URL** (phase 24) — ~5 min. Needed
  for production DB
- **Cloudflare R2 bucket + access key pair** (phase 25) — ~5 min.
  Needed for backups + cold archive

These are `optional`. Features ship in disabled / mock mode if unset
and auto-activate when env vars land later:

- `DISCORD_WEBHOOK_URL` (phase 12 alerts) — FileSink + StdoutSink
  always work
- `SENTRY_DSN` (phase 24 observability) — shipped-wired-dormant
- `VERCEL_TOKEN` (phase 24 frontend) — Vercel's GitHub integration
  auto-deploys from main without a token
- `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` / `IBKR_ACCOUNT` (phases
  14, 22) — MockBroker fallback is fully functional for backtest,
  risk-gate, reconciliation, UI. When real creds land, `make_broker()`
  switches with zero code changes.

Already set (do not re-provision): `GFW_API_TOKEN`, `AISSTREAM_API_KEY`,
`GOOGLE_APPLICATION_CREDENTIALS`, `GCP_PROJECT_ID`, `DATABASE_URL`
(local), `REDIS_URL` (local).

## If it halts

Read `manual_setup_required.md`. Provision the listed service(s). Re-run
`python3 .build/run.py`. The harness re-attempts blocked phases before
advancing.

## If a phase fails (non-blocked)

Open `reports/NN_failure.md` for the path to the subprocess log. Inspect.
If the failure is a CI flake, re-running the harness is usually enough
(`python3 .build/run.py` picks up where it left off — state is in
`build_state.json`). If the failure is a real bug in the phase contract
or in the generated code, fix it manually on a branch, merge, then resume.

## Inspecting a run

- `cat .build/build_state.json | jq .` — completed / blocked / failed
  phases, test-count baseline, phase-run log
- `ls -lt .build/reports/` — subprocess logs, newest first
- `ls -lt .build/handoffs/` — agent-written handoffs
- `cat .build/handoffs/NN_handoff.md` — per-phase result narrative

## Skipping phases

`--skip` is for one-off runs. To durably skip a phase, edit the phase
file to document the skip and mark the phase `deleted` in a
`skipped_phases` array in state. Currently no phase is marked skip.

## Run exactly one phase

```bash
python3 .build/run.py --only 00
```

Useful for debugging a phase contract or for re-running a phase after
manually fixing an issue.

## Phase 07 fail-fast

If the IC analysis phase halts the harness with a no-edge verdict, read
`reports/ic_analysis.md`. The signal in phase 04 needs revision before
any downstream work is worth building. Treat this as a real signal that
the project's premise needs re-examination — do NOT lower the threshold
to let the harness continue. The threshold IS the gate.

## Codex / external audit

Everything the harness produces — phase contracts, subprocess logs,
handoffs, PRs, tests — is auditable. Codex is expected to audit all of
it. Contracts that seem loose will be flagged and should be fixed on a
follow-up harness run (see phase 90 candidate triage).
