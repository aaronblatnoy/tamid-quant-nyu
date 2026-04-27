# Phase 06 — Equity price extractor

## Metadata
- Effort: `standard`
- Depends on phases: 00
- Applies security-review: `no`
- Max phase runtime (minutes): 90
- External services:
  - `yfinance` — Yahoo Finance, no API key required. Best-effort; Yahoo
    occasionally rate-limits or drops tickers. Document the fallback
    behavior.
  - `DATABASE_URL (required)` — persistence target (Postgres `prices`
    table). Already set locally.

## Mission
To know whether our tightness signal has predictive power (phase 07) and
to backtest trades (phase 08), we need historical equity prices for the
shipping-equity proxy basket: FRO, DHT, INSW, EURN, TNK. Baltic TD3C spot
rate is paywalled; equity proxies correlate ~70–80% at weekly horizon per
ADR 0002 Gap 4 and are free via yfinance. This phase implements
`packages/prices` with a yfinance client, stores daily OHLCV in Postgres
(integer cents, per the `no-floats-for-money` rule in CLAUDE.md), and
ships both a backfill CLI and an incremental-update CLI.

## Orientation
- `.build/handoffs/00_handoff.md`
- `docs/data_model.md` — `prices` table spec
- `docs/adrs/0002-gfw-voyages-as-historical-source.md` — Gap 4 rationale
- `packages/core/src/taquantgeo_core/{config,db,schemas}.py`
- `packages/signals/src/taquantgeo_signals/models.py` — integer-cents
  convention example (phase 04 work)
- `packages/ais/src/taquantgeo_ais/gfw/events.py` — quality bar

## Service preflight
- `DATABASE_URL` required; phase 04 already ensured local postgres is
  reachable. If not: same manual-setup path.
- yfinance is network-required for the real backfill. Tests must use a
  VCR cassette or mocked yfinance client — NO network in CI.

## Acceptance criteria
- NEW package at `packages/prices/` with `pyproject.toml`, added to
  workspace.
- `packages/prices/src/taquantgeo_prices/yfinance_client.py` with:
  - `fetch_ohlcv(ticker: str, start: date, end: date) -> polars.DataFrame`
  - Module docstring documents which yfinance series is used
    (**adjusted close** for close_cents — justify in the ADR) and the
    timezone normalization (Yahoo returns naive datetimes; we force UTC).
  - Defensive handling: empty frame for delisted tickers, logs a WARN;
    does NOT raise (daily job must not die because one ticker dropped).
- `packages/prices/src/taquantgeo_prices/models.py` — SQLAlchemy `Price`
  ORM matching `docs/data_model.md` (no `from __future__ import annotations`).
- `packages/prices/src/taquantgeo_prices/persistence.py` —
  `upsert_prices(session, rows) -> int` returns count inserted+updated;
  idempotent on `(ticker, as_of)`.
- Alembic migration `infra/alembic/versions/0003_prices_table.py`.
- CLI:
  - `taq prices backfill --since 2017-01-01 [--until YYYY-MM-DD]
    [--ticker FRO ...]` — default tickers: FRO DHT INSW EURN TNK
  - `taq prices update` — incremental; looks up MAX(as_of) per ticker,
    fetches from MAX(as_of)+1 to today
  - `taq prices show --ticker FRO [--tail 10]` — quick diagnostic
- Tests:
  - `test_fetch_ohlcv_parses_yfinance_frame` (mocked yfinance response)
  - `test_fetch_ohlcv_empty_on_delisting_logs_warn`
  - `test_ohlcv_converted_to_integer_cents` — no floats in inserted rows
  - `test_upsert_idempotent`
  - `test_backfill_cli_end_to_end` (mock yfinance)
  - `test_update_cli_resumes_from_latest_as_of`
  - `test_all_tickers_default_list_matches_docs`
- `uv run alembic upgrade head` clean.
- All quality gates green.

## File plan
- `packages/prices/pyproject.toml` — new; depends on yfinance, polars
- `packages/prices/src/taquantgeo_prices/__init__.py`
- `packages/prices/src/taquantgeo_prices/yfinance_client.py` — new
- `packages/prices/src/taquantgeo_prices/models.py` — new (no __future__)
- `packages/prices/src/taquantgeo_prices/persistence.py` — new
- `packages/prices/tests/test_yfinance_client.py`
- `packages/prices/tests/test_persistence.py`
- `packages/prices/tests/test_cli.py`
- `packages/cli/src/taquantgeo_cli/prices.py` — new typer subapp
- `packages/cli/src/taquantgeo_cli/main.py` — register
- `pyproject.toml` (root) — add `taquantgeo-prices` workspace member
- `infra/alembic/versions/0003_prices_table.py` — new migration
- `docs/adrs/0008-equity-price-source.md` — NEW ADR: why adjusted close,
  why yfinance vs Polygon for v0, fallback plan, delisting handling
- `CLAUDE.md` — register package, add new CLI rows
- `ruff.toml` (or inline) — add `taquantgeo_prices` to known-first-party

## Non-goals
- Real-time streaming prices — yfinance is daily; intraday is out of v0
  scope. Polygon is planned but a candidate, not this phase.
- Alternate data (ship-order book, freight futures) — candidate entries.
- Adjustments for splits/dividends beyond yfinance's built-in — we trust
  adjusted-close. Documented in the ADR.
- TD3C FFA ingest — paywalled per ADR 0002 Gap 4. Candidate entry already
  exists in `candidate_phases.md`.

## Quality gates
- Format + lint + typecheck clean
- ≥7 new tests
- `uv run alembic upgrade head` succeeds against local postgres
- Pre-commit meta-review full loop
- ADR 0008 committed

## Git workflow
1. Branch `feat/phase-06-equity-prices`
2. Commits:
   - `build(prices): new workspace package`
   - `feat(prices): yfinance client + price ORM + migration`
   - `feat(cli): taq prices backfill / update / show`
   - `test(prices): unit + CLI coverage with mocked yfinance`
   - `docs: ADR 0008 equity price source`
3. PR, CI green, squash-merge

## Handoff
Row counts after backfill against the actual 5-ticker basket from
2017-01-01 to today. Any delistings or rename events observed (e.g.,
EURN was renamed / rolled into CMB.TECH during 2024). Document
workarounds in RESEARCH_LOG.
