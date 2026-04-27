# TD3C tightness signal — IC analysis (2026-04-21)

## Executive summary

[BLOCKED] **Verdict**: INSUFFICIENT DATA. Not enough signal x price history to produce 3+ walk-forward windows across every horizon. Backfill prices and historical signals, then re-run.

```
================================================================
==                                                            ==
==        INSUFFICIENT DATA — HARNESS BLOCKED                 ==
==                                                            ==
==  Not enough overlap between signal history and price       ==
==  history to compute IC across the required horizons.       ==
==                                                            ==
==  Backfill prices (taq prices backfill) and / or compute    ==
==  historical signals (taq signals compute-tightness over    ==
==  a date range) before re-running the IC analysis.          ==
==                                                            ==
================================================================

```

## Data summary

- Signal observations: **0** (range: None → None)
- Price observations: **9348** (range: 2017-01-03 → 2026-04-21)
- Basket tickers: DHT, FRO, INSW, TNK
- Basket weighting: equal-weight unless overridden


## IC summary

(no walk-forward windows produced)


## Rolling IC sparkline

(no walk-forward windows produced)


## Per-horizon scatter (textual)

Each row in the IC summary is a per-(horizon, method) aggregate across walk-forward windows. The signal-vs-return scatter for any single window is roughly: x = signal at as_of=t, y = forward log-return [t, t+h]; correlation is the IC value reported above. We omit a rendered scatter image because the report is consumed as plain markdown by the build harness; phase 19's dashboard will surface the live scatter.


## Regime breakdown

(no windows fell in any regime)


## Appendix — definitions & edge cases

- IC = Pearson correlation of (signal, forward log-return).
- Rank IC = Spearman correlation of (signal, forward log-return).
- t-stat = ic x sqrt(n - 2) / sqrt(1 - ic²); clamped to a finite value when |ic| ≈ 1.
- Hit rate = fraction of observations where sign(signal) == sign(forward return). Zero-sign pairs count as misses.
- Forward return = log(close[t + h] / close[t]) where h is the trading-day horizon (h-th trading day after t, NOT calendar h).
- Walk-forward windows: span=180 days, step=20 days, horizons=[5, 10, 20].
- Look-ahead-free: at decision date T only signals with as_of ≤ T are visible; forward returns require t + h ≤ max(price.as_of).
- Fail-fast trigger: mean |IC| < 0.02 AND mean |t-stat| < 1.5 across **every** horizon AND **both** methods.
- Insufficient-data trigger: any (horizon, method) cell missing or n_windows < 3.
- Edge cases handled: signal/price calendar mismatch (as-of join), NaN bars (paired-NaN drop), constant-input correlation (returns NaN), zero-variance basket return on flat days (returns NaN).
