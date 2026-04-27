# ADR 0001: Initial stack decisions

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell

## Context

Bootstrapping TaQuantGeo from zero. Multiple defensible stack choices for every layer. Need to pin defaults and document why so they aren't relitigated every PR.

## Decisions

### Language: Python 3.12

The quant + geo ecosystem (pyais, geopandas, vectorbt, statsmodels, ib_insync) is irreplaceable. AIS message rate after VLCC + bbox filter is ~10–50 msg/sec — well under Python's headroom. If the streamer ever bottlenecks, it can be swapped to Rust as one isolated microservice. Don't pay the polyglot tax now.

### Storage: Postgres + Parquet + DuckDB lakehouse

Workload is 80% analytical (voyage classification, signal compute, backtest), 20% live operational. Parquet+DuckDB queries are 10–100x faster than Postgres for analytics, and DuckDB has zero ops cost. Postgres holds only live operational state (~1GB). Parquet on R2 is the cold archive. Three-tier split, each tier doing what it's best at.

### AIS data: AISStream.io (live) + Global Fishing Watch BigQuery (historical)

Spire (~$500/mo) ruled out by user. AISStream.io is free for live. **GFW BigQuery** is the underused unlock — free historical AIS going back to 2012, queryable from Python. Lets us backtest immediately rather than waiting 6 months to self-collect. Datalastic (~$200 one-time) is the fallback if GFW VLCC coverage is thin.

### Backtester: custom pandas/polars first, vectorbt for sweeps later

Strategy is weekly-rebalance with 2–4 week holds. vectorbt is overkill (built for HF parameter sweeps). A 50-line custom backtester in pandas is more transparent and easier to debug. vectorbt re-introduced later for parameter exploration. **Nautilus Trader** on the roadmap because it shares code between backtest and live execution — same code path = fewer "works in backtest, broken in live" bugs.

### Trading: Interactive Brokers via ib_insync

Only realistic API for shipping equities (FRO/DHT/INSW/EURN/TNK). Alpaca is US-only and has thinner instrument coverage. Paper-trade first via port 7497, then switch to live (7496) only after the risk gate, reconciliation, and audit log pass dry runs.

### Frontend: Next.js 15 + Tailwind v4 + shadcn/ui + DeckGL on Maplibre + TradingView Lightweight Charts

User explicitly required "awesome UX". Streamlit was rejected — does not look modern. shadcn gives Linear-grade components. DeckGL renders thousands of vessel positions at 60fps. Maplibre is OSS Mapbox-compatible (no Mapbox $$$). TradingView's free Lightweight Charts is the standard finance look (crosshair, drawing, zoom out of the box).

### Hosting: Vercel + Hetzner CX32 + Neon + Cloudflare R2

- **Vercel** for the Next.js frontend (native, edge CDN, free tier).
- **Hetzner CX32** (€6/mo) for backend, jobs, and the long-running AIS WebSocket. Cheaper than Fly.io for always-on workloads.
- **Neon** for Postgres (serverless, branching for dev/prod, free tier).
- **Cloudflare R2** for parquet (no egress fees).
- **Sentry** + **Better Stack** for errors and uptime (both free tier).

Total run cost: ~€6/mo plus $10/mo IBKR market data, until volume forces a tier upgrade.

### Type checker: basedpyright (strict)

Stricter than mypy, faster, better error messages. Strict mode from day one — adding type discipline retroactively is much harder than starting with it.

### Package manager: uv (workspace mode)

Fastest installer, lockfile by default, native workspace support. Each `packages/<name>/` is an independent buildable package; root is a virtual workspace declaring members.

## Consequences

**Positive**
- Modern, defensible stack. Easy to attract collaborators familiar with these tools.
- Cost stays under €20/mo until trading volume forces upgrades.
- Backtest queries are fast (DuckDB) so iteration loop on signal definition is short.

**Negative**
- Multi-tier storage means extra mental overhead vs "everything in Postgres".
- Vercel + Hetzner means two deploy targets to maintain.
- Strict type checking will slow some PRs while ramp-up happens.

## Alternatives considered

See the planning conversation that produced this ADR. Major alternatives rejected:
- Rust for the AIS streamer (premature optimization)
- TimescaleDB for everything (loses to DuckDB on analytics)
- vectorbt as primary backtester (overkill for slow strategy)
- Streamlit for UI (does not meet "awesome UX" bar)
- Palantir Foundry/AIP (external dep with no clear ROI for v0)
