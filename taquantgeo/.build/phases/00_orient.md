# Phase 00 — Orient & preflight

## Metadata
- Effort: `standard`
- Depends on phases: none
- Applies security-review: `no`
- Max phase runtime (minutes): 15
- External services:
  - `Docker Desktop with WSL2 integration (required)` — must be able to run
    `docker ps` successfully. Without it, postgres/redis bring-up in later
    phases fails.
  - (all API tokens checked here as informational, not required)

## Mission
This phase does no building. It verifies the invariants the remaining
phases will depend on: working tree clean, on `main`, CI auth working,
test count matches the declared baseline (53 at commit `ccdefeb`), data
directory layout is as CLAUDE.md promises, and the local dev stack
(Docker + postgres + redis + required CLIs) is reachable. If any of these
fail at phase 00, later phases will crash mid-flight with a less clear
error — we catch the bad state here where the fix is a single operator
action, not a code rollback.

## Orientation (read before writing)
- `CLAUDE.md` (repo root) — conventions, storage tiers, data layout
- `~/.claude/CLAUDE.md` — cursor_apply routing, pre-commit meta-review,
  git discipline, commit trailers
- `README.md` — build plan, completed phases through Phase 1b core
- `docs/adrs/0001-stack-decisions.md`
- `docs/adrs/0002-gfw-voyages-as-historical-source.md`
- `docs/adrs/0003-events-api-for-freshness.md`
- `docs/RESEARCH_LOG.md`
- `docs/runbook.md`
- `docs/data_model.md`
- `packages/ais/src/taquantgeo_ais/gfw/events.py` — the quality bar
- `.build/README.md` — harness usage
- `.build/templates/phase_template.md` and `handoff_template.md`

No prior handoffs yet.

## Service preflight
- Required check: `docker ps` must exit 0. If not (Docker Desktop stopped,
  WSL2 integration disabled, daemon crashed), append entry to
  `.build/manual_setup_required.md` with guidance to enable Docker Desktop
  WSL2 integration, write handoff `status: blocked`, exit.
- Required check: `gh auth status` must exit 0. If not, write manual-setup
  entry for `gh auth login`.
- Informational: record which of {GFW_API_TOKEN, AISSTREAM_API_KEY,
  DATABASE_URL, R2_ACCESS_KEY_ID, IBKR_HOST, DISCORD_WEBHOOK_URL,
  SENTRY_DSN} are set into the handoff so future phases can see the
  provisioning state at time of the run.

## Acceptance criteria
- `git status --porcelain` prints nothing (working tree clean)
- `git rev-parse --abbrev-ref HEAD` prints `main`
- `git rev-parse HEAD` recorded in handoff; matches
  `build_state.json["commit_baseline"]` after this phase
- `docker ps` exits 0
- `gh auth status` exits 0
- `uv sync` exits 0 (full workspace installable at this commit)
- `uv run pytest -m "not integration and not live" --collect-only -q`
  reports `53 tests collected` (the current baseline at commit `ccdefeb`).
  If higher, set `build_state.json["test_count_baseline"]` to the new
  number and record the delta in the handoff. If lower, HALT with
  `status: failed` (tests have regressed outside the harness).
- `uv run ruff format --check .` clean
- `uv run ruff check .` clean
- `uv run basedpyright` 0 errors
- Expected data subdirectories exist (or are created as empty dirs with a
  `.gitkeep`-style marker): `data/raw/gfw/{anchorages,voyages,sar_vessels,
  sar_infrastructure,distance_from_port,cvp}/`, `data/raw/ais_live/`,
  `data/processed/voyages/`, `data/processed/events/`. The phase may
  `mkdir -p` any missing; it must not write a parquet or CSV.

## File plan
No source file mutations. This phase:
- Writes `.build/handoffs/00_handoff.md` per handoff_template.md
- Writes `.build/reports/00_orient_<timestamp>.log` with verbatim output
  from each preflight check (redirected by the driver, but the phase agent
  should also include a bullet-form summary in the handoff)
- Creates missing data-directory markers (empty dir creation only; no
  parquet, no CSV). If any were created, list them in the handoff under
  "What shipped".

## Non-goals
- No dependency upgrades — use whatever `uv.lock` pins as-of `ccdefeb`.
  That's phase 99's audit territory if rot is found.
- No env-var provisioning — this phase observes, it does not provision.
- No test additions — the harness should start with exactly the baseline
  test count. (Phase 01 adds the first new tests.)
- Does NOT run integration or live tests. Baseline is the non-integration
  non-live slice.

## Quality gates
- Lint, format, typecheck all clean (baseline should already satisfy;
  failure here means the harness started on a broken main)
- Test baseline preserved (see acceptance criteria)
- Handoff file written at correct path

## Git workflow
No git branch, no PR — this phase is read-only except for the handoff and
optional data-dir markers. If any data-dir markers are created, they are
committed directly to main with message
`chore(build): seed data/ subdirectory markers for harness preflight`
and Co-Authored-By trailer. Otherwise no commit.

## Handoff
`.build/handoffs/00_handoff.md` includes:
- Status (`completed` unless preflight found a blocker)
- Exact commit SHA of `main`
- Test count observed
- Environment-variable provisioning snapshot (which env vars are set; do
  NOT print their values — just "set" or "unset")
- Any data directory markers created
- Note that `build_state.json` was updated with `commit_baseline` and
  `test_count_baseline`
