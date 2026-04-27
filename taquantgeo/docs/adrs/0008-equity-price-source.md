# ADR 0008: Equity price source

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean

## Context

The TD3C tightness signal (ADR 0007) has to be validated against a market
anchor before it can carry a live trading position. The direct anchor
— Baltic Exchange TD3C spot rate — is paywalled (USD 15k+/yr) and behind
an API that requires a commercial licence. For v0 we want a source that
is (a) free, (b) has daily bars going back to at least 2017 so we can
backtest over 8+ years, and (c) correlates strongly enough with TD3C
spot to serve as a usable proxy.

ADR 0002 Gap 4 identified five shipping equities that are pure-play
VLCC / large-tanker names with historically observed weekly-horizon
correlations around 0.70–0.80 vs TD3C spot:

| Ticker | Name | Exchange |
|---|---|---|
| FRO | Frontline plc | NYSE |
| DHT | DHT Holdings | NYSE |
| INSW | International Seaways | NYSE |
| EURN | Euronav (rolled into CMB.TECH during 2024) | NYSE |
| TNK | Teekay Tankers | NYSE |

EURN in particular is a known discontinuity — Euronav's tanker assets
were merged into CMB.TECH during 2024 and the standalone EURN ticker
gradually stopped trading. Data source must tolerate this without
crashing a daily job.

We also need a canonical storage representation — integer cents, per
CLAUDE.md's money invariant — and an ingest path that works for both
one-off backfill (seeding from 2017) and daily incremental updates.

## Decision

**Source**: yfinance (Yahoo Finance) for v0. Free, no API key, daily
OHLCV with split/dividend adjustments, reliable for mid-cap US-listed
tankers. The `yfinance.download(..., auto_adjust=False)` call gives us
both `Close` and `Adj Close` in the same request, which lets us pin
the adjustment semantics here rather than relying on an implicit
rewrite.

**Adjustment**: we persist **adjusted** prices (adjusted close in
`close_cents`; OHL scaled by the same adjustment ratio). Rationale:
the downstream consumer (phase 07 IC study, phase 08 backtest) compares
*returns*. Splits and dividends inject discontinuities into unadjusted
series that would mask real signal. Adjusting at the ingest boundary
means every consumer sees a return-comparable series without having to
re-adjust.

**Storage**: Postgres table `prices` with columns
`(ticker, as_of, open_cents, high_cents, low_cents, close_cents, volume)`,
all prices integer cents (CLAUDE.md invariant), volume `bigint`. Unique
index on `(ticker, as_of)` for idempotent ON-CONFLICT upserts. Schema
lives in `packages/prices/src/taquantgeo_prices/models.py` and the
alembic 0003 migration is its DDL contract.

**Delisting / empty-frame behaviour**: if yfinance returns an empty
frame for a ticker (delisted, rate-limited, unknown symbol), we log a
WARN and return an empty polars DataFrame. The caller upserts zero
rows and continues. The daily job must survive a single ticker
dropping out of the basket — partial baskets are still usable for IC
work.

**Ticker basket** is pinned as `DEFAULT_TICKERS` in
`taquantgeo_prices.yfinance_client`. Altering the basket requires a
code change (which means a PR and a test update), not a config flip,
to prevent accidental silent basket changes mid-backtest.

## Consequences

**Positive**
- Zero-cost data source unblocks phase 07 (IC) and phase 08 (backtest)
  immediately.
- Adjusted-at-ingest means every downstream consumer is return-comparable
  without re-running the adjustment.
- Empty-frame tolerance means EURN's 2024 rollover does not break the
  daily job; we simply stop getting EURN bars from its delisting date
  forward.
- Integer cents everywhere — FP drift on money math is impossible by
  construction.
- Upsert idempotency means the scheduler (phase 11) can retry a failed
  daily fetch without creating duplicate rows or needing a guard query.

**Negative**
- yfinance is unofficial. Yahoo has broken the public data endpoint
  several times in the past decade; an outage could pause our daily
  update. Mitigations: (a) we log WARN on empty frames, (b) the data
  is static once fetched (backtests don't depend on today's bar being
  present), (c) Polygon is a candidate backup (see below).
- Yahoo-adjusted prices are not consensus — different adjustment
  vendors round dividends slightly differently. Absolute returns match
  across vendors to ~5 basis points, but backtest P&L will shift by
  that margin if we later swap sources. Fine for v0; to re-validate
  against a different vendor we would re-ingest from scratch.
- "Adjusted close" is actually adjusted for dividends too, which
  inflates total-return prices slightly above pure split-adjusted
  price-return prices. Acceptable because the signal study is
  return-based and dividend reinvestment is the textbook right answer
  for total-return backtests.
- EURN's rollover into CMB.TECH is not automatically followed. Once
  standalone EURN delists entirely, the basket effectively drops to
  four names. A future phase can add `CMB.TECH` (listed on Euronext
  Brussels, ticker `CMB.BR` on yfinance) if we want to follow the
  merged entity — but that's a distinct instrument and would need IC
  re-validation.

## Alternatives considered

- **Polygon.io** (paid, $199/mo for starter). Higher-quality data with
  an official REST API and official historical tick feeds. Rejected for
  v0 because yfinance meets the correlation bar for free; Polygon is on
  the candidate list for phase 12+ if Yahoo becomes unreliable. The
  config already has a `POLYGON_API_KEY` placeholder (see
  `taquantgeo_core.config.Settings.polygon_api_key`) so the switch is
  drop-in.
- **Alpha Vantage** (free tier capped at 5 req/min, 500/day). Too
  aggressive a rate limit for an 8-year × 5-ticker backfill without
  complicated retry/sleep logic. yfinance's effective rate limit is
  much looser.
- **Direct from exchange / NYSE tape**. Requires a commercial
  subscription; out of scope for v0.
- **Baltic Exchange TD3C spot directly**. Paywalled at ~USD 15k/yr;
  explicitly out of scope per ADR 0002 Gap 4. Remains on the candidate
  list if we commit to a paid feed.
- **TD3C FFA (freight-futures) prices from an FFA broker**. Also
  paywalled. Separate candidate entry; would need a counterparty
  relationship even to get a data feed.
- **Store unadjusted prices and adjust at read time**. Rejected — every
  consumer would have to re-implement the adjustment, and splits don't
  propagate backwards through a cached read without re-fetching the
  full series. Adjust-at-ingest is simpler and matches the
  "every-consumer-sees-return-comparable-bars" contract the backtest
  relies on.
- **Float prices everywhere**. Rejected per CLAUDE.md invariant. Integer
  cents also plays better with the `audit_log` JSON payload in the trade
  module (no float-serialisation ambiguity).
