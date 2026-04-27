# Phase 23c — Local end-to-end integration test

## Metadata
- Effort: `max`
- Depends on phases: 04, 06, 07, 08, 09, 15, 16
- Applies security-review: `no`
- Max phase runtime (minutes): 240
- External services:
  - `DATABASE_URL (required)` — brought up via docker-compose

## Mission
Every phase before this has tested its component in isolation. Phase 23c
proves the components compose. Spin up local docker-compose postgres +
redis, run the entire pipeline end-to-end against real data on disk
(voyages on disk, price backfill, all phases' CLI entry points), and
assert each step produces expected outputs. If any step fails, the
integration fails — no skips, no degradations. This is the GATE before
we touch production deploy (phase 24). It's a real-money system; we do
not deploy something we haven't fully integration-tested locally.

## Orientation
- All handoffs through phase 23b (don't re-read all; the agent reads
  the most recent ~2 and uses them to find links back)
- Every CLI entry point shipped so far
- `infra/docker-compose.yml` — the local stack

## Service preflight
- Requires Docker. If `docker ps` fails, halt with manual-setup entry
  (same as phase 00).

## Acceptance criteria
- NEW directory `tests/integration_e2e/` (top-level repo tests; not
  inside a package)
- `tests/integration_e2e/test_pipeline_end_to_end.py` with ONE mega-
  test marked `@pytest.mark.integration` (so default CI skips it;
  runs only when invoked with `-m integration`). The test:
  1. Brings up docker-compose (postgres + redis) via `testcontainers-
     python` (or a `docker compose` subprocess wrapper, whichever is
     cleaner — document choice in the ADR)
  2. Runs `alembic upgrade head`
  3. Asserts a small but real voyages CSV is on disk under
     `data/raw/gfw/voyages/` (fails with clear message if absent;
     the test does NOT download one)
  4. Executes each step via the actual CLI:
     - `uv run taq gfw ingest-voyages --voyages-csv ... --route td3c`
     - `uv run taq gfw classify-vessels --voyages-dir ...`
     - `uv run taq gfw compute-distances --voyages-dir ...`
     - `uv run taq gfw ingest-sar --since ...` (if SAR data present;
       else xfail with explicit marker)
     - `uv run taq signals compute-tightness --as-of ...`
     - `uv run taq prices backfill --since 2020-01-01 --ticker FRO
       DHT` (mocked yfinance — cassette under tests/integration_e2e/
       cassettes/)
     - `uv run taq signals ic --since 2020-01-01 --out <tmp>/ic.md`
     - `uv run taq backtest run --config tests/integration_e2e/
       v1_config.json --out <tmp>/backtest/`
     - `uv run taq backtest wfcv --config ... --out <tmp>/wfcv.md`
     - `uv run taq trade reconcile --dry-run` (MockBroker)
     - `uv run taq trade audit verify`
  5. After every step asserts:
     - Exit code 0 for non-gate steps; expected exit code 10 for IC
       gate if the integration fixtures are deliberately constructed
       to pass. (If IC fails on real data, that's the fail-fast at
       phase 07; the integration test uses a crafted signal that is
       KNOWN to pass, so IC should pass here.)
     - Expected output file(s) exist with minimum row counts
     - Postgres tables have the expected row counts
     - Audit chain verifies intact
  6. Tears down the docker-compose stack
- GitHub Actions workflow `local-e2e.yml` added: runs on PRs touching
  `packages/**` AND on manual dispatch; runs the integration marker
  against a service-container postgres; 20-min timeout
- Tests within the e2e test are NOT split into smaller units — the
  point is ONE end-to-end run that exercises composition
- Fixtures for e2e deliberately small so run completes in < 10min
- Phase writes a runbook section: "Running e2e locally" with explicit
  commands

## File plan
- `tests/integration_e2e/conftest.py` — docker-compose bring-up/down
  fixtures, testcontainers setup
- `tests/integration_e2e/test_pipeline_end_to_end.py`
- `tests/integration_e2e/cassettes/` — VCR for yfinance
- `tests/integration_e2e/fixtures/` — small voyages CSV, SAR CSV, etc.
- `tests/integration_e2e/v1_config.json` — backtest config
- `.github/workflows/local-e2e.yml` — new
- `pyproject.toml` (root) — add testcontainers to dev deps if used
- `docs/adrs/0021-local-e2e-integration-test.md` — NEW ADR: why one
  mega-test instead of many; docker strategy; why gate before deploy
- `docs/runbook.md` — "Running local E2E" section
- `CLAUDE.md` — note the e2e test in Useful commands

## Non-goals
- Performance benchmarking — separate candidate
- Chaos testing (kill postgres mid-run) — candidate
- Multi-node / distributed tests — not v0

## Quality gates
- Format + lint + typecheck clean
- `uv run pytest -m integration tests/integration_e2e/ -q` green
  locally and in the dedicated CI workflow
- Pre-commit meta-review full loop
- Three-round review loop per `Effort: max`
- ADR 0021 committed

## Git workflow
1. Branch `feat/phase-23c-local-e2e`
2. Commits:
   - `build: testcontainers dev dep`
   - `test: end-to-end integration covering full pipeline`
   - `ci: dedicated local-e2e workflow on PRs`
   - `docs: ADR 0021 local e2e + runbook`
3. PR, CI green (including new e2e workflow), squash-merge

## Handoff
End-to-end run duration (for baseline). List of every CLI step and its
output row count. Any step that surprised (e.g., a race-condition
between alembic + signal upsert) — those are bugs in earlier phases
and a candidate follow-up should be noted.
