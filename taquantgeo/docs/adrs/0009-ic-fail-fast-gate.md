# ADR 0009: IC fail-fast gate

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell

## Context

The build harness has a long tail of work scoped after Phase 07: a custom
backtester (Phase 08), walk-forward cross-validation (Phase 09), data-quality
monitors (Phase 10), scheduled jobs (Phase 11), alerting (Phase 12), broker
plumbing (Phases 13-16), the dashboard (Phases 17-22), production deploy
(Phases 23-26), and the v0 release (Phase 28). Every one of those phases
implicitly bets that the TD3C tightness signal carries detectable
predictive power against shipping-equity returns. **If that assumption is
false, the entire downstream investment — weeks of engineering, plus real
money once trading starts — has zero value.**

Phase 07's mission, narrowly: prove (or disprove) that bet *before* the
downstream investment, using the cheapest test available. The cheapest test
is the information coefficient (IC) — a regression of the signal against
forward returns over walk-forward windows. Two correlation methods
(Pearson and Spearman) and three horizons (1-, 2-, 4-week) cover the
plausible signal shapes (linear vs monotone-rank; short vs long horizon).
If every (horizon × method) cell shows |IC| ≈ 0 with t-stat ≈ 0, we have
empirical evidence the signal is flat, and the harness should halt rather
than build everything that follows on top of nothing.

The decision in this ADR is *what counts as flat enough to halt*.

## Decision

A "fail-fast" gate fires when **all** of the following hold simultaneously:

1. mean |IC| < `IC_FAIL_FAST_MEAN_ABS = 0.02` across every horizon in
   `FAIL_FAST_HORIZONS_DAYS = (5, 10, 20)` AND
2. mean |t-stat| < `IC_FAIL_FAST_T_STAT = 1.5` across the same set AND
3. condition (1) and (2) hold for both methods in
   `FAIL_FAST_METHODS = ("pearson", "spearman")` AND
4. Each (horizon × method) cell has at least
   `MIN_VIABLE_WINDOWS = 3` walk-forward windows of evidence.

If any cell clears either threshold (and is viable), the verdict is `PASS`
— we proceed to Phase 08, and the question of *which* slice to actually
trade is left to the backtester and the walk-forward CV (Phase 09). If the
data is too thin to compute the gate (some cells missing or under-sampled
AND no covered cell shows edge), the verdict is `BLOCKED_INSUFFICIENT_DATA`
— we halt, the user backfills prices and historical signals, and re-runs.
Only when every gate cell is fully covered AND flat does the verdict become
`FAIL_NO_EDGE`.

### Threshold rationale

**`IC_FAIL_FAST_MEAN_ABS = 0.02`.** Spearman ICs in the 0.03 - 0.05 range
are standard for "weak but tradable" quant signals (cf. WorldQuant's
Alpha 101 benchmark). Below 0.02 the signal-to-noise ratio is too low to
overcome trade frictions in any reasonable backtest: a $500 K notional
TD3C-equity proxy book trading 1-2× per month against a 0.015 IC has
expected per-trade alpha well below typical commission + slippage.
Setting the threshold at 0.02 is permissive enough that a real-but-weak
signal passes, strict enough that pure noise fails.

