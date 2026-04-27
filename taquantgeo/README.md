# TaQuantGeo

Quant geospatial trading platform. v1 vertical: **VLCC freight tightness signal** on the TD3C route (Persian Gulf → China), traded via shipping equities (FRO, DHT, INSW, EURN, TNK).

## Status

Phase 0 (foundation). See [docs/architecture.md](docs/architecture.md) for the system design and the build plan below.

## Quickstart

### Prerequisites

- Python 3.12 — `uv` will install it for you
- [uv](https://github.com/astral-sh/uv) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker Desktop (with WSL2 integration enabled if on Windows)
- Optional: [gh CLI](https://cli.github.com/) for repo management

### Setup

```bash
git clone https://github.com/sn12-dev/taquantgeo.git
cd taquantgeo
uv sync                                            # installs all workspace packages + dev tools
cp .env.example .env                               # fill in your credentials
docker compose -f infra/docker-compose.yml up -d   # local postgres + redis
uv run pre-commit install                          # commit hooks
uv run pre-commit install --hook-type commit-msg   # conventional-commit linting
uv run pytest                                      # should pass on the empty scaffold
```

### CLI

```bash
uv run taq --help
```

## Restoring on a new machine

After cloning, you get all code, the committed `.env` (rotate keys — see note), the 76 KB of `data/processed/` parquets needed to skip re-derivation, and the phase handoff docs under `.build/handoffs/`. What you need to re-download manually is the 8.1 GB of raw GFW bulk data.

### Clone to the same absolute path

Claude Code stores per-project memory at `~/.claude/projects/-<absolute-path-with-dashes>/`. This project's memory directory is `~/.claude/projects/-home-seanp-taquantgeo/`, encoded from `/home/seanp/taquantgeo`. Clone on the new machine so the working directory ends up at the identical absolute path — same username (`seanp`), same location — or Claude Code will start with an empty per-project memory for this repo.

### Fast setup

```bash
git clone https://github.com/sn12-dev/taquantgeo.git
cd taquantgeo
uv sync
docker compose -f infra/docker-compose.yml up -d
uv run pre-commit install && uv run pre-commit install --hook-type commit-msg
uv run pytest -m "not integration and not live"       # sanity check on the scaffold
```

### Rotate `.env` secrets immediately

`.env` is committed to the repo as a one-time pre-wipe backup. Rotate every key listed below on the new machine and update `.env` in place (gitignored once rotated is optional — for now it is tracked):

- `AISSTREAM_API_KEY` — regenerate at aisstream.io
- `GFW_API_TOKEN` — regenerate in the GFW portal
- `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` — regenerate in Cloudflare
- `DATABASE_URL` — current value points at the local docker Postgres (`postgresql+psycopg://taq:taq@localhost:5432/taquantgeo`), no rotation needed. If you've migrated to Neon, rotate the Neon role password and update this URL.
- `IBKR_*` — no secret, but re-verify host/port
- `SENTRY_DSN` — regenerate the DSN
- `DISCORD_WEBHOOK_URL` — regenerate the webhook
- `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` — rotate in Twilio console
- `GOOGLE_APPLICATION_CREDENTIALS` — the referenced file (`secrets/gcp-taquantgeo.json`) is committed. **Disable the key in GCP IAM** (IAM → Service Accounts → select account → Keys → delete), generate a new JSON key, and overwrite `secrets/gcp-taquantgeo.json` on the new machine. Used for the GFW BigQuery research tier.

### Raw data to re-download (not in git — 8.1 GB)

Place every file under the exact path shown. All are GFW bulk releases from the [GFW Data Portal](https://globalfishingwatch.org/data-download/), except the distance-from-port raster (same portal, 'Distance from port' dataset).

| Path | Size | Files |
|---|---|---|
| `data/raw/gfw/anchorages/` | 80 MB | `named_anchorages_v1_20181108.csv`, `named_anchorages_v1_20191205.csv`, `named_anchorages_v2_20201104.csv`, `named_anchorages_v2_20221206.csv`, `named_anchorages_v2_pipe_v3_202601.csv` |
| `data/raw/gfw/voyages/` | 732 MB | `voyages_c4_pipe_v3_202602.csv`, `voyages_c4_pipe_v3_202603.csv`, `voyages_c4_pipe_v3_20260416.csv` |
| `data/raw/gfw/sar_vessels/` | 20 MB | `sar_vessel_detections_pipev4_202603.csv`, `sar_vessel_detections_pipev4_20260417.csv` |
| `data/raw/gfw/sar_infrastructure/` | 2.4 GB | `sar_fixed_infrastructure_202510.csv`, `sar_fixed_infrastructure_202511.csv`, `sar_fixed_infrastructure_202512.csv` |
| `data/raw/gfw/distance_from_port/` | 4.9 GB | `distance-from-port-v1.tiff`, `distance-from-port-v20201104.tiff` |
| `data/raw/gfw/cvp/` | 12 KB | `CVP_ports_202501.csv`, `CVP_ports_20250221.csv` |

### Rehydrate the local Postgres DB

`DATABASE_URL` in `.env` points at the docker-compose Postgres (not Neon). The tables that get data locally are:

- `prices` — equity bars for FRO/DHT/INSW/EURN/TNK since 2017, ~9.3k rows
- `signals` — daily TD3C tightness snapshots (1 row per `compute-tightness --persist` call)
- `vessels` — VLCC registry (usually empty until `taq gfw classify-vessels` pushes to Postgres)
- `alembic_version` — migration bookkeeping

All of it is **cheap to regenerate from public sources**, so carrying a dump is optional. Two paths:

#### Path A — regenerate from scratch (recommended)

```bash
docker compose -f infra/docker-compose.yml up -d postgres
uv run alembic -c alembic.ini upgrade head            # schema
uv run taq prices backfill --since 2017-01-01         # ~9.3k rows from yfinance (~2 min)
uv run taq signals compute-tightness --as-of <YYYY-MM-DD> --persist   # re-derive signal snapshot(s)
```

Caveat: yfinance occasionally adjusts historical prices for splits/dividends, so row values may differ by a few basis points from a restored dump. Fine while the system is pre-production.

#### Path B — restore from a pg_dump file (only if you carried one)

Before wiping, on the old machine while the container is up:

```bash
docker exec taq-postgres pg_dump -U taq -d taquantgeo -Fc -f /tmp/taquantgeo.dump
docker cp taq-postgres:/tmp/taquantgeo.dump ./taquantgeo.dump
```

The resulting `taquantgeo.dump` (~160 KB) is gitignored — move it to the new machine via USB / R2 / scp. Then:

```bash
docker compose -f infra/docker-compose.yml up -d postgres
docker cp ./taquantgeo.dump taq-postgres:/tmp/taquantgeo.dump
docker exec taq-postgres pg_restore -U taq -d taquantgeo --clean --if-exists /tmp/taquantgeo.dump
```

Redis is a cache/queue — no dump or rebuild step needed; it re-populates itself on first job run.

### Regenerating `data/processed/` (optional — committed copy is in the repo)

The four parquets under `data/processed/` (`vessel_registry.parquet`, `distance_cache.parquet`, `dark_fleet_candidates.parquet`, and `voyages/route=td3c/.../*.parquet`) are derived artifacts. After the raw data is restored, rebuild them with:

```bash
uv run taq gfw ingest-voyages --voyages-csv data/raw/gfw/voyages/voyages_c4_pipe_v3_20260416.csv
uv run taq gfw classify-vessels --voyages-dir data/processed/voyages
uv run taq gfw compute-distances --voyages-dir data/processed/voyages
uv run taq gfw ingest-sar --since 2026-03-01 --until 2026-03-31
```

Only needed if the committed versions are stale relative to newer GFW releases.

## Repo layout

| Path | Contents |
|---|---|
| `packages/core/` | Shared models, config, DB session |
| `packages/ais/` | AIS ingestion, parsing, voyage state machine |
| `packages/signals/` | Tightness signal, baselines, signal-vs-price |
| `packages/backtest/` | Custom backtester + reports |
| `packages/trade/` | IBKR + risk gate + reconciliation + audit log |
| `packages/api/` | FastAPI for the dashboard |
| `packages/jobs/` | Scheduled pipelines (APScheduler) |
| `packages/cli/` | `taq` command-line tool |
| `web/` | Next.js 15 dashboard (separate workspace) |
| `infra/` | Dockerfile, docker-compose, alembic migrations |
| `docs/` | Architecture, data model, runbook, ADRs, RESEARCH_LOG |
| `research/` | Notebooks (isolated; not imported by `packages/`) |

## Build plan

| Phase | Status | Days | Deliverable |
|---|---|---|---|
| 0 — Foundation | ✅ done | 1-2 | Repo scaffold, CI, docker-compose, docs |
| 1a — AIS live ingestion | ✅ done | 2-3 | AISStream.io → parquet, VLCC filter, Postgres vessel registry |
| 1b — AIS historical (GFW CSVs + Events API) | 🔄 in progress | 3-4 | Voyages/anchorages extractor ✅; Events API near-real-time ✅; vessel-class classifier ⏳; sea-route distance ⏳; SAR dark-fleet ⏳ |
| 2 — Voyage enrichment (ship type + ton-miles) | ⏳ | 2-3 | Wraps up as part of Phase 1b's remaining tasks |
| 3 — Tightness signal | ⏳ | 2-3 | Daily TD3C signal in Postgres |
| 4 — Market data + IC | ⏳ | 2-3 | Equity prices + signal-vs-return correlation |
| 5 — Backtest | ⏳ | 3-5 | Custom backtester, walk-forward CV, latency-aware |
| 6 — Frontend MVP | ⏳ | 5-7 | Next.js + shadcn + DeckGL + TradingView Charts |
| 7 — Risk + paper trading | ⏳ | 3-4 | IBKR paper, risk gate, recon, audit log |
| 8 — Live trading | ⏳ | 1 | Flip to live IBKR with small position size |
| 9 — Daily ops + alerts | ⏳ | 2-3 | APScheduler + Discord/SMS alerts |

**Note on Phase 2**: the original plan was "build a voyage classifier from raw AIS." GFW publishes C4 anchorage-to-anchorage voyages for all vessels directly (see [ADR 0002](docs/adrs/0002-gfw-voyages-as-historical-source.md)) and their REST Events API gives near-real-time events per vessel (see [ADR 0003](docs/adrs/0003-events-api-for-freshness.md)), so Phase 2 is now primarily *enrichment* (ship type, ton-miles) rather than classification from scratch — and its remaining work is tracked under Phase 1b's vessel-class and sea-route-distance tasks.

See [docs/architecture.md](docs/architecture.md) for the full design and [docs/adrs/](docs/adrs/) for decision records.

## License

MIT — see [LICENSE](LICENSE).
