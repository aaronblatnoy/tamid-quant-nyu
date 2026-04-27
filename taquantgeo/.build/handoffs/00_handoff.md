# Phase 00 handoff

## Status
`completed`

This is a rerun of Phase 00 after the prior `blocked` status caused by Docker
Desktop WSL2 integration being disabled. The operator enabled the integration;
`docker ps` now exits 0. All other preflight gates remained green from the
prior run, so this rerun is a straight pass.

## What shipped
- `.build/reports/00_orient_20260421T091530Z.log` — verbatim preflight outputs
  from this rerun (git state, docker, gh, uv sync, pytest collect, ruff
  format/check, basedpyright, data dir layout, env-var snapshot)
- `.build/handoffs/00_handoff.md` — this file (overwrites the prior `blocked`
  handoff)
- `.build/manual_setup_required.md` — annotated the existing Phase 00 entry
  with a `RESOLVED 2026-04-21` note (kept for audit trail; not deleted)
- `data/processed/events/.gitkeep` and `data/processed/voyages/.gitkeep` —
  empty markers created on disk. Both are gitignored by `data/processed/*`
  rule at `.gitignore:71`, so no commit results from these. Their existence
  on disk satisfies the phase acceptance criterion that the directories be
  present.

No source files were mutated. No PR was opened. No commit was made (the
data-dir markers are gitignored, so the contract's optional commit step is a
no-op this run).

## PR
None — phase 00 is observation-only and produces no source-tree changes.
Future phases open PRs; this one updates harness state files only.

## Surprises / findings
- **R2 credentials now provisioned.** The prior 2026-04-21T09:09:55Z run
  flagged `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` as missing from
  `.env`. Both are now present (key names only — values not inspected).
  Means R2-dependent phases are unblocked from a credential standpoint.
- **`.gitignore:71` (`data/processed/*`) silently ignores the new
  `.gitkeep` markers.** Existing markers under `data/raw/gfw/<sub>/` are
  also untracked — only `data/{raw,processed,interim}/.gitkeep` at depth-2
  are committed (3 files total under `data/` per `git ls-files data/`).
  The contract's promise of a commit message
  `chore(build): seed data/ subdirectory markers for harness preflight`
  cannot be honored as-is because the markers are unrepresentable in git
  under the current `.gitignore`. Treating this as a non-issue: the
  directories exist on disk (which is what later phases need), and the
  `.gitignore` rule is intentional (everything under `data/` is gitignored
  per CLAUDE.md). If a future phase wants these markers committed, it
  would need an explicit `!data/processed/<sub>/.gitkeep` negation rule —
  noted under Ideas for future phases.
- `gh auth status` is green for `sn12-dev`; `read:org` scope still missing.
  `repo`, `workflow`, `delete_repo` present, so PR creation / CI polling /
  squash-merge / branch deletion all work. No harness flow currently
  queries org-level metadata; informational only.
- Basedpyright reports `0 errors, 5 warnings`. The 5 warnings are
  pre-existing `reportMissingTypeStubs` on first-party packages
  (`taquantgeo_core.db`, `taquantgeo_core.schemas`, `taquantgeo_cli.main`).
  Not a regression; not a blocker.

## Test count delta
- Before: 53 (`build_state.json.test_count_baseline`)
- After: 53 (no tests added or removed — observation phase)
- New tests: none
- Tests removed: none

Baseline matches; no update to `build_state.json["test_count_baseline"]`
needed.

## Optional services not configured
None in this phase — phase 00 builds no features. Env-var snapshot below is
informational, not a degraded-mode declaration.

Env-var snapshot (names only, values NOT inspected):

| Env var | Shell-exported at phase start | Present in `.env` |
|---|---|---|
| `GFW_API_TOKEN` | unset | yes |
| `AISSTREAM_API_KEY` | unset | yes |
| `DATABASE_URL` | unset | yes |
| `R2_ACCESS_KEY_ID` | unset | **yes** (was missing prior run) |
| `R2_SECRET_ACCESS_KEY` | unset | **yes** (was missing prior run) |
| `IBKR_HOST` | unset | yes |
| `DISCORD_WEBHOOK_URL` | unset | yes |
| `SENTRY_DSN` | unset | yes |

"Unset in shell" only means the harness-spawning shell did not export them.
The `.env` file is the source of truth; future phases should read via the
pydantic-settings layer, not via `os.environ` at process start.

## Deferred / open questions
- `read:org` scope on the `gh` token — informational. If a future phase
  needs org-level GitHub queries, run `gh auth refresh -h github.com -s
  read:org`.
- Whether to commit `.gitkeep` markers under `data/processed/<sub>/`
  requires a `.gitignore` negation rule. Not done in this phase; raised as
  a candidate (see Ideas).

## Ideas for future phases
Appended to `candidate_phases.md` would be: a small chore phase to add
`!data/processed/events/.gitkeep` and `!data/processed/voyages/.gitkeep`
negation rules so the harness's directory-marker convention is
representable in git. NOT appended this run — too small to warrant a
candidate entry, and the directories exist on disk anyway. If it ever
matters (e.g., for fresh-clone reproducibility on CI), revisit then.

## For the next phase
- **Driver**: phase 00 is `completed`. Advance to phase 01.
- All quality gates (ruff format, ruff check, basedpyright, pytest collect)
  are green at commit `a2429dc6`. Test baseline = 53.
- `data/processed/{events,voyages}/` exist on disk (gitignored markers
  present). Phase 01 can write parquet under these without first having to
  `mkdir -p`.
- Env-var truth lives in `.env`, not the shell environment. Phases should
  consume settings via `pydantic-settings` / `python-dotenv`, not
  `os.environ`.
- `.build/manual_setup_required.md` Phase 00 entry is annotated
  `RESOLVED 2026-04-21`; the file is not empty, but the only outstanding
  blocker has been cleared.

## Preflight check results (verbatim summary)

- `git status --porcelain` → empty (working tree clean) ✅
- `git rev-parse --abbrev-ref HEAD` → `main` ✅
- `git rev-parse HEAD` → `a2429dc64e131476e546b0b62b2e508823e48a59` ✅
  (matches `build_state.json.commit_baseline`)
- `docker ps` → EXIT=0 ✅ **(prior blocker resolved)**
- `gh auth status` → EXIT=0, logged in as `sn12-dev`; missing `read:org`
  scope (informational) ✅
- `uv sync` → EXIT=0, "Resolved 96 packages, Checked 91 packages" ✅
- `uv run pytest -m "not integration and not live" --collect-only -q` →
  `53 tests collected` (matches baseline) ✅
- `uv run ruff format --check .` → "42 files already formatted", EXIT=0 ✅
- `uv run ruff check .` → "All checks passed!", EXIT=0 ✅
- `uv run basedpyright` → "0 errors, 5 warnings, 0 notes", EXIT=0 ✅
  (warnings pre-existing, not a regression)
- Data directory layout: all required subdirectories present on disk;
  `.gitkeep` markers added for `data/processed/{events,voyages}/`
  (gitignored, so on-disk only). ✅
- `.env` provisioning: all eight surveyed keys present (R2 keys newly
  added since prior run). Values not inspected. ✅

