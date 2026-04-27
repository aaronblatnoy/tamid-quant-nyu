# CLAUDE.md — TaQuantGeo

## What this is

Quant geospatial trading platform. Vertical 1: VLCC freight tightness signal on the TD3C route (Persian Gulf → China). The system ingests AIS, classifies voyages as loaded or ballast, computes a daily tightness ratio, compares to market-implied tightness via shipping-equity prices, and generates trades through Interactive Brokers.

## Stack

- Python 3.12, uv workspace (9 packages under `packages/`)
- Postgres on Neon (operational state) + Parquet on Cloudflare R2 (cold archive) + DuckDB (analytical)
- AISStream.io (live AIS) + Global Fishing Watch BigQuery (historical)
- Custom backtester in pandas/polars
- Interactive Brokers via `ib_insync` for execution
- FastAPI backend, Next.js 15 + shadcn/ui + DeckGL + TradingView Lightweight Charts on the frontend
- Hetzner CX32 backend host + Vercel frontend

## Repo layout

- `packages/core/` — shared models, config, DB session
- `packages/ais/` — AIS ingestion (live streamer + GFW historical pipeline under `taquantgeo_ais.gfw`)
- `packages/signals/` — tightness signal, baselines, signal-vs-price comparison
- `packages/prices/` — equity-price ingest (yfinance → Postgres prices table)
- `packages/backtest/` — custom backtester + reports
- `packages/trade/` — IBKR + risk gate + reconciliation + audit log
- `packages/api/` — FastAPI for the dashboard
- `packages/jobs/` — scheduled pipelines (APScheduler)
- `packages/cli/` — `taq` CLI
- `web/` — Next.js dashboard (separate workspace)
- `infra/` — Dockerfile, docker-compose, alembic migrations, Caddy config
- `docs/` — architecture, data model, runbook, ADRs, RESEARCH_LOG

## Conventions

