# Phase 07 — IC analysis ⚠ FAIL-FAST GATE

## Metadata
- Effort: `max`
- Depends on phases: 04, 06
- Applies security-review: `no`
- Max phase runtime (minutes): 120
- External services: none (uses Postgres and parquet already populated)

## Mission
Before we spend weeks on a backtester, walk-forward cross-validation, IBKR
plumbing, and a frontend, we answer the one question that makes the whole
project worthless if the answer is "no": **does the tightness signal have
detectable edge against the shipping-equity basket at 1-, 2-, and 4-week
horizons?** Information coefficient (IC) + rank IC + t-stat + hit rate
over walk-forward windows gives us a defensible answer. If IC is flat
across all horizons, the harness halts here with a loud manual-setup-
required entry asking the user to re-examine the signal definition (phase
04) before continuing. No backtester, no deploy, no UI will be built on a
signal that has no edge — that work is literally zero value.

The gate triggers ONLY on "no edge anywhere". A mixed result (weak 1w,
strong 4w) is still a pass — backtester and horizon selection phase can
work with it. A clear fail is all-horizons flat.

## Orientation
- `.build/handoffs/04_handoff.md`, `05_handoff.md`, `06_handoff.md`
- `packages/signals/src/taquantgeo_signals/tightness.py`
- `packages/prices/src/taquantgeo_prices/models.py` — prices table
- `docs/adrs/0007-tightness-signal-definition.md`
- `docs/adrs/0002-gfw-voyages-as-historical-source.md` Gap 4
- `packages/ais/src/taquantgeo_ais/gfw/events.py` — quality bar

## Service preflight
- `DATABASE_URL` required (reads signals + prices). Same bring-up flow as
  phase 04.

## Acceptance criteria
- `packages/signals/src/taquantgeo_signals/compare.py` exists with:
  - `compute_ic(signal_series, return_series, method: Literal["pearson",
     "spearman"] = "spearman") -> ICResult` where
    `ICResult` has `ic`, `rank_ic`, `t_stat`, `hit_rate`, `n_obs`.
  - `walk_forward_ic(signals_df, prices_df, *, horizons_days: list[int]
     = [5, 10, 20], min_history_days: int = 180,
     step_days: int = 20) -> polars.DataFrame` returning per-window IC
    across all horizons. No look-ahead: IC for window ending at T is
    computed using only signals ≤ T and returns from T to T+horizon.
  - `basket_return(prices_df, *, tickers, weights=None, horizon_days)` —
    equal-weight by default; returns forward log-returns.
  - `ic_summary(wf_df) -> pandas.DataFrame` producing the summary table
    (horizon, mean IC, median IC, mean t-stat, mean hit rate, IR, n
    windows).
- CLI: `taq signals ic [--since YYYY-MM-DD] [--until YYYY-MM-DD]
  [--out reports/ic_analysis.md]` writes the report and prints the
  no-edge verdict at the end (exit code 0 on pass; exit code 10 on
  fail-fast trigger).
- Report file `reports/ic_analysis.md` contains:
  - Executive summary: pass or fail, with one-sentence verdict
  - Data summary: date range, N trading days, N signal observations,
    tickers in basket, basket return series description
  - IC table across horizons (markdown table)
  - ASCII sparkline (monospace, per-horizon) of rolling IC
  - Per-horizon scatter description (text, no image)
  - Regime breakdown (COVID Mar-May 2020, Russia Feb 2022+, Red Sea Nov
    2023+) — per-regime mean IC
  - Appendix: exact definitions + edge cases encountered (division by
    zero, missing tickers, signal gaps)
