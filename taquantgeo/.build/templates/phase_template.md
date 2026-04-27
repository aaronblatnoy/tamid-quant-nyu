# Phase NN — <slug>

## Metadata
- Effort: `standard` | `max` — when `max`, engage extended thinking for every
  architectural decision; use `think hard` or `think harder` internally;
  iterate the pre-commit meta-review loop to 3 rounds even if round 1 only
  surfaces minor findings; do not shortcut
- Depends on phases: [numbers, or "none"]
- Applies security-review: `yes` | `no`
- Max phase runtime (minutes): <N>
- External services:
  - `SERVICE_NAME (required)` — halt if env var missing
  - `SERVICE_NAME (optional)` — ship feature in disabled/mock mode if missing
  - (or "none")

## Mission
One paragraph. Why this phase exists, tied to the thesis: "does this move us
toward a backtested-and-validated TD3C tightness signal that can be safely
traded with real capital?" If the answer isn't obvious, rewrite.

## Orientation (read before writing)
Explicit files to read. Reference the latest prior handoff by path. Cite the
ADRs and the representative quality bar (`packages/ais/src/taquantgeo_ais/
gfw/events.py` — module docstring with discovered quirks, defensive
pagination, pytest-recording VCR cassettes, typed models).

## Service preflight
For each `required` service whose env var is missing:
  1. Append an entry to `.build/manual_setup_required.md` with: phase number,
     service name, env var name, where to obtain, estimated setup time.
  2. Write handoff with `status: blocked`.
  3. Exit cleanly. The driver will log and halt after writing the alert.

For each `optional` service whose env var is missing:
  1. Proceed. Build the feature in disabled/mock mode behind the abstraction
     pattern defined in the phase.
  2. Log a one-line warning at startup.
  3. Document degradation in the handoff under "Optional services not
     configured" so the user knows which surfaces are dormant.

## Acceptance criteria
Each bullet a testable assertion with a concrete shell command or code
invocation — no vibes. Example:
- `uv run taq <cmd> --help` exits 0
- `uv run pytest packages/<pkg>/tests/test_<x>.py -q` all green
- `<path>.parquet` exists with `>= N` rows and columns `[a, b, c]`

## File plan
Enumerate every file to create or modify, with a one-line justification
each. Include tests, docs, alembic migrations, CLI registration, fixtures.

## Non-goals
MANDATORY. What this phase does NOT do, and for each item the phase number
(or `candidate_phases.md` entry) that owns it. Scope creep is the failure
mode this section prevents.

## Quality gates (must pass before PR opens)
- `uv run ruff format --check .` clean
- `uv run ruff check .` clean
- `uv run basedpyright` 0 errors
- `uv run pytest -m "not integration and not live"` — all green AND test
  count has not decreased from `build_state.json["test_count_baseline"]`.
  Update the baseline after this phase passes.
- New tests must be behavior tests, not decoration
- Pre-commit meta-review from `~/.claude/CLAUDE.md` run when the scope gate
  is met (>1 file OR >20 lines OR touches trade/auth/deploy)
- Security-review subagent additionally if `Applies security-review: yes`
- Docs touched when public surface changes (CLAUDE.md / README.md /
  RESEARCH_LOG.md / relevant ADR)
- Write new ADR at `docs/adrs/NNNN-<slug>.md` if the phase makes a
  non-obvious architectural decision

## Git workflow
1. `git checkout main && git pull`
2. `git checkout -b feat/phase-NN-<slug>` (or `fix/…` if bugfix-only)
3. Atomic conventional commits; Co-Authored-By trailer on each per
   `~/.claude/CLAUDE.md`
4. `gh pr create` with a specific title + body explaining WHAT and WHY
5. `gh pr checks <PR> --watch` — wait for green
6. Three consecutive red CI cycles with fix attempts between → HALT, write
   `.build/reports/NN_failure.md` describing what was tried; DO NOT merge
7. On green → `gh pr merge --squash --delete-branch`
8. `git checkout main && git pull`

## Handoff (mandatory — write before exit)
`.build/handoffs/NN_handoff.md` conforming to `handoff_template.md`. Must
include: status, shipped artifacts, PR URL + merge sha, surprises, test
count delta, optional services not configured, deferred items, ideas for
future phases, notes for the next phase.

## If you spot an improvement mid-phase
Before expanding beyond the File plan, read every other phase file under
`.build/phases/` AND `.build/candidate_phases.md`.
- If the idea is already scoped elsewhere: DO NOT implement; note in this
  handoff under "Ideas for future phases" with a pointer.
- If genuinely new: append a stub entry to `candidate_phases.md` and note in
  the handoff — still DO NOT implement in this phase.
The only things that land here are what the File plan enumerates.
