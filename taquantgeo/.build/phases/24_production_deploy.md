# Phase 24 — Production deploy

## Metadata
- Effort: `max`
- Depends on phases: 11, 16, 23c
- Applies security-review: `yes`
- Max phase runtime (minutes): 240
- External services:
  - `HETZNER_SSH_KEY (required)` — SSH key paired to a Hetzner CX32 VPS
    the user has provisioned. If missing, append manual-setup entry
    (Hetzner signup + CX32 instance €6/mo + SSH key upload) and halt.
  - `NEON_PROD_DATABASE_URL (required)` — a Neon production branch URL
    distinct from the dev DATABASE_URL. Missing → manual-setup
    (Neon signup + production branch + copy URL) and halt.
  - `SENTRY_DSN (optional)` — observability activates when present.
  - `VERCEL_TOKEN (optional)` — Vercel's GitHub integration deploys the
    frontend automatically from main; we don't need a token at CI time
    for that path. Only needed for programmatic deploys.
  - `DISCORD_WEBHOOK_URL (optional)` — alerts dormant until set.

## Mission
Backend + frontend to production. Hetzner CX32 runs Docker Compose for
the FastAPI + scheduler + AIS streamer. Vercel deploys Next.js from
main via its GitHub integration (no token needed). Alembic auto-migrates
on deploy with a safety check that refuses to apply a down-migration
spanning more than three tables. Caddy handles TLS. Sentry is optional
but shipped-wired so it lights up if DSN provided. This phase does NOT
flip any real-money switches — the backend serves the paper-trading
MockBroker path until IBKR env is set on the server.

## Orientation
- `.build/handoffs/11_handoff.md`, `16_handoff.md`, `23c_handoff.md`
- `infra/docker-compose.yml` — local reference
- `infra/alembic/` — migrations
- `docs/runbook.md` — "Deploy" section to expand
- `docs/adrs/0001-stack-decisions.md` — hosting choices
- `packages/api/` — app being containerized

## Service preflight
Per the three required services above. Also: GitHub Actions repo secrets
must be settable (`gh secret set ...`) — if `gh auth status` fails, halt.

## Acceptance criteria
- `infra/Dockerfile` — multi-stage build:
  - Base: python:3.12-slim-bookworm
  - Stage 1 installs uv + pyproject + lock; `uv sync --frozen`
  - Stage 2 copies source + venv; non-root `taq` user; `CMD uvicorn ...`
  - Build target arg selects entrypoint (api | scheduler | ais-streamer)
- `infra/docker-compose.prod.yml` — production compose:
  - api, scheduler, ais-streamer services sharing the built image with
    different entrypoints
  - Caddy sidecar for TLS + reverse proxy
  - Restart policies + resource limits
  - Env file mounted from `/opt/taquantgeo/.env`
- Caddy config with Let's Encrypt automatic TLS for the production
  domain. Document the domain name to set in the handoff and the runbook.
- GitHub Actions `.github/workflows/deploy-backend.yml`:
  - Trigger on push to tags `v*` (release deploys) + manual dispatch
  - Build image, push to GHCR
  - SSH to Hetzner via `HETZNER_SSH_KEY` (stored as GH secret), pull
    image, `docker compose -f docker-compose.prod.yml up -d`
  - Alembic runs INSIDE the container on startup with safety check:
    - `class SafeMigrator:` computes the diff between
      current_version and target. Refuses to apply if the diff
      includes > 3 down-migrations OR drops > 1 table unless
      `ALEMBIC_ALLOW_DESTRUCTIVE=true` is set in env (documented as
      operator break-glass only)
  - On failure: workflow rolls back by redeploying the previous image
    tag; alerts via Discord if configured
- Vercel config:
  - `web/vercel.json` with framework preset, env var scaffolding
  - `NEXT_PUBLIC_API_URL` wired to the production backend domain
  - Preview deploys on PRs, production on main (Vercel default)
  - Document in the handoff which env vars the user must set in
    Vercel's UI (or via `vercel env add` if VERCEL_TOKEN provided)