- **Python**: 3.12. ruff format + lint, basedpyright `strict`. Line length 100.
- **Pydantic / SQLAlchemy model files**: do NOT use `from __future__ import annotations`. Both frameworks resolve annotations at class-definition time and need the types available in module globals (`datetime`, `AisDimension`, etc.). Other modules can and should use `from __future__ import annotations`.
- **Commits**: conventional (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`, `ci:`, `build:`). Enforced by commit-msg hook.
- **Branches**: `feat/short-description`, `fix/short-description`. PR required to `main`. Squash merge.
- **Tests**: every new module gets a test file. Markers:
  - `@pytest.mark.integration` — requires postgres/redis (CI runs these against service containers)
  - `@pytest.mark.live` — hits live external APIs (CI skips these)
  - `@pytest.mark.slow` — slow tests (skip with `-m 'not slow'`)
- **Real-money discipline**: changes to `packages/trade/` REQUIRE a passing reconciliation test against a paper-trading IBKR account before merge.
- **Comments**: only when WHY is non-obvious. No docstrings on trivial functions. No comments restating WHAT.

## Storage tiers

| Tier | Where | Purpose |
|---|---|---|
| Hot operational | Postgres on Neon | Voyages currently underway, latest signal, open positions, audit log shape |
| Cold archive | Parquet on Cloudflare R2 | Raw AIS, historical price archive, finalized voyages — immutable, partitioned by date |
| Analytical | DuckDB (in-process) | Joins parquet from R2 + Postgres state for backtests and ad-hoc queries |

## Key invariants

- **Audit log is append-only.** Never mutate.
- **Risk gate runs on every order before submission.** No exceptions. No bypass flag.
- **Backtest enforces T+1 execution lag** (signal computed at close → trade at next open).
- **All AIS timestamps stored as UTC.**
- **All money values stored as integer cents** to avoid float drift.

## External services (env vars in `.env.example`)

| Service | Env var | Purpose |
|---|---|---|
| AISStream.io | `AISSTREAM_API_KEY` | Live AIS WebSocket |
| Global Fishing Watch | `GFW_API_TOKEN` | Vessel identity lookup (free API tier; BigQuery research tier pending) |
| Google BigQuery (future) | `GOOGLE_APPLICATION_CREDENTIALS` | GFW research tier, once approved |
| Cloudflare R2 | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Parquet storage |
| Neon Postgres | `DATABASE_URL` | Operational DB (use `postgresql+psycopg://` — psycopg3 driver) |
| Interactive Brokers | `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` | Trading (TWS or IB Gateway) |
| Sentry | `SENTRY_DSN` | Error tracking |
| Discord | `DISCORD_WEBHOOK_URL` | Alert delivery |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `ALERT_PHONE` | Critical SMS |

## Data layout on disk

Everything under `data/` is gitignored.

```
data/
├── raw/
│   ├── gfw/
│   │   ├── anchorages/                  # named_anchorages_v*_*.csv
│   │   ├── voyages/                     # voyages_c4_pipe_v3_YYYYMM.csv (~30 day lag)
│   │   ├── sar_vessels/                 # sar_vessel_detections_*.csv
│   │   ├── sar_infrastructure/          # sar_fixed_infrastructure_*.csv
│   │   ├── distance_from_port/          # *.tiff raster grids
│   │   └── cvp/                         # Carrier Vessel Portal events
│   └── ais_live/                        # Phase 1a streamer output (date-partitioned parquet)
├── interim/
└── processed/
    ├── voyages/
    │   └── route=<name>/year=YYYY/month=MM/*.parquet   # TD3C-filtered voyages
    └── events/                                          # GFW Events API — ~3 day lag
        └── type=<kind>/year=YYYY/month=MM/*.parquet     # port_visit / gap / encounter / loitering
```

## GFW data layering — freshness tiers

Three sources of GFW-derived voyage/event data, used together:

| Tier | Source | Lag | Scope | When to use |
|---|---|---|---|---|
| Bulk historical | `data/raw/gfw/voyages/*.csv` → `data/processed/voyages/` | ~30 days | All vessels globally | Backtest over months/years |
| Near-real-time events | GFW `/v3/events` REST (our token) → `data/processed/events/` | ~3 days | Specific vessel_ids we query | Fill gap between last CSV release and today |
| Live per-message AIS | AISStream.io WebSocket → `data/raw/ais_live/` | Real-time | Filtered to VLCCs | Forward signal generation |

## How to add a new workspace package

1. Create `packages/<name>/pyproject.toml` and `packages/<name>/src/taquantgeo_<name>/__init__.py`
2. Add `taquantgeo-<name> = { workspace = true }` to root `[tool.uv.sources]`
3. Add `taquantgeo_<name>` to ruff's isort `known-first-party`
4. `uv sync` to wire it up

## Useful commands

```bash
uv sync                                  # install / update everything
uv run pytest                            # run all tests
uv run pytest -m "not integration"       # skip tests that need postgres/redis
uv run ruff format && uv run ruff check  # format + lint
uv run basedpyright                      # type check
uv run taq --help                        # the CLI
uv run taq ais stream --duration 60 --no-db --archive-dir /tmp/x  # live AIS smoke
uv run taq gfw list-routes               # inspect available freight routes
uv run taq gfw ingest-voyages --voyages-csv data/raw/gfw/voyages/<file>.csv  # extract one month
uv run taq gfw sample-events --vessel-id <id> --event-type port_visit        # probe events API
uv run taq gfw fetch-events --vessel-ids-file vids.txt --since 2026-01-01    # batched events
uv run taq gfw classify-vessels --voyages-dir data/processed/voyages         # build VLCC registry
uv run taq gfw compute-distances --voyages-dir data/processed/voyages         # sea-route distance cache
uv run taq gfw ingest-sar --since 2026-03-01 --until 2026-03-31                   # SAR dark-fleet candidate cross-reference
uv run taq signals compute-tightness --as-of 2026-03-15                           # compute TD3C tightness snapshot (stdout)
uv run taq signals compute-tightness --as-of 2026-03-15 --persist                 # also upsert to Postgres `signals`
uv run taq prices backfill --since 2017-01-01                                     # backfill default shipping-equity basket (FRO/DHT/INSW/EURN/TNK)
uv run taq prices update                                                          # incremental update to today per ticker
uv run taq prices show --ticker FRO --tail 10                                     # tail-10 diagnostic print for one ticker
uv run taq signals ic --since 2017-01-01 --out reports/ic_analysis.md             # walk-forward IC + fail-fast gate; exit 10 on no-edge or insufficient data
docker compose -f infra/docker-compose.yml up -d   # local postgres + redis
docker compose -f infra/docker-compose.yml down    # stop them
```