- **Fail-fast trigger**: if mean |IC| < 0.02 AND |t-stat| < 1.5 across
  ALL of {5d, 10d, 20d} horizons, AND across both Pearson and Spearman
  methods, the phase:
  1. Writes `reports/ic_analysis.md` with a **LOUD BANNER** at the top
     (all-caps, bordered) stating NO EDGE DETECTED.
  2. Appends manual-setup entry to `.build/manual_setup_required.md`:
     "Signal has no detectable edge at current definition. Re-examine
     Phase 04 tightness math (ADR 0007) or signal thresholds before
     proceeding to backtester. Diagnostic: `reports/ic_analysis.md`."
  3. Writes handoff `status: blocked` with the verdict and summary.
  4. Exits cleanly (code 10 from the CLI; driver detects blocked status).
- **Pass path**: if ANY horizon/method passes the threshold, phase
  completes with full report, handoff `status: completed`, and the
  harness proceeds to phase 08.
- Tests:
  - `test_compute_ic_pearson_matches_scipy_reference`
  - `test_compute_ic_spearman_matches_scipy_reference`
  - `test_walk_forward_ic_no_lookahead` — construct a signal that
    perfectly predicts forward returns; IC should be ~1. Shift signal by
    +1 day (into the future it doesn't see); IC should drop sharply.
  - `test_walk_forward_ic_handles_missing_days_gracefully` —
    signal/price calendars don't overlap perfectly (weekends, holidays);
    aligner must handle without blowing up
  - `test_ic_summary_formats_markdown`
  - `test_fail_fast_trigger_when_all_horizons_flat` — synthetic random
    signal produces no-edge verdict + correct exit code
  - `test_fail_fast_does_not_trigger_on_mixed_result` — strong 20d, weak
    5d passes
  - `test_regime_breakdown_slices_correctly`
- All quality gates green (the FAIL-FAST scenario is tested with
  synthetic data, not by triggering against real data — the real run
  is what determines whether the harness proceeds).

## File plan
- `packages/signals/src/taquantgeo_signals/compare.py` — new
- `packages/signals/tests/test_compare.py` — new
- `packages/signals/tests/fixtures/ic_synthetic_signal.parquet` — seeded
  random-but-deterministic synthetic signal for the no-edge test
- `packages/cli/src/taquantgeo_cli/signals.py` — add `ic` subcommand to
  the signals typer app
- `reports/ic_analysis.md` — **generated artifact**, NOT committed to
  git (reports/ is gitignored). The generated file is referenced by the
  handoff; the handoff itself is ephemeral (gitignored) too.
- `docs/adrs/0009-ic-fail-fast-gate.md` — NEW ADR: rationale for the
  fail-fast gate, threshold choices (0.02 mean IC, 1.5 t-stat),
  three-horizon coverage, and why we kill the whole harness on flat
- `docs/RESEARCH_LOG.md` — append real-run verdict with summary stats

## Non-goals
- Signal tuning — if this phase fails, it halts so the user can tune
  phase 04, not so this phase can search parameter space.
- Per-name IC (FRO vs DHT vs …) — basket-level only here. Per-name is a
  candidate entry for attribution work.
- Mean-variance portfolio construction — phase 08 territory.

## Quality gates
- Format + lint + typecheck clean
- ≥8 new tests, all green
- Pre-commit meta-review: scope gate met; run all 3 subagents. This phase
  is a **critical gate** — reviewer should be pedantic on edge cases
  (alignment, look-ahead, regime cuts)
- `Effort: max` → three-round review loop
- ADR 0009 committed

## Git workflow
1. Branch `feat/phase-07-ic-analysis`
2. Commits:
   - `feat(signals): walk-forward IC + fail-fast gate`
   - `test(signals): IC methods + look-ahead guard + regime slicing`
   - `feat(cli): taq signals ic + report generator`
   - `docs: ADR 0009 IC fail-fast gate`
3. PR, CI green, squash-merge
4. **Then** run the real IC analysis against real signals + real prices.
   If it fails, halt per acceptance criteria. Do NOT revert the PR —
   the tooling is still good; it's the signal that's the problem.

## Handoff
The verdict (pass or fail). If pass: summary table and sparkline excerpts
pasted inline. If fail: the loud banner reproduced verbatim, the
manual-setup entry, and a one-paragraph suggestion for what to try in a
future phase 04 revision.