- Sentry wiring (backend + frontend):
  - Initializes when DSN present; silent when absent
  - Python: `sentry-sdk` with a `before_send` that strips env vars,
    settings fields, and anything with "token" / "key" / "secret" /
    "password" / "webhook" in its name (case-insensitive). Unit tests
    for the scrubber
  - Frontend: `@sentry/nextjs` with similar redactor; `ignoreErrors`
    includes browser-specific noise (ResizeObserver, etc.)
- Tests:
  - `infra/tests/test_dockerfile_builds.sh` — smoke: `docker build`
    succeeds for each target arg
  - `infra/tests/test_safe_migrator.py` — synthetic migration diffs;
    rejects destructive diffs without env flag; accepts with flag
  - `test_sentry_scrubber_redacts_webhook_urls`
  - `test_sentry_scrubber_redacts_env_keys_with_token_pattern`
  - `test_sentry_scrubber_passes_through_clean_breadcrumbs`
- Deploy workflow dry-runs green. One real deploy executed during the
  phase against a staging tag (e.g., `v0.0.99-rc1`) — document the
  result in handoff. If Hetzner or Neon isn't provisioned, the phase
  halts per preflight.

## File plan
- `infra/Dockerfile`
- `infra/docker-compose.prod.yml`
- `infra/caddy/Caddyfile`
- `infra/scripts/deploy.sh` — entrypoint the GH Actions job SSHes to
  run; idempotent, logs every step
- `infra/migrations_safety.py` (or
  `packages/core/src/taquantgeo_core/migrations_safety.py`) —
  SafeMigrator
- `.github/workflows/deploy-backend.yml`
- `infra/tests/test_dockerfile_builds.sh`
- `infra/tests/test_safe_migrator.py`
- `packages/core/src/taquantgeo_core/observability.py` — Sentry init
  + scrubber (backend)
- `web/src/lib/sentry.ts` — Sentry init (frontend)
- `web/sentry.{client,server,edge}.config.ts` — Next.js Sentry wiring
- `docs/adrs/0022-production-deploy.md` — NEW ADR: Hetzner+Vercel+Neon
  split, Caddy + LE, alembic safety, Sentry optional-wire pattern,
  GHCR vs Docker Hub
- `docs/runbook.md` — expand "Deploy" section: tag → deploy flow,
  rollback, Hetzner SSH access, Caddy cert troubleshooting, Neon
  branch strategy, Vercel env var list

## Non-goals
- Multi-region deploy — candidate
- Blue/green deploys — candidate (compose swap is acceptable for v0)
- Canary routing — candidate
- Custom monitoring dashboards (Grafana etc.) — candidate
- Real-money IBKR wiring on the server — operator toggle, not a code
  change. Documented in runbook.

## Quality gates
- Format + lint + typecheck clean
- Dockerfile builds cleanly for all targets
- SafeMigrator tests + Sentry scrubber tests green
- Deploy workflow passes on at least one real tag during the phase
- Pre-commit meta-review + **security-review subagent mandatory**
  (secrets handling, SSH surface, scrubber completeness)
- Three-round review loop per `Effort: max`
- ADR 0022 committed

## Git workflow
1. Branch `feat/phase-24-production-deploy`
2. Commits:
   - `build(infra): multi-stage Dockerfile + prod compose`
   - `build(infra): Caddyfile with Let's Encrypt TLS`
   - `feat(core): SafeMigrator + observability scrubber`
   - `ci: deploy-backend workflow on tags + manual dispatch`
   - `feat(web): Sentry wiring (Next.js)`
   - `build(web): Vercel config`
   - `test: SafeMigrator + scrubber + Dockerfile build smoke`
   - `docs: ADR 0022 production deploy; runbook expansion`
3. PR, CI green, squash-merge
4. Create a staging tag `v0.0.99-rc1`, run the deploy workflow, verify
   https://<domain>/healthz returns 200, then proceed. Leave the staging
   tag in place for reference.

## Handoff
Production domain, IP of the Hetzner box (redacted ok; document out-
of-band), Neon branch name, Vercel project URL. List of env vars
the user needs to set in each location (Hetzner `.env`, Vercel UI,
GH Actions secrets). Screenshot/log excerpt of successful deploy.
Confirmation that Sentry lights up when DSN provided (test with
staging DSN during phase).