**`IC_FAIL_FAST_T_STAT = 1.5`.** The conventional 95%-significance cutoff
is 1.96 (one-sided), 2.58 (two-sided 99%). Choosing 1.5 is deliberately
permissive: the gate's purpose is to halt only on truly flat IC, not on
borderline-significant IC. A signal with mean |t-stat| = 1.7 but mean
|IC| = 0.018 still passes (clears the t-stat threshold even though the
IC threshold isn't quite met) — and Phase 08's backtester or Phase 09's
walk-forward CV will get to make the deeper call. Setting t-stat too high
(e.g. 2.0) would risk killing the harness on a real-but-noisy signal.

**Three horizons (5, 10, 20 trading days).** TD3C cycle times are 30-45
days port-to-port; the signal's predictive value plausibly compounds over
weeks rather than days. Three horizons cover (a) "very short term: does
the equity book react within a week?", (b) "monthly: typical institutional
rebalance cadence?", (c) "quarterly-ish: full TD3C cycle horizon?". A
signal that is flat across all three is unlikely to have power at any
intermediate horizon — flatness is a strong null.

**Both methods (Pearson + Spearman).** Pearson catches linear effects;
Spearman catches monotone non-linear effects. A signal could be flat on
Pearson but non-zero on Spearman (e.g. a regime-switching signal that
flips sign in extreme regimes). Requiring both to be flat is a stronger
null than either alone — a signal with a single non-zero method passes,
which we want.

**`MIN_VIABLE_WINDOWS = 3`.** Two windows isn't statistical evidence;
three is the minimum where the variance estimator across windows
(used for `IR = mean / std`) has a meaningful denominator. A blocked-on-
insufficient-data verdict is preferred over a no-edge verdict computed on
two windows — the "no edge" claim should be statistically defensible, not
just a glance at small-N noise.

## Consequences

**Positive**

- Cheapest possible decision gate before the bulk of the downstream
  investment. Walk-forward IC takes seconds to compute against real data.
- Prevents the harness from building 20+ phases on a signal that has no
  edge. The mistake is caught in Phase 07 instead of Phase 28.
- Three-tier verdict (`PASS` / `FAIL_NO_EDGE` / `BLOCKED_INSUFFICIENT_DATA`)
  separates "signal is flat" from "we don't have enough data to tell" —
  the user-action prescription differs (revisit ADR 0007 vs backfill
  prices), and conflating them would waste a debugging session.
- All thresholds are module-scope `Final` constants. A future ADR can
  amend them in one place; tests pin the current values so the change is
  loud.

**Negative**

- **The gate is a one-shot test, not a sweep.** A signal that almost
  passes today might pass with a small tweak to ADR 0007's math — but
  Phase 07 explicitly does NOT search parameter space. If the signal
  fails, the user re-thinks the math (Phase 04) before continuing, NOT
  retunes thresholds here. (See "Non-goals" in the phase contract.)
- **Threshold values are partly judgment.** 0.02 mean |IC| has no
  theoretical bedrock — it's calibrated to "weak but tradable" empirical
  practice. A future amendment with a labelled IC distribution from
  comparable freight-quant signals could refine this; we don't have that
  reference set yet.
- **Walk-forward windows are correlated.** With `step_days = 20` and
  `min_history_days = 180`, successive windows share 160 of 180 days of
  data. The mean-IC estimator is biased toward the overall-sample IC;
  the IR (mean / std across windows) is less informative than it would
  be for non-overlapping windows. We accept this — the alternative
  (non-overlapping windows) yields too few decision dates over a 4-year
  history.
- **The `BLOCKED_INSUFFICIENT_DATA` verdict can hide a flat signal.** If
  the user backfills more data and the verdict becomes `FAIL_NO_EDGE`,
  we've spent the backfill effort to learn the signal is dead. The
  reverse — backfilling and finding edge — is the better outcome but is
  not guaranteed. This is an inherent tradeoff with the cheap-gate
  approach; we tolerate the wasted backfill in the worst case.
- **The gate runs against persisted signals, not freshly recomputed
  ones.** The CLI reads `signals.tightness` from Postgres; if the
  persisted set was computed under an older ADR-0007 math, the gate
  evaluates the older math. Re-running `taq signals compute-tightness`
  over the relevant date range keeps the persisted set current; phase 11
  will eventually schedule that automatically.

## Alternatives considered

- **No gate.** Build everything and find out at Phase 28 whether the
  signal works. Rejected: this is exactly the failure mode the gate
  protects against — at Phase 28 the sunk cost is so high that the
  team-level pressure is to ship anyway. Front-loading the falsification
  test is the whole point.
- **Stricter thresholds (mean |IC| ≥ 0.05).** Rejected: would kill the
  harness on a marginal-but-tradable signal. The backtester and
  walk-forward CV are the right place to make the marginal call, not
  here.
- **Looser thresholds (mean |IC| ≥ 0.005).** Rejected: would never fire
  on plausible random noise — the gate would be a no-op. With 4 years of
  daily signals and ~40 windows, mean |IC| under the null is ~0.01-0.02
  by chance.
- **Single-horizon gate (just 20-day Spearman).** Rejected: a signal
  that's strong at 5d and weak at 20d would fail the gate by accident.
  Multi-horizon coverage means a signal needs to be flat *everywhere*
  before we halt.
- **Out-of-sample only IC (no in-sample).** Rejected as out-of-scope —
  proper out-of-sample CV is Phase 09's job. Phase 07's job is the
  cheapest possible falsification, not the proper validation.
- **Bootstrap confidence intervals on IC.** Rejected as out-of-scope —
  the t-stat threshold approximates the same statistical test at a
  fraction of the compute cost. Phase 09 may add bootstrap if needed.
- **Use simple returns instead of log returns.** Rejected: log returns
  compound additively across horizons (so 4× weekly IC has the same
  scaling as 1× monthly IC), which makes cross-horizon comparison
  cleaner. Difference vs simple returns is sub-percent at the IC scale
  we measure.
- **Use Postgres-side aggregations.** Rejected: the IC calculation is
  fast in-process (pure pandas/numpy/scipy); the additional complexity
  of writing it as SQL would obscure the math without speed gains.
