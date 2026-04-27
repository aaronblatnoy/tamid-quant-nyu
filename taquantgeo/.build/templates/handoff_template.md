# Phase NN handoff

## Status
One of: `completed` | `partially_completed` | `blocked` | `failed`

## What shipped
Bulleted list of concrete artifacts — files, tests, CLI subcommands, alembic
migrations, ADRs, docs updates. One line each.

## PR
- URL: https://github.com/sn12-dev/taquantgeo/pull/<N>
- CI status at merge: green|red (reason)
- Merge sha: <sha>

## Surprises / findings
API quirks discovered only when running against real endpoints, schema
gotchas in source data, bugs uncovered in prior phases' code. Anything
that would have cost a future phase hours to re-discover.

## Test count delta
- Before: X
- After: Y
- New tests (by name): [...]
- Any tests removed: [name + justification] (must be documented or phase
  quality-gate fails)

## Optional services not configured
List of `optional`-tier services whose env vars were missing during this
phase. Feature was built in disabled/mock mode; it auto-activates when the
env var is set later without any code change.

## Deferred / open questions
Items considered but not done in this phase; include reason (scope, waiting
on external signal, blocked on another phase).

## Ideas for future phases
Entries appended to `candidate_phases.md` during this phase, with pointers.

## For the next phase
Coordination-specific notes — data paths the next phase expects, env vars
the next phase assumes are set, anything non-obvious from the diff. Usually
empty.
