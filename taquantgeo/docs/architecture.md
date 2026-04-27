# Architecture

## North-star description

TaQuantGeo ingests global AIS broadcasts, classifies VLCC voyages on the TD3C route (Persian Gulf → China), computes a daily *tightness* signal (forward demand ÷ forward supply), compares to market-implied tightness via shipping equity prices, and generates trades through Interactive Brokers when the gap exceeds a calibrated threshold.

## Data flow

```
                         ┌──────────────────┐
                         │ AISStream.io WS  │  live AIS, free
                         └────────┬─────────┘
                                  │
                                  ▼
┌─────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  GFW BigQuery   │─────▶│  ais.streamer +  │─────▶│  R2 (parquet,    │
│  historical     │      │  ais.parser      │      │  partitioned by  │
└─────────────────┘      └────────┬─────────┘      │  date+vessel)    │
                                  │                └────────┬─────────┘
                                  ▼                         │
                         ┌──────────────────┐               │
                         │ Postgres (live   │               │
                         │ vessel state)    │               │
                         └────────┬─────────┘               │
                                  │                         │
                                  ▼                         ▼
                         ┌──────────────────────────────────────┐
                         │ voyages.classifier (DuckDB job)      │
                         │ — loaded vs ballast                  │
                         │ — voyage start/end                   │
                         │ — ton-mile remaining                 │
                         └────────────────┬─────────────────────┘
                                          │
                                          ▼
                         ┌──────────────────────────────────────┐
                         │ signals.tightness (daily)            │
                         │   forward demand ÷ forward supply    │
                         └────────────────┬─────────────────────┘
                                          │
            ┌─────────────────────────────┼────────────────────────────┐
            │                             │                            │
            ▼                             ▼                            ▼
   ┌──────────────────┐          ┌──────────────────┐         ┌──────────────────┐
   │ prices.equities  │─────────▶│ signals.compare  │────────▶│ alerts (Discord) │
   │ (yfinance)       │          │ — IC, spread     │         │ + audit          │
   └──────────────────┘          └────────┬─────────┘         └──────────────────┘
                                          │
                                          ▼
                                ┌──────────────────┐
                                │ trade.risk gate  │  pre-trade checks
                                └────────┬─────────┘
                                          │ (passes)
                                          ▼
                                ┌──────────────────┐
                                │ trade.ibkr       │  ib_insync → IBKR
                                └────────┬─────────┘
                                          │
                                          ▼
                                ┌──────────────────┐
                                │ trade.audit      │  append-only log
                                └──────────────────┘
```

## Storage tiers

| Tier | Where | Contents |
|---|---|---|
| Live operational | Postgres on Neon | Active voyages, latest signal value, open positions, latest reconciliation |
| Cold archive | Parquet on Cloudflare R2 | Raw AIS messages (partitioned by date), historical equity prices, finalized voyages |
| Analytical | DuckDB (in-process) | Joins parquet + Postgres; serves backtests and ad-hoc research |

Why this split: 80% of compute is analytical scans (voyage classification, signal compute, backtest). DuckDB on parquet is ~100x faster than Postgres for those workloads. Live operational state is small (~1GB) and benefits from Postgres ACID + ecosystem. Three-tier lakehouse pattern.

## Hosting

| Component | Where | Why |
|---|---|---|
| Next.js dashboard | Vercel | Native Next.js, edge CDN, free tier |
| FastAPI + jobs + AIS streamer | Hetzner CX32 (€6/mo) | Long-running WebSocket, cheap compute |
| Postgres | Neon | Serverless, branching for dev/prod, free tier |
| Object storage | Cloudflare R2 | No egress fees vs S3 |
| Errors | Sentry | Free tier covers indie usage |
| Uptime | Better Stack | Free tier 10 monitors |

## Module ownership

| Module | Owns | Boundary |
|---|---|---|
| `core` | Config (`pydantic-settings`), DB session, shared Pydantic models, time utilities | No I/O beyond DB |
| `ais` | Live WS client, parser, archiver, geo polygons, voyage state machine | Does NOT compute trading signals |
| `signals` | Tightness signal, baselines, signal-vs-price comparison, IC | Does NOT issue trades |
| `backtest` | Custom pandas/polars backtester, walk-forward CV, reports | Pure functions over historical data |
| `trade` | IBKR client, **risk gate**, reconciliation, audit log | **Only** module allowed to issue real orders |
| `api` | FastAPI routes serving the dashboard | Read-only against operational state |
| `jobs` | APScheduler-driven daily/hourly pipelines | Glue only — orchestrates the others |
| `cli` | `taq` command-line tool | Operator UX for ops + research |

## Non-negotiables

1. **Audit log is append-only.** Every order, fill, and cancel is recorded. Never mutated.
2. **Risk gate runs on every order.** No bypass. Failures block the trade.
3. **Backtest enforces T+1 execution lag.** Signal at close → trade at next open.
4. **Reconciliation runs daily.** Diff between expected (our system) and actual (IBKR) positions; alert on mismatch.
5. **Kill switch (`KILL_SWITCH=true` in env)** blocks all new orders without restart.
6. **Snapshot tests on signal definition.** Frozen historical AIS → asserted tightness number.

## Known limitations (called out so reviewers can't say we missed them)

- **AIS gaps over open ocean** — VLCCs lose signal mid-Indian-Ocean. Voyage classifier interpolates on heading + speed, marks gaps as `estimated`.
- **Sanctioned dark fleet** — Iranian crude often goes dark. Tightness signal is biased downward for Iranian flows. Document, don't try to model in v0.
- **Co-loading and STS transfers** — Edge case. Document, ignore in v0.
- **TD3C index vs actual deals** — We trade equity proxies, not FFAs, in v0. Equity proxies price the same fundamentals but with extra noise from market beta and company-specific news. Acceptable for v0.
- **Latency from signal to trade** — 12-24h minimum. Hard-enforced in backtest.
- **Liquidity in shipping equities** — FRO/DHT trade fine; EURN/INSW have wider spreads. Position sizing must respect ADV.
- **Survivorship bias in equity history** — yfinance has it. We track 5-7 names manually and document if any delist.

## Phase status

See [README.md](../README.md#build-plan) for the live phase tracker.
